"""V8 Evidence-Set Selector â€” second-stage evidence selection for legal RAG.

Given the ARM F (dense + sparse + KG + cross-encoder rerank) top-20 chunk pool
+ up to 15 KG provisions per question, these selectors choose a complementary
evidence set of *k=10* items that maximises legal-coverage while minimising
redundancy.

Five strategies (A-E):
  A â€” TopK              : baseline, top-K by upstream CE score.
  B â€” MMR               : Maximal Marginal Relevance (text-level diversity).
  C â€” LegalStructure    : one representative per (family, section) group.
  D â€” HierarchyAware    : preserve Section->subsection->proviso chains.
  E â€” Hybrid            : MMR + legal-overlap penalty + hierarchy
                          preservation + KG section complementarity.

Design notes
------------
* ``score = 1.0 - (rank - 1) / (total - 1)`` â€” normalised position score;
  chunks naturally score higher than KG provisions because they are ranked
  first in the ARM F tail-concatenation ordering.
* Family/section resolution delegates to ``evaluation.resolution.payload_to_keys``
  and ``_kg_item_keys`` from ``evaluation.metrics`` so the *exact same* keys
  used by ``score_question`` are produced â€” guaranteeing consistency.
* ``candidates_to_arm_result`` emits ``chunk_ids`` + ``kg_provisions`` (NOT
  ``fused_items``) because the cached ARM F data has zero ``fused_items``
  entries (confirmed 2026-08-13); this matches the scoring path of the
  baseline.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from evaluation.resolution import FamilyMap, payload_to_keys

logger = logging.getLogger(__name__)

_LEGAL_STOPWORDS: frozenset[str] = frozenset({
    "the",
    "of",
    "and",
    "to",
    "in",
    "a",
    "an",
    "is",
    "for",
    "with",
    "under",
    "shall",
    "may",
    "be",
    "on",
    "that",
    "this",
    "by",
    "or",
    "as",
    "at",
    "from",
    "not",
    "but",
    "all",
    "any",
    "such",
    "has",
    "have",
    "been",
    "will",
    "would",
    "can",
    "should",
    "do",
    "does",
    "did",
    "if",
    "then",
    "than",
    "so",
    "no",
    "nor",
    "its",
    "it",
    "which",
    "who",
    "whom",
    "whose",
    "where",
    "when",
    "how",
    "what",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "only",
    "own",
    "same",
    "too",
    "very",
    "just",
    "also",
})
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokenize(text: str | None) -> frozenset[str]:
    """Lowercase alphanumeric tokens (len >= 3), legal stop-words removed."""
    if not text:
        return frozenset()
    return frozenset(t for t in _TOKEN_RE.findall(text.lower()) if t not in _LEGAL_STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets (0 if both empty)."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class CandidateItem:
    """A single candidate from the upstream ARM F pool.

    ``payload`` holds the full chunk payload dict (chunks) or the full
    KG provision dict (KG items) so ``candidates_to_arm_result`` can round-trip.
    """

    rank: int
    score: float
    kind: str
    key: str
    family: str | None
    section: str | None
    section_keys: list[tuple[str | None, str | None]]
    text: str
    document_id: str | None
    hierarchy_level: int
    parent_key: str | None
    chunk_index: int
    authority: str | None
    instrument_id: str | None
    text_tokens: frozenset[str] = field(default_factory=frozenset)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def sections_only(self) -> set[str | None]:
        """All *section* values across ``section_keys`` (de-duplicated)."""
        return {sec for _, sec in self.section_keys if sec is not None}

    @property
    def has_section(self) -> bool:
        return len(self.section_keys) > 0


def build_candidates(
    arm_result: dict[str, Any],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> list[CandidateItem]:
    """Build the candidate pool from an ARM F arm result.

    Chunks (rank 1..N) resolved via ``payload_to_keys``; KG provisions
    (rank N+1..) via ``_kg_item_keys`` from ``evaluation.metrics``.
    Duplicate keys dropped (first occurrence wins â€” preserves upstream order).
    """
    from evaluation.metrics import _kg_item_keys

    chunk_ids: list[str] = arm_result.get("chunk_ids", [])
    kg_provisions: list[dict[str, Any]] = arm_result.get("kg_provisions", [])
    n_total = len(chunk_ids) + len(kg_provisions)
    if n_total == 0:
        return []

    def _score(rank: int) -> float:
        return max(0.01, 1.0 - (rank - 1) / max(n_total - 1, 1))

    candidates: list[CandidateItem] = []
    seen: set[str] = set()

    # --- Chunks ---
    for i, cid in enumerate(chunk_ids):
        cid = str(cid)
        if cid in seen:
            continue
        seen.add(cid)
        payload = payload_index.get(cid)
        if payload is None:
            logger.debug("chunk %s not found in payload index â€” skipping", cid)
            continue
        keys = payload_to_keys(payload, family_map)
        family = keys[0][0] if keys else None
        section = keys[0][1] if keys else None
        text = payload.get("chunk_text") or payload.get("text") or ""
        doc_id = payload.get("document_id")
        hl = payload.get("hierarchy_level") or 0
        parent = payload.get("parent_chunk_id")
        ci = payload.get("chunk_index") or i + 1
        auth = payload.get("authority")
        inst_id = payload.get("instrument_id")
        candidates.append(
            CandidateItem(
                rank=i + 1,
                score=_score(i + 1),
                kind="chunk",
                key=cid,
                family=family,
                section=section,
                section_keys=list(keys),
                text=text,
                document_id=doc_id,
                hierarchy_level=int(hl) if hl else 0,
                parent_key=parent,
                chunk_index=int(ci) if ci else i + 1,
                authority=auth,
                instrument_id=inst_id,
                text_tokens=_tokenize(text),
                payload=payload,
            )
        )

    # --- KG provisions ---
    for j, prov in enumerate(kg_provisions):
        pid = prov.get("provision_id") or prov.get("id") or f"kg_{j}"
        if pid in seen:
            continue
        seen.add(pid)
        keys = _kg_item_keys(prov, family_map)
        family = keys[0][0] if keys else None
        section = keys[0][1] if keys else None
        text_parts = [
            prov.get("provision_number", ""),
            prov.get("title", ""),
            prov.get("instrument_title", ""),
        ]
        text = " ".join(p for p in text_parts if p)
        doc_id = prov.get("instrument_title")
        auth = prov.get("instrument_title") or prov.get("legal_domain") or ""
        inst_id = prov.get("instrument_id") or pid
        candidates.append(
            CandidateItem(
                rank=len(chunk_ids) + j + 1,
                score=_score(len(chunk_ids) + j + 1),
                kind="kg",
                key=pid,
                family=family,
                section=section,
                section_keys=list(keys),
                text=text,
                document_id=doc_id,
                hierarchy_level=0,
                parent_key=None,
                chunk_index=len(chunk_ids) + j + 1,
                authority=auth,
                instrument_id=inst_id,
                text_tokens=_tokenize(text),
                payload=prov,
            )
        )

    return candidates


# --------------------------------------------------------------------------- #
# Hierarchy map — infers parent/child from (document_id, section) grouping
# --------------------------------------------------------------------------- #
def _build_hierarchy_map(
    chunks: list[CandidateItem],
) -> tuple[dict[str, str | None], dict[str, list[str]]]:
    """Build parent/child maps for chunk candidates.

    Groups chunks by (document_id, primary_section) and sorts within
    each group by (hierarchy_level, chunk_index).  A parent is the most
    recent earlier item in the same group with a lower hierarchy_level.
    """
    parent_map: dict[str, str | None] = {}
    children_map: dict[str, list[str]] = defaultdict(list)

    groups: dict[tuple[str | None, str | None], list[CandidateItem]] = defaultdict(list)
    for c in chunks:
        doc = c.document_id
        sec = c.section_keys[0][1] if c.section_keys else None
        groups[(doc, sec)].append(c)

    for group_items in groups.values():
        group_items.sort(key=lambda c: (c.hierarchy_level, c.chunk_index))
        for idx, item in enumerate(group_items):
            p_key = None
            for j in range(idx - 1, -1, -1):
                if group_items[j].hierarchy_level < item.hierarchy_level:
                    p_key = group_items[j].key
                    break
            parent_map[item.key] = p_key
            if p_key is not None:
                children_map[p_key].append(item.key)

    return parent_map, dict(children_map)


# Strategy A and B selectors
@dataclass
class TopKSelector:
    def select(self, candidates, k):
        return list(candidates[:k])


@dataclass
class MMRSelector:
    lambda_param: float = 0.7

    def select(self, candidates, k):
        if len(candidates) <= k:
            return list(candidates)
        remaining = list(candidates)
        selected = []
        first = max(remaining, key=lambda c: (c.score, -c.rank))
        remaining.remove(first)
        selected.append(first)
        while len(selected) < k and remaining:
            best, best_score = None, -1.0
            for item in remaining:
                rel = item.score
                div = max(_jaccard(item.text_tokens, s.text_tokens) for s in selected) if selected else 0.0
                mmr = self.lambda_param * rel - (1.0 - self.lambda_param) * div
                if mmr > best_score:
                    best, best_score = item, mmr
            if best is None:
                best = remaining[0]
            remaining.remove(best)
            selected.append(best)
        return selected


@dataclass
class LegalStructureDiversitySelector:
    def select(self, candidates, k):
        groups = {}
        for item in candidates:
            primary = item.section_keys[0] if item.section_keys else (None, None)
            key = str(primary)
            if key not in groups:
                groups[key] = []
            groups[key].append(item)
        representatives = [max(g, key=lambda c: (c.score, -c.rank)) for g in groups.values()]
        representatives.sort(key=lambda c: (-c.score, c.rank))
        selected = representatives[:k]
        if len(selected) < k:
            selected_keys = {s.key for s in selected}
            remaining = [c for c in candidates if c.key not in selected_keys]
            remaining.sort(key=lambda c: (-c.score, c.rank))
            selected.extend(remaining[: k - len(selected)])
        return selected


@dataclass
class HierarchyAwareSelector:
    def select(self, candidates, k):
        if len(candidates) <= k:
            return list(candidates)
        chunks = [c for c in candidates if c.kind == "chunk"]
        parent_map, children_map = _build_hierarchy_map(chunks)
        chunk_lookup = {c.key: c for c in chunks}
        selected = []
        selected_keys = set()
        remaining = sorted(candidates, key=lambda c: (-c.hierarchy_level, -c.score, c.rank))
        for item in remaining:
            if len(selected) >= k or item.key in selected_keys:
                continue
            if item.kind == "chunk" and item.key in parent_map:
                p_key = parent_map[item.key]
                if p_key is not None:
                    parent = chunk_lookup.get(p_key)
                    if parent and parent.key not in selected_keys and len(selected) < k:
                        selected.append(parent)
                        selected_keys.add(parent.key)
            if len(selected) >= k:
                break
            selected.append(item)
            selected_keys.add(item.key)
            if item.kind == "chunk" and item.key in children_map:
                children = sorted(
                    [chunk_lookup[c] for c in children_map[item.key] if c in chunk_lookup],
                    key=lambda c: (-c.score, c.rank),
                )
                for child in children:
                    if len(selected) >= k:
                        break
                    if child.key not in selected_keys:
                        selected.append(child)
                        selected_keys.add(child.key)
        return selected


@dataclass
class HybridEvidenceSetSelector:
    lambda_param: float = 0.7
    legal_penalty: float = 0.15

    def select(self, candidates, k):
        if len(candidates) <= k:
            return list(candidates)
        chunks = [c for c in candidates if c.kind == "chunk"]
        kg_items = [c for c in candidates if c.kind == "kg"]
        parent_map, children_map = _build_hierarchy_map(chunks)
        chunk_lookup = {c.key: c for c in chunks}
        selected = []
        selected_keys = set()
        covered_sections = set()
        remaining_chunks = sorted(chunks, key=lambda c: (-c.score, c.rank))

        # Phase 1 - MMR + legal overlap + hierarchy
        while remaining_chunks and len(selected) < k:
            # If all remaining chunks are from already-covered sections, stop
            # Phase 1 early — Phase 2 (KG complementarity) may fill remaining
            # slots with chunks covering new sections.
            remaining_uncovered = [c for c in remaining_chunks if c.sections_only - covered_sections]
            if not remaining_uncovered:
                break
            best, best_mmr = None, -999.0
            for item in remaining_chunks:
                rel = item.score
                div = max(_jaccard(item.text_tokens, s.text_tokens) for s in selected) if selected else 0.0
                overlap_pen = self.legal_penalty if (item.sections_only & covered_sections) else 0.0
                mmr = self.lambda_param * rel - (1.0 - self.lambda_param) * div - overlap_pen
                if mmr > best_mmr:
                    best, best_mmr = item, mmr
            if best is None:
                best = remaining_chunks[0]
            remaining_chunks.remove(best)
            if best.key in parent_map:
                p_key = parent_map[best.key]
                if p_key is not None:
                    parent = chunk_lookup.get(p_key)
                    if parent and parent.key not in selected_keys and len(selected) < k:
                        self._add(parent, selected, selected_keys, covered_sections)
            if len(selected) >= k:
                break
            self._add(best, selected, selected_keys, covered_sections)
            if best.key in children_map and len(selected) < k:
                children = sorted(
                    [chunk_lookup[c] for c in children_map[best.key] if c in chunk_lookup],
                    key=lambda c: (-c.score, c.rank),
                )
                for child in children:
                    if len(selected) >= k:
                        break
                    if child.key not in selected_keys:
                        self._add(child, selected, selected_keys, covered_sections)

        # Phase 2 - KG section complementarity
        remaining_kg = [c for c in kg_items if c.key not in selected_keys]
        remaining_kg.sort(key=lambda c: (-c.score, c.rank))
        complementary = [c for c in remaining_kg if (c.sections_only - covered_sections)]
        complementary.sort(key=lambda c: (-c.score, c.rank))
        for item in complementary:
            if len(selected) >= k:
                break
            self._add(item, selected, selected_keys, covered_sections)

        # Phase 3 - top-up by score
        if len(selected) < k:
            remaining = [c for c in candidates if c.key not in selected_keys]
            remaining.sort(key=lambda c: (-c.score, c.rank))
            for item in remaining:
                if len(selected) >= k:
                    break
                self._add(item, selected, selected_keys, covered_sections)
        return selected[:k]

    @staticmethod
    def _add(item, selected, selected_keys, covered_sections):
        selected.append(item)
        selected_keys.add(item.key)
        covered_sections.update(item.sections_only)


# --------------------------------------------------------------------------- #
# Conversion: candidates -> arm_result dict
# --------------------------------------------------------------------------- #
def candidates_to_arm_result(selected, strategy_name=""):
    chunk_ids = [item.key for item in selected if item.kind == "chunk"]
    kg_provisions = [item.payload for item in selected if item.kind == "kg"]
    return {
        "arm": strategy_name,
        "chunk_ids": chunk_ids,
        "kg_provisions": kg_provisions,
        "latency_ms": 0,
        "error": None,
        "retriever": "evidence_set_selector",
    }


# --------------------------------------------------------------------------- #
# Redundancy analysis
# --------------------------------------------------------------------------- #
def _hhi(groups):
    total = sum(groups.values())
    if total == 0:
        return 0.0
    return sum((count / total) ** 2 for count in groups.values())


def compute_redundancy(selected):
    n = len(selected)
    if n == 0:
        return {
            "duplicate_provision_rate": 0.0,
            "same_section_concentration": 0.0,
            "same_document_concentration": 0.0,
        }

    section_groups = defaultdict(int)
    all_keys = []
    for item in selected:
        keys = item.section_keys if item.section_keys else [(None, None)]
        for key in keys:
            section_groups[str(key)] += 1
        all_keys.extend(keys)

    unique_keys = len(set(all_keys))
    total_keys = len(all_keys)
    if total_keys <= 1:
        dup_rate = 0.0
    elif unique_keys == 1:
        dup_rate = 1.0
    else:
        dup_rate = 1.0 - (unique_keys / total_keys)
    section_hhi = _hhi(section_groups)

    doc_groups = defaultdict(int)
    for item in selected:
        doc_id = item.document_id or "unknown"
        doc_groups[str(doc_id)] += 1
    doc_hhi = _hhi(doc_groups)

    return {
        "duplicate_provision_rate": round(dup_rate, 4),
        "same_section_concentration": round(section_hhi, 4),
        "same_document_concentration": round(doc_hhi, 4),
    }


# --------------------------------------------------------------------------- #
# Strategy registry
# --------------------------------------------------------------------------- #
STRATEGIES = {
    "V8_A_topk": (TopKSelector(), "Top-K by upstream score (baseline)"),
    "V8_B_mmr": (MMRSelector(lambda_param=0.7), "Maximal Marginal Relevance (lambda=0.7)"),
    "V8_C_legal_diversity": (
        LegalStructureDiversitySelector(),
        "Legal-structure diversity (one per section)",
    ),
    "V8_D_hierarchy": (
        HierarchyAwareSelector(),
        "Hierarchy-aware (preserves section->subsection->proviso)",
    ),
    "V8_E_hybrid": (
        HybridEvidenceSetSelector(lambda_param=0.7, legal_penalty=0.15),
        "Hybrid: MMR + legal-overlap + hierarchy + KG complementarity",
    ),
}

STRATEGY_NAMES = list(STRATEGIES.keys())
