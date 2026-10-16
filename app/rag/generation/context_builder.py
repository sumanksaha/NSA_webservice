"""Context builder for RAG generation.

Follows the orchestration pattern from app/services/document_lifecycle.py
(DocumentSaveCoordinator): ContextBuilder coordinates the multi-step process
of selecting, sorting, truncating, and formatting chunks into a structured
context string with citation labels that the LLM can reference as [n].
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.rag.retrieval.result import RetrievedChunk
from app.shared.config import cfg

logger = logging.getLogger(__name__)

from app.rag.constants import TOKENS_PER_CHAR

_TOKENS_PER_CHAR = TOKENS_PER_CHAR
_CHUNK_OVERHEAD_CHARS = 120

#: P0-1 prompt packing order — lower number packs earlier (analysis §6 item 1:
#: Primary → Exceptions → Definitions → Penalties → Cross-References).
#:
#: Deliberately NOT the selector's ``_EVIDENCE_TYPE_PRIORITY``. The two answer
#: different questions: selection asks *which* provisions to keep (a definition
#: may legitimately outrank an exception on evidence value), while packing asks
#: *in what order* to place them in the prompt window. Pushing definitions after
#: the primary provision and its exceptions is the anti-definition-anchoring
#: effect §6 calls for; reusing the selection order would place definitions
#: second and reintroduce the anchoring this feature exists to prevent.
_PACK_ORDER: dict[str, int] = {
    "primary_provision": 0,
    "subsection": 1,
    "exception": 2,
    "definition": 3,
    "penalty_provision": 4,
    "cross_reference": 5,
    "authority": 6,
    "adjacent_section": 7,
    "duplicate": 8,
}
_PACK_ORDER_DEFAULT = 7

#: Index/navigation detection for context dropping (config
#: ``RAG_CONTEXT_DROP_INDEX_CHUNKS``, default on).
#:
#: On the gold-in-prompt failure slice these chunks consumed 16.9% of
#: admitted slots (158/933) while carrying no provision text —
#: ``evaluation/out/ceiling_v5/gip_window_content.txt``. Two shapes matter in
#: this corpus: arrangement-of-sections listings (long, but pure navigation)
#: and Act mastheads (the "THE <Act> ACT, <year>" cover line). Everything
#: substantive — including a one-clause penalty provision — must survive, so
#: the masthead rule only fires when nothing remains after the title and its
#: bracketed/numeric metadata.
_INDEX_MARKER_RE = re.compile(r"arrangement\s+of\s+sections", re.IGNORECASE)
_MASTHEAD_RE = re.compile(
    r"^\s*(?:\d{1,3}\.?\s+)?(?:THE\s+.{3,120}?\s+ACT|AN\s+ACT)\b",
    re.IGNORECASE,
)
#: Title metadata that may follow a masthead: years, act numbers, bracketed
#: enactment data (``[23rd March, 1974]``, ``(BEN. ACT VI OF 1944)``).
_MASTHEAD_METADATA_RE = re.compile(
    r"^[\s,\.\-–—]*(?:"
    r"\d{1,3}\.?\s+"
    r"|\(\s*[^)]{0,60}\)"
    r"|\[\s*[^\]]{0,60}\]?"
    r"|\d{4}"
    r"|NO\.?\s*\d+\s+OF\s+\d{4}"
    r"|ACT\b"
    r"|THE\b|\s+|,|\.|;|:|-"
    r")+",
    re.IGNORECASE,
)
#: A body shorter than this cannot hold a provision clause; dropping it loses
#: nothing a model could cite.
_INDEX_MIN_CHARS = 60

#: Gazette amendment-notification prose detection (config
#: ``RAG_CONTEXT_DROP_AMENDMENT_PROSE``, default on).
#:
#: The predicate is the stamper's own ``AMENDMENT_NOTIFICATION_RE`` — one
#: home, imported lazily.  A second copy here would be exactly the
#: duplication the provision-currency seam was built to prevent, and the
#: engine import stays lazy so a deployment without the package still boots
#: (same pattern as ``app/services/legal_engine.get_legal_engine``).
_amendment_re: re.Pattern[str] | None = None
_amendment_re_resolved = False


def _resolve_amendment_re() -> re.Pattern[str] | None:
    """The engine's amendment regex, or ``None`` when the engine is absent."""
    global _amendment_re, _amendment_re_resolved
    if not _amendment_re_resolved:
        _amendment_re_resolved = True
        try:
            from legal_paragraph_detection_engine.src.legal_engine import AMENDMENT_NOTIFICATION_RE

            _amendment_re = AMENDMENT_NOTIFICATION_RE
        except Exception as exc:  # pragma: no cover - packaging-dependent
            logger.debug("amendment regex unavailable (%s); amendment drop disabled", exc)
            _amendment_re = None
    return _amendment_re


def is_amendment_prose(text: str) -> bool:
    """True when *text* is a gazette amendment notification.

    Such a chunk *refers to* a provision ("In section 63, for the words
    X, the words Y shall be substituted") without containing one — it
    carries the reference of an operative clause but none of its
    content, so a packed window slot spent on it is a slot the answer
    cannot cite.  This is the same defect class the stamper fix removed
    from ``section_number`` (poisoned stamps 141 -> 0, no gold unit lost,
    ``evaluation/FINDINGS_89_STATUS.md``).
    """
    rx = _resolve_amendment_re()
    return bool(rx and rx.search(text or ""))


def is_index_chunk(text: str) -> bool:
    """True when *text* is navigation/heading material, not a provision.

    Deliberately conservative: a penalty clause, a definition-with-explanation,
    and an amendment-note fragment all pass. Only arrangement-of-sections
    listings, bare mastheads, and under-60-character fragments are treated as
    index material.
    """
    stripped = (text or "").strip()
    if len(stripped) < _INDEX_MIN_CHARS:
        return True
    if _INDEX_MARKER_RE.search(stripped):
        return True
    # Masthead-only: after the title and its enactment metadata, nothing is
    # left. Operative text ("... ACT, 2006. Section 31 provides that ...")
    # leaves a remainder and survives.
    match = _MASTHEAD_RE.match(stripped)
    if match:
        remainder = _MASTHEAD_METADATA_RE.sub("", stripped[match.end() :], count=1).strip(" ,.-;:][()")
        if not remainder:
            return True
    return False


@dataclass
class BuiltContext:
    """Result of assembling a retrieval context for the LLM."""

    context: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    chunk_count: int = 0
    truncated: bool = False
    total_tokens_estimate: int = 0
    enough_evidence: bool = True  # 2.8: answerability check flag
    missing: list[str] = field(default_factory=list)  # 2.8: missing requirement IDs


class ContextBuilder:
    """Assemble retrieved chunks into a structured LLM context.

    Args:
        max_context_chars: Maximum total characters for the context text.
        max_chunks: Maximum number of chunks to include.

    """

    # 2.6: Per-query-type context budgets, as CAPS relative to the operator's
    # ceiling (RAG_CONTEXT_MAX_CHUNKS / RAG_CONTEXT_MAX_CHARS).
    #
    # Raised from the original 8-12 chunk / 10k-16k char hardcodes after
    # measuring where the audited failures actually come from
    # (evaluation/failure_attribution.py): for 40 of the 89 model_wrong
    # questions the gold provision IS in the candidate pool but ranks below the
    # window — R@10 51.7% vs R@20 68.5%. Those were unanswerable by
    # construction. Two things forced this to move chars and chunks together:
    # measured evidence-set fit was flat across max_chunks 10/15/20 but jumped
    # when max_context_chars rose, so the char budget binds first.
    #
    # These are caps, applied via min() against the configured ceiling, so an
    # operator setting a SMALLER window still wins. They only raise the default
    # when the ceiling is larger.
    _QUERY_TYPE_BUDGETS: ClassVar[dict[str, dict[str, int]]] = {
        "case_law": {"max_context_chars": 40_000, "max_chunks": 50},
        "cross_reference": {"max_context_chars": 40_000, "max_chunks": 50},
        "prohibition": {"max_context_chars": 40_000, "max_chunks": 50},
        "definition": {"max_context_chars": 40_000, "max_chunks": 50},
        "penalty": {"max_context_chars": 40_000, "max_chunks": 50},
        "general": {"max_context_chars": 40_000, "max_chunks": 50},
        "procedure": {"max_context_chars": 40_000, "max_chunks": 50},
    }

    def __init__(
        self,
        max_context_chars: int | None = None,
        max_chunks: int | None = None,
        query_type: str = "",
    ) -> None:
        # 2.6: Adjust budget per query type when caller doesn't override.
        # Context-K lever (RAG_CONTEXT_MAX_CHUNKS / RAG_CONTEXT_MAX_CHARS)
        # sets the CEILING. Per-type budgets are caps applied beneath it via
        # min(), so raising the configured window actually reaches typed
        # queries instead of being silently overridden by a hardcoded table
        # (which is what made RAG_CONTEXT_MAX_CHUNKS=20 a no-op).
        base_chars = cfg.context_max_chars if max_context_chars is None else max_context_chars
        base_chunks = cfg.context_max_chunks if max_chunks is None else max_chunks
        budget = self._QUERY_TYPE_BUDGETS.get(query_type.lower(), {})
        self.max_context_chunks = min(budget.get("max_chunks", base_chunks), base_chunks)
        self.max_context_chars = min(budget.get("max_context_chars", base_chars), base_chars)
        self._query_type = query_type

    def build(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
        evidence_set: dict[str, Any] | None = None,
    ) -> BuiltContext:
        """Build a structured LLM context from retrieved chunks.

        Chunks are sorted by retrieval score (descending), limited to
        ``max_chunks``, and formatted with citation labels [n] that the LLM
        can reference as [n].  Before building the final context, performs an
        answerability check (§2.8): if evidence coverage is insufficient for
        the query type, the method signals this so the caller can trigger
        targeted retrieval instead of proceeding to generation.

        P0-1: when *evidence_set* is supplied (the serialized dict produced
        by :func:`select_evidence_set` via ``apply_stages``), selected
        provisions are packed first in legal-role order (:data:`_PACK_ORDER`)
        so the primary provision leads the prompt, with unselected chunks
        retained as overflow.  Each ``<document>`` tag carries a ``role``
        attribute naming its evidence type.  When *evidence_set* is ``None``
        the behaviour is byte-identical to the score-only ordering.
        """
        if not chunks:
            return BuiltContext(
                context="",
                citations=[],
                chunk_count=0,
                truncated=False,
                total_tokens_estimate=0,
                enough_evidence=False,
                missing=["empty_chunks"],
            )

        # 2.8: Answerability check — assess whether retrieved chunks satisfy
        # evidence requirements for this query type.
        try:
            enough_evidence, missing = self._check_answerability(query, chunks, query_type or self._query_type)
        except Exception as exc:
            logger.warning("Answerability check failed: %s", exc)
            enough_evidence, missing = True, []  # fail open
        if not enough_evidence:
            return BuiltContext(
                context="",
                citations=[],
                chunk_count=0,
                truncated=False,
                total_tokens_estimate=0,
                enough_evidence=False,
                missing=missing,
            )

        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)

        # P0-1 roles are resolved first because the index drop below has to
        # know which chunks the evidence selector explicitly picked.
        roles: dict[Any, str] = {}
        if evidence_set:
            roles = self._roles_from_evidence_set(evidence_set, chunks)

        # GIP packing fix: drop index/navigation chunks before selection so the
        # freed slots go to substantive provisions ranked below them. Applied
        # after the answerability gate (the gate has already judged the full
        # pool) and before role packing, so the ranked order the packer sees is
        # score order minus index material.
        #
        # A chunk the evidence selector *selected* is never dropped. That
        # selector is the downstream, better-informed decision, and silently
        # discarding what it chose would leave ``_pack_by_role`` reordering a
        # set it never receives. ``is_index_chunk`` also fires on anything
        # under ``_INDEX_MIN_CHARS``, and a short selected provision is exactly
        # the kind of chunk a role packer is meant to promote. Amendment prose
        # (``is_amendment_prose``) is exempt the same way.
        dropped_index = 0
        drop_index = bool(cfg.context_drop_index_chunks)
        drop_amendment = bool(cfg.context_drop_amendment_prose)
        if drop_index or drop_amendment:
            keep: list[RetrievedChunk] = []
            for chunk in ranked:
                if chunk.chunk_id not in roles and (
                    (drop_index and is_index_chunk(chunk.text)) or (drop_amendment and is_amendment_prose(chunk.text))
                ):
                    dropped_index += 1
                    continue
                keep.append(chunk)
            # Never empty the window: if every chunk looked like index
            # material, fall back to the unfiltered ranking.
            if keep:
                ranked = keep
                if dropped_index:
                    logger.debug("Dropped %d index/amendment chunks before packing", dropped_index)

        # P0-1: evidence-set packing.  The answerability gate above has
        # already judged the FULL pool — packing happens strictly after, so
        # filtering can never shrink the set that gate considered.
        if evidence_set:
            ranked = self._pack_by_role(ranked, roles)
        selected = ranked[: self.max_context_chunks]

        context_parts: list[str] = []
        citations: list[dict[str, Any]] = []
        total_chars = 0
        truncated = len(chunks) > self.max_context_chunks

        for idx, chunk in enumerate(selected, start=1):
            header = self._format_header(chunk)
            role = roles.get(chunk.chunk_id)
            entry = self._format_entry(idx, header, chunk.text, role=role)
            entry_len = len(entry) + _CHUNK_OVERHEAD_CHARS

            if total_chars + entry_len > self.max_context_chars:
                remaining = self.max_context_chars - total_chars
                if remaining > 200:
                    overhead = len(self._format_entry(idx, header, "", role=role))
                    max_text = remaining - overhead
                    truncated_text = chunk.text[: max(0, max_text)]
                    entry = self._format_entry(idx, header, truncated_text, role=role)
                    context_parts.append(entry)
                    total_chars += len(entry)
                    truncated = True
                    citations.append(self._citation_entry(idx, chunk, role))
                else:
                    truncated = True
                break

            context_parts.append(entry)
            total_chars += entry_len
            citations.append(self._citation_entry(idx, chunk, role))

        context = "\n\n---\n\n".join(context_parts)
        token_est = int(len(context) * _TOKENS_PER_CHAR)

        return BuiltContext(
            context=context,
            citations=citations,
            chunk_count=len(citations),
            truncated=truncated,
            total_tokens_estimate=token_est,
            enough_evidence=enough_evidence,
            missing=missing,
        )

    def _check_answerability(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str,
    ) -> tuple[bool, list[str]]:
        """2.8: Determine if retrieved chunks satisfy evidence requirements.

        Heuristic: count how many distinct evidence types are covered by the
        retrieved chunks and compare against the minimum per query type.

        Returns:
            (enough_evidence: bool, missing: list of requirement IDs)

        """
        covered_types: set[str] = set()

        for chunk in chunks:
            ct = (chunk.text or "").lower()
            # Simple heuristic: detect evidence types from chunk text
            if any(t in ct for t in ["section", "article", "provision"]):
                covered_types.add("PROVISION")
            if any(t in ct for t in ["penalty", "fine", "punishment"]):
                covered_types.add("PENALTY_PROVISION")
            if any(t in ct for t in ["authority", "enforcement", "power"]):
                covered_types.add("AUTHORITY_PROVISION")
            if any(t in ct for t in ["definition", "means"]):
                covered_types.add("DEFINITION")
            if any(t in ct for t in ["exception", "unless", "except"]):
                covered_types.add("EXCEPTION")
            if any(t in ct for t in ["reference", "cross"]):
                covered_types.add("CROSS_REFERENCE")
            if any(t in ct for t in ["case", "precedent", "court"]):
                covered_types.add("CASE_LAW")
            if any(t in ct for t in ["temporal", "before", "after"]):
                covered_types.add("TEMPORAL")
            if any(t in ct for t in ["jurisdiction", "state", "district"]):
                covered_types.add("JURISDICTION")

        # Query-type minimum coverage thresholds (2.8)
        min_coverage: dict[str, int] = {
            "case_law": 3,
            "cross_reference": 2,
            "prohibition": 2,
            "definition": 1,
            "penalty": 2,
            "general": 2,
            "procedure": 2,
        }
        min_req = min_coverage.get(query_type.lower(), 2)

        if len(covered_types) < min_req:
            all_required = set(min_coverage.keys())
            missing_types = [t for t in all_required if t not in covered_types]
            return False, [f"type:{t}" for t in missing_types]

        if len(chunks) < 2 and query_type not in ("definition",):
            return False, ["count:minimum_chunks"]

        return True, []

    @staticmethod
    def _format_header(chunk: RetrievedChunk) -> str:
        parts: list[str] = []
        if chunk.document_title:
            parts.append(chunk.document_title)
        if chunk.section_number:
            parts.append(f"Section {chunk.section_number}")
        meta: list[str] = []
        if chunk.authority:
            meta.append(f"Authority: {chunk.authority}")
        if chunk.document_type:
            meta.append(f"Type: {chunk.document_type}")
        header = ", ".join(parts) if parts else "Unnamed document"
        if meta:
            header += f" ({', '.join(meta)})"
        return header

    @staticmethod
    def _roles_from_evidence_set(evidence_set: dict[str, Any], chunks: list[RetrievedChunk]) -> dict[str, str]:
        """Map chunk_id → evidence_type for the items the selector kept.

        Tolerates the serialized dict form (``EvidenceSet.to_dict()``) and
        ignores items whose chunk is no longer in the pool.  Unknown or
        malformed shapes degrade to an empty mapping, which leaves packing
        in score order rather than failing generation.
        """
        items = evidence_set.get("items") if isinstance(evidence_set, dict) else None
        if not isinstance(items, list):
            return {}
        known = {c.chunk_id for c in chunks}
        roles: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            chunk_id = item.get("chunk_id")
            evidence_type = item.get("evidence_type")
            if chunk_id and evidence_type and chunk_id in known:
                roles[chunk_id] = str(evidence_type)
        return roles

    @staticmethod
    def _pack_by_role(ranked: list[RetrievedChunk], roles: dict[str, str]) -> list[RetrievedChunk]:
        """Order selected provisions by legal role, unselected as overflow.

        Selected chunks sort by role (:data:`_PACK_ORDER`) then by descending score;
        unselected chunks follow in their original score order.
        """
        if not roles:
            return list(ranked)
        return sorted(
            ranked,
            key=lambda c: (
                0 if c.chunk_id in roles else 1,
                _PACK_ORDER.get(roles.get(c.chunk_id, ""), _PACK_ORDER_DEFAULT),
                -c.score,
            ),
        )

    @staticmethod
    def _format_entry(idx: int, header: str, text: str, role: str | None = None) -> str:
        """One per-source entry (research §3.1 document/source tags).

        P0-1: *role* annotates the tag with the provision's legal role so the
        model can distinguish the governing provision from definitions and
        exceptions.  Omitted entirely when no evidence set was supplied, so
        the default prompt is unchanged.
        """
        if role:
            return (
                f'<document index="{idx}" role="{role}">\n<source>[Source {idx}] {header}</source>\n{text}\n</document>'
            )
        return f'<document index="{idx}">\n<source>[Source {idx}] {header}</source>\n{text}\n</document>'

    @staticmethod
    def _citation_entry(idx: int, chunk: RetrievedChunk, role: str | None = None) -> dict[str, Any]:
        entry = {
            "index": idx,
            "chunk_id": chunk.chunk_id,
            "section_number": chunk.section_number,
            "document_title": chunk.document_title,
            "document_type": chunk.document_type,
            "authority": chunk.authority,
        }
        if role:
            entry["evidence_type"] = role
        return entry
