"""Legal-aware reranker — ENTITY + INTENT + PROVISION matching (task spec §6-§9).

Two-stage reranking on top of the existing hybrid pool:

* **Stage 1 — entity relevance.  Does this chunk concern the requested
  commodity/entity?**  Chunks that never mention the entity (nor its
  canonical name) are demoted below every entity-matching chunk.
* **Stage 2 — legal intent relevance.  Does this chunk carry the *type* of
  legal information requested?**  A definition chunk has high entity
  relevance but low standard relevance for ``food_standard`` queries; the
  intent stage is what fixes the definition-anchoring failure.

Final score (task spec §7, weights configurable + ablatable)::

    FinalScore =
        w_ce   * norm_ce        # cross-encoder / primary semantic signal
      + w_dense * dense         # original dense/retrieval score (RRF-normalised)
      + w_entity  * entity_match
      + w_intent  * intent_match       (document_role vs requested intent)
      + w_prov    * provision_type_match
      + w_legal   * legal_identifier_match   (clause/section anchor)
      + w_parent  * parent_context_match
      + w_lex     * provision_lexical_signal (§8 regulatory language)

The CE reranker of record stays primary: when the ensemble reranker has
already scored the pool, its scores ride in as ``w_ce``; when it has not,
this reranker computes the deterministic features only (graceful — no model
required), so offline ablations are reproducible without the CE.

Stage-1 gating is a *soft* demotion, not a hard filter: an entity-absent
chunk keeps ``w_ce`` semantics but loses every entity/intent/parent bonus, so
it can still surface when nothing better exists (regression safety for
ordinary queries whose entity vocabulary differs from the corpus).

Feature flag: ``RAG_FOOD_LEGAL_RERANK`` (default on).  ``enabled=False``
constructs a pass-through wrapper so callers can ablate the layer without
changing call sites.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.rag.retrieval.food_query_understanding import FoodQueryUnderstanding
from app.rag.retrieval.parent_reconstruction import clause_commodity_for
from app.rag.retrieval.provision_metadata import (
    _CLAUSE_LEAD_RE,
    _KNOWN_COMMODITIES,
    commodity_agrees,
    commodity_phrase_match,
    derive_provision_metadata_cached,
    is_definition_chunk,
    is_standard_chunk,
)

logger = logging.getLogger(__name__)

__all__ = ["LegalAwareReranker", "LegalAwareWeights", "default_weights"]


def default_weights() -> LegalAwareWeights:
    """Initial weights — deliberately conservative (semantic signals dominate;
    legal features break ties).  Tuned by the ablation study, not guessed as
    final.
    """
    return LegalAwareWeights(
        w_ce=0.40,
        w_dense=0.15,
        w_entity=0.20,
        w_intent=0.25,
        w_prov=0.10,
        w_legal=0.05,
        w_parent=0.10,
        w_lex=0.15,
    )


@dataclass
class LegalAwareWeights:
    """Configurable feature weights (task spec §7).  All non-negative."""

    w_ce: float = 0.40
    w_dense: float = 0.15
    w_entity: float = 0.20
    w_intent: float = 0.25
    w_prov: float = 0.10
    w_legal: float = 0.05
    w_parent: float = 0.10
    w_lex: float = 0.15
    w_param: float = 0.20

    def as_dict(self) -> dict[str, float]:
        return {
            "w_ce": self.w_ce,
            "w_dense": self.w_dense,
            "w_entity": self.w_entity,
            "w_intent": self.w_intent,
            "w_prov": self.w_prov,
            "w_legal": self.w_legal,
            "w_parent": self.w_parent,
            "w_lex": self.w_lex,
            "w_param": self.w_param,
        }

    @classmethod
    def from_env(cls, cfg: Any) -> LegalAwareWeights:
        """Read overrides from the config seam (RAG_FOOD_LEGAL_W_*)."""
        w = cls()
        try:
            for name in ("w_ce", "w_dense", "w_entity", "w_intent", "w_prov", "w_legal", "w_parent", "w_lex", "w_param"):
                val = getattr(cfg, f"food_legal_{name}", None)
                if val is not None:
                    setattr(w, name, float(val))
        except Exception:  # pragma: no cover - config seam edge cases
            pass
        return w


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

_ENTITY_TOKEN_MIN = 3  # entities shorter than this ("ghee"=4 ok, "tea"=3 ok) guard

#: The pool currently being reranked (set by :meth:`LegalAwareReranker.rerank`).
#: Feature functions read it for pool-relative judgements (e.g. "does another
#: chunk carry the entity identity before I demote this heading?").  Single-
#: threaded pipeline; cleared+refilled per call.
_CURRENT_POOL: list[Any] = []


def _entity_variants(entity: str | None) -> list[str]:
    """Token variants to look for: the full entity and its significant words.

    Single-word variants must be KNOWN commodity words (ablation iteration 8):
    the generic token of a compound name ("mixed" of "mixed masala") matched
    prose ("when it is mixed with …") and fabricated a false identity anchor
    that outranked the real clause (FI015).  Unknown tokens ("mixed", "pan")
    stay inside the full-phrase variant only; commodity tokens ("masala" of
    "mixed masala", "ginger" of "ginger cocktail") remain usable variants —
    the commodity-compatibility gate in :func:`_entity_match` still decides
    whether they may match.
    """
    if not entity:
        return []
    e = entity.lower().strip()
    variants = [e]
    words = [w for w in e.split() if len(w) >= _ENTITY_TOKEN_MIN]
    if len(words) > 1:
        for w in words:
            if w not in variants and w in _KNOWN_COMMODITIES:
                variants.append(w)
    return variants


def _entity_match(chunk: Any, entity: str | None) -> float:
    """1.0 when the chunk text/title mentions the entity (or a variant).

    Phrase-aware (ablation iteration 4): the mention must be the *commodity
    phrase* — "Ginger Cocktail"/"Cumin Black" do not mention "ginger"/"cumin",
    while "cumin powder" does.  A table-row fragment that names no commodity
    inherits its clause sibling's commodity through the clause→commodity map,
    so "(iii) Moisture ≤ 10%" under clause 2.9.8 counts as an entity match.

    Commodity-compatibility gate (iteration 5): OCR chunks that are
    ingredient lists / gazette blobs name MANY commodities ("curry powder
    means … coriander, cumin, cardamom …"); a verbatim mention there is
    incidental, not identity.  The text-mention branch therefore requires
    the chunk's derived commodity (its clause heading's first commodity
    word) to agree with the entity — unknown-derived chunks (bare rows) are
    unaffected.
    """
    if not entity:
        return 0.0
    text = str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else ""))
    title = str(getattr(chunk, "document_title", "") or (chunk.get("document_title", "") if isinstance(chunk, dict) else ""))
    derived = str(derive_provision_metadata_cached(chunk).get("commodity", "unknown") or "unknown")
    # Bidirectional agreement: the harvested vocabulary means the query side
    # may carry the full clause name ("mixed masala") while the chunk-side
    # heading derives the head ("masala"), or the reverse.  Compound guards
    # ("Pan Masala"/"Cumin Black"/"Ginger Cocktail") still disagree.
    compatible = derived == "unknown" or commodity_agrees(derived, entity.lower())
    best = 0.0
    if compatible:
        for v in _entity_variants(entity):
            if len(v) < _ENTITY_TOKEN_MIN:
                continue
            if commodity_phrase_match(title, v) or commodity_phrase_match(text, v):
                best = max(best, 1.0)
    if best == 0.0:
        # Clause-sibling inheritance: the parent-reconstruction grouping pass
        # registered the clause→commodity map from heading chunks in the
        # pool; a row fragment under that clause inherits the match.
        # Phrase-aware: "ginger cocktail"/"cumin black" clauses do NOT
        # transfer their identity to "ginger"/"cumin" queries.
        # Table-like gate (iteration 6): only provision-table fragments
        # (rows/measurements) or the clause-lead heading inherit — clause
        # boilerplate does not.
        commodity = clause_commodity_for(chunk)
        if commodity and commodity_agrees(commodity, entity.lower()) and _is_table_like(chunk):
            best = 0.9
    return best


def _intent_match(chunk: Any, fq: FoodQueryUnderstanding) -> float:
    """Document-role vs requested intent (Stage 2 — the definition trap fix).

    mapping: what the query wants  →  roles that satisfy it
    """
    want_roles: dict[str, set[str]] = {
        "food_standard": {"standard", "limit"},
        "requirement": {"standard", "requirement", "limit"},
        "parameter_specific_standard": {"standard", "limit"},
        "compliance": {"standard", "limit"},
        "definition": {"definition"},
        "prohibition": {"prohibition"},
        "licensing": {"licensing"},
        "sampling": {"sampling"},
        "procedure": {"procedure"},
        "penalty": {"penalty"},
        "scope": {"scope"},
        "general_information": {"general"},
    }
    roles = want_roles.get(fq.intent, {"general"})
    meta = derive_provision_metadata_cached(chunk)
    role = str(meta.get("document_role", "general"))

    # Definition-form clause headings ("2.9.4: Cinnamon … whole means …") are
    # NOT limit rows even when their tail carries requirement language — the
    # role detector reads "standard" from that tail.  Checked FIRST so the
    # early role return cannot mask the demotion (ablation iteration 3: FI019
    # showed 0.15 was not enough — the heading still won on dense ties).
    #
    # Anchor-aware (iteration 4): the heading is also the pool's IDENTITY
    # anchor.  Demote to 0.0 only when another chunk in the pool can carry
    # the entity identity (a sibling row of the entity's clause, via the
    # clause map).  When no anchor exists, keep the heading at the role
    # score — demoting the pool's only identity-bearing evidence left the
    # top slot to foreign rows (FI015) and starved validation of its
    # entity match (FI019, fallback starved of its anchor).
    if fq.wants_standard and _is_clause_definition_heading(chunk):
        # Sibling anchor: a standard-shaped chunk that carries the entity
        # identity (own text or clause map) — i.e. the entity's real limit
        # rows are in the pool and the heading is pure context.
        has_sibling_anchor = any(
            other is not chunk and is_standard_chunk(other) and _entity_match(other, fq.entity) > 0
            for other in _CURRENT_POOL
        )
        if has_sibling_anchor:
            return 0.0  # identity carried elsewhere — heading is pure context
        return 0.9  # definition-only clause: the heading IS the entity's provision

    if role in roles:
        # Within-role gradation: a real standard beats a bare limit row.
        if is_standard_chunk(chunk) and fq.wants_standard:
            return 1.0
        # Definition asks strongly prefer the clause's LEAD definition
        # ("1. Cardamom … whole means") over later form sub-definitions
        # ("3. Cardamom powder means") — the lead carries the commodity's
        # canonical identity (iteration 5, FI012; the gap must exceed the
        # w_dense spread between the two chunks).
        if fq.intent == "definition":
            return 1.0 if _clause_top_position(chunk) == 0 else 0.3
        return 0.9
    # Cross-intent penalties: a definition chunk is actively wrong for a
    # standard query and vice versa (the measured failure mode).
    if fq.wants_standard and is_definition_chunk(chunk):
        return 0.0
    if fq.intent == "definition" and is_standard_chunk(chunk):
        return 0.1
    return 0.3  # neutral evidence (explanations, adjacent provisions)


_DEFINITION_HEAD_RE = re.compile(r"^\s*\d{1,2}\.\d{1,3}(?:\.\d{1,3})?\s*[:.]\s*", re.IGNORECASE)


def _is_clause_definition_heading(chunk: Any) -> bool:
    """True for the clause-heading chunk whose body is a *definition*
    ("2.9.4: Cinnamon … whole means …").

    These dual-role chunks (definition lead + requirement tail) defeat the
    plain definition detector; the clause-lead marker + 'means' shape is the
    reliable signature (ablation iteration 3: FI019).
    """
    text = str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else ""))
    if not text or not _DEFINITION_HEAD_RE.match(text):
        return False
    return bool(re.search(r"\bmeans\b", text, re.IGNORECASE))


def _provision_type_match(chunk: Any, fq: FoodQueryUnderstanding) -> float:
    """Derived provision_type vs requested_provision_type."""
    meta = derive_provision_metadata_cached(chunk)
    pt = str(meta.get("provision_type", "general"))
    want = fq.requested_provision_type
    if want == "general" or pt == "general":
        return 0.5
    if pt == want:
        return 1.0
    # A limit row satisfies a standard ask (it is part of the standard table).
    if want == "standard" and pt in ("food_standard",):
        return 1.0
    if want == "standard" and pt in ("licensing", "procedure", "sampling", "penalty"):
        return 0.1
    return 0.4


def _legal_identifier_match(chunk: Any, fq: FoodQueryUnderstanding) -> float:
    """Chunk anchored to a legal identifier (clause/section) — the §4
    hierarchy-preservation signal.  Parameter-specific queries additionally
    reward the exact clause the parameter row hangs from.
    """
    meta = derive_provision_metadata_cached(chunk)
    section = str(meta.get("section", "unknown"))
    if section == "unknown":
        return 0.0
    score = 0.6
    if fq.intent == "parameter_specific_standard":
        # Rows inside a dotted clause = the actual requirement row.
        score = 1.0 if re.match(r"\d{1,2}\.\d", section) else score
    return score


def _clause_top_position(chunk: Any) -> int:
    """Chunk order within its clause (10**9 when unknown).

    The clause heading is position 0; its first table row ("(i) Extraneous
    matter …", the operative "shall conform" row that names the standard)
    is position 1.  Used as a stable tie-break among sibling standard rows.
    """
    text = str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else ""))
    idx = getattr(chunk, "chunk_index", None)
    if idx is None and isinstance(chunk, dict):
        idx = chunk.get("chunk_index", None)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return 10**9
    if _CLAUSE_LEAD_RE.match(text or ""):
        return 0
    if re.match(r"^\s*\((?:i|1)\)\s+\w", text or "", re.IGNORECASE):
        return 1
    return 2


def _parent_context_match(chunk: Any, parent_context: list[Any]) -> float:
    """1.0 when the chunk IS part of the reconstructed parent bundle, or is
    the parent itself (a heading chunk with children in the pool).
    """
    cid = str(getattr(chunk, "chunk_id", "") or (chunk.get("chunk_id", "") if isinstance(chunk, dict) else ""))
    if not cid:
        return 0.0
    if any(cid == str(getattr(p, "chunk_id", "") or (p.get("chunk_id", "") if isinstance(p, dict) else "")) for p in parent_context):
        return 1.0
    return 0.0


_PROVISION_LEXICON: tuple[str, ...] = (
    "shall conform",
    "shall comply",
    "requirements",
    "maximum",
    "minimum",
    "not more than",
    "not less than",
    "limit",
    "prescribed",
    "per cent by weight",
    "percent by weight",
    "moisture",
    "extraneous matter",
    "foreign matter",
    "defective",
    "total ash",
    "acid insoluble",
    "volatile oil",
)


_MEASUREMENT_RE = re.compile(
    r"\b(?:not\s+more\s+than|not\s+less\s+than|maximum|minimum|per\s+cent|percent|mg/kg|ppm)\b",
    re.IGNORECASE,
)

_ROW_MARKER_RE = re.compile(r"\((?:[ivx]+|\d+)\)\s+\w", re.IGNORECASE)


def _is_table_like(chunk: Any) -> bool:
    """Whether the chunk looks like part of a clause's provision table
    (row markers, measurements, or the clause-lead heading).

    Used to gate clause-map inheritance: boilerplate prose inside a clause
    (gazette page furniture, labelling notes) must NOT inherit the clause's
    commodity identity (iteration 6: the clause 3.1.3 gazette blob beating
    the MIXED MASALA standard clause on a 'standards for mixed masala' ask).
    """
    text = str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else ""))
    if not text:
        return False
    if _CLAUSE_LEAD_RE.match(text):
        return True
    return bool(_ROW_MARKER_RE.search(text) or _MEASUREMENT_RE.search(text))


def _param_match(chunk: Any, fq: FoodQueryUnderstanding) -> float:
    """Named-parameter feature (ablation iteration 2).

    When the query names a parameter ("moisture"), chunks carrying that
    parameter *together with a measurement* (an actual limit row) score 1.0;
    chunks carrying the parameter without a measurement 0.5; everything
    else 0.0.  This is what separates a clause's own moisture row from the
    clause's other rows and from sibling-commodity rows.
    """
    if not fq.parameters:
        return 0.0
    text = str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else "")).lower()
    if not text:
        return 0.0
    if not any(p.lower() in text for p in fq.parameters):
        return 0.0
    return 1.0 if _MEASUREMENT_RE.search(text) else 0.5


def _provision_lexical_signal(chunk: Any, fq: FoodQueryUnderstanding) -> float:
    """§8 — provision-specific lexical evidence, *gated by entity match*.

    The boost applies only when the chunk also mentions the requested entity,
    so a random chunk containing the word "standard" is never boosted
    (spec §8's explicit anti-pattern).
    """
    # Provision-lexical evidence only boosts standard/limit intents — for a
    # definition ask, regulatory language is NOT the target signal (a limit
    # row must not outrank the definition the user asked for).
    if not fq.wants_standard:
        return 0.0
    text = str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else "")).lower()
    if not text:
        return 0.0
    # Entity gate
    if fq.entity and _entity_match(chunk, fq.entity) == 0.0:
        return 0.0
    hits = sum(1 for t in _PROVISION_LEXICON if t in text)
    if hits == 0:
        return 0.0
    return min(hits / 3.0, 1.0)


def _normalise_scores(values: list[float]) -> list[float]:
    """Min-max to [0,1]; all-equal input maps to 0.5s (neutral, not zero)."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 1e-9:
        return [0.5] * len(values)
    return [(v - lo) / span for v in values]


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------

@dataclass
class _ChunkFeatures:
    entity: float
    intent: float
    prov: float
    legal: float
    parent: float
    lex: float
    param: float = 0.0


class LegalAwareReranker:
    """Two-stage legal-aware reranker (task spec §9).

    Args:
        weights: feature weights (default: :func:`default_weights`).
        enabled: when False, :meth:`rerank` returns the input order unchanged
            (ablation arm / flag-off path).

    """

    def __init__(
        self,
        weights: LegalAwareWeights | None = None,
        enabled: bool = True,
        parent_context: list[Any] | None = None,
    ) -> None:
        self.weights = weights or default_weights()
        self.enabled = enabled
        self.parent_context = parent_context or []

    def rerank(
        self,
        query: str,
        chunks: list[Any],
        food: FoodQueryUnderstanding | None = None,
        ce_scores: dict[str, float] | None = None,
        top_k: int | None = None,
    ) -> list[Any]:
        """Re-rank ``chunks`` by the legal-aware score.

        Args:
            query: the original user query.
            chunks: the fused candidate pool (already ranked by hybrid/CE).
            food: the parsed :class:`FoodQueryUnderstanding` (parsed from
                ``query`` when omitted).
            ce_scores: optional ``{chunk_id: normalized_ce}`` from the
                ensemble reranker head — the primary semantic signal.
            top_k: truncate after ranking.

        Returns:
            The re-ranked chunk list (same objects, ``score`` updated to the
            final blended score).

        """
        if not chunks:
            return []
        if not self.enabled:
            return chunks[:top_k] if top_k is not None else list(chunks)

        # Publish the clause→commodity map from this pool (heading chunks are
        # authoritative) so row fragments inherit their commodity in the
        # entity feature.  Cheap pure-Python pass; idempotent per pool.
        try:
            from app.rag.retrieval.parent_reconstruction import group_by_clause

            group_by_clause(chunks)
        except Exception as exc:  # pragma: no cover - grouping must never break reranking
            logger.warning("legal_reranker: clause grouping failed (%s)", exc)
        _CURRENT_POOL.clear()
        _CURRENT_POOL.extend(chunks)

        fq = food or FoodQueryUnderstanding.from_query(query)
        w = self.weights
        ce = ce_scores or {}
        ce_norm = _normalise_scores([float(ce.get(_cid(c), 0.0)) for c in chunks]) if ce else [0.5] * len(chunks)
        dense_norm = _normalise_scores([float(getattr(c, "score", 0.0) or (c.get("score", 0.0) if isinstance(c, dict) else 0.0)) for c in chunks]) if len(chunks) > 1 else [1.0]

        feats: list[_ChunkFeatures] = []
        for chunk in chunks:
            feats.append(
                _ChunkFeatures(
                    entity=_entity_match(chunk, fq.entity),
                    intent=_intent_match(chunk, fq),
                    prov=_provision_type_match(chunk, fq),
                    legal=_legal_identifier_match(chunk, fq),
                    parent=_parent_context_match(chunk, self.parent_context),
                    lex=_provision_lexical_signal(chunk, fq),
                    param=_param_match(chunk, fq),
                ),
            )

        # Stage-1 soft gate (relative form): the cap applies only when the
        # pool actually contains entity-matching chunks — when nothing in the
        # pool mentions the entity, the signal is uninformative and capping
        # everything would erase the intent features (spec §20 regression
        # safety for queries whose entity vocabulary differs from the corpus).
        any_entity_match = any(f.entity > 0.0 for f in feats)
        # Identity anchor = entity match from the chunk's OWN text (not the
        # clause map) — a verbatim mention is decisive for pool identity.
        anchor_present = [f.entity >= 1.0 for f in feats]
        finals: list[float] = []
        for i, _chunk in enumerate(chunks):
            f = feats[i]
            score = (
                w.w_ce * ce_norm[i]
                + w.w_dense * dense_norm[i]
                + w.w_entity * f.entity
                + w.w_intent * f.intent
                + w.w_prov * f.prov
                + w.w_legal * f.legal
                + w.w_parent * f.parent
                + w.w_lex * f.lex
                + w.w_param * f.param
            )
            finals.append(score)

        # Definition-form cap (iteration 6): for a definition ask, the
        # clause-LEAD chunk (the canonical "1. Cardamom … whole means …"
        # heading) must not be outranked by later form sub-definitions
        # ("3. Cardamom powder means …") on CE/dense ties (FI012).  The lead
        # is keyed on POSITION among entity-matching chunks, not derived
        # role — the heading's requirement tail often stamps it "standard".
        if fq.intent == "definition":
            lead_finals = [
                finals[i] for i, c in enumerate(chunks) if feats[i].entity > 0 and _clause_top_position(c) == 0
            ]
            if lead_finals:
                lead_cap = max(lead_finals)
                for i, c in enumerate(chunks):
                    if (
                        feats[i].entity > 0
                        and _clause_top_position(c) != 0
                        and str(derive_provision_metadata_cached(c).get("document_role", "")) == "definition"
                    ):
                        finals[i] = min(finals[i], lead_cap)

        # Strict pool identity (ablation iteration 4): when the pool
        # contains an identity anchor (a chunk whose own text names the
        # entity), every non-matching chunk — including foreign limit
        # rows that answer nothing — is capped at 0.45.  Without an
        # anchor, unknown-identity rows keep their original hybrid
        # scores (no information = no cap).
        for i, f in enumerate(feats):
            if (fq.entity and any(anchor_present) and f.entity == 0.0) or (fq.entity and not any(anchor_present) and any_entity_match):
                finals[i] = min(finals[i], 0.45)

        # Stable tie-break: among equal-scoring sibling rows prefer the
        # clause's top position (the "(i) …" operative row) — the ablation
        # showed the last table fragment ("(v) Non volatile ether …") winning
        # dense ties without it.
        # Universal stable tie-break (iteration 2+4): among equal-scoring
        # candidates prefer the clause-lead position — the "(i) …" operative
        # row for standard asks (FI002: last table fragment won dense ties),
        # the heading (position 0) for definition asks (FI012: the "Cardamom
        # powder means…" sub-definition outranked the clause heading).
        order = sorted(
            range(len(chunks)),
            key=lambda i: (-finals[i], _clause_top_position(chunks[i]), i),
        )
        result = []
        for i in order:
            _set_score(chunks[i], finals[i])
            result.append(chunks[i])
        if top_k is not None:
            result = result[:top_k]
        return result

    # ------------------------------------------------------------------
    # Explainability trace (task spec §21)
    # ------------------------------------------------------------------
    def trace(
        self,
        query: str,
        chunks: list[Any],
        food: FoodQueryUnderstanding | None = None,
        ce_scores: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Per-chunk feature breakdown for debugging (dev mode)."""
        fq = food or FoodQueryUnderstanding.from_query(query)
        ranked = self.rerank(query, chunks, food=fq, ce_scores=ce_scores)
        top = [
            {
                "rank": i + 1,
                "chunk_id": _cid(c),
                "document_role": derive_provision_metadata_cached(c).get("document_role"),
                "text": _ctext(c)[:120],
                "final_score": round(float(_cscore(c)), 6),
            }
            for i, c in enumerate(ranked[:10])
        ]
        return {
            "query": query,
            "entity": fq.entity,
            "intent": fq.intent,
            "target": fq.target,
            "weights": self.weights.as_dict(),
            "top_results": top,
        }


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _cid(chunk: Any) -> str:
    return str(getattr(chunk, "chunk_id", "") or (chunk.get("chunk_id", "") if isinstance(chunk, dict) else ""))


def _ctext(chunk: Any) -> str:
    return str(getattr(chunk, "text", "") or (chunk.get("text", "") if isinstance(chunk, dict) else ""))


def _cscore(chunk: Any) -> float:
    try:
        return float(getattr(chunk, "score", 0.0) or (chunk.get("score", 0.0) if isinstance(chunk, dict) else 0.0))
    except (TypeError, ValueError):
        return 0.0


def _set_score(chunk: Any, score: float) -> None:
    try:
        chunk.score = float(score)
    except AttributeError:
        if isinstance(chunk, dict):
            chunk["score"] = float(score)
