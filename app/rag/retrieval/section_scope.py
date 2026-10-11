"""Section/act-scoped structural retrieval (production).

## Why this exists

Measured (Phase 0, ``evaluation/diagnose_retrieval_ceiling.py``): of the 78
remaining ``model_wrong`` failures, the C_hybrid arm **does** contain the gold
provision inside its 500-chunk pool for 75 of them, but only 36 of those rank
inside the top-10 — the window the ``ContextBuilder`` actually packs. So 39
failures are *ranking* failures: the right chunk is in the pool and then loses
to semantically-similar-but-wrong chunks. Where text similarity fails, exact
structural identity does not.

The corpus already carries the right structural signal. Re-ranking the same
pool with deterministic section/act features gives R@10 = **0.474**, against
**0.311** for the untuned cross-encoder and **0.362** for the fine-tuned one
(``evaluation/out/ceiling_v5/ce_rerank_eval.json``) — the strongest single
retrieval feature in this corpus, and it needs no model at all.

## What it does

Given the Act/section detected in the query, build a **payload filter** that
retrieves the structurally-correct chunks and RRF-fuse them with the existing
arms:

* **act + section known** — ``section_number == N`` AND the Act. Precise:
  this is the governing provision itself, retrieved at rank 1 regardless of
  what the vector similarity thinks.
* **act only known** — the Act's provisions, dense-ranked. An
  act-conditional arm: the query names the statute but not the number, so the
  Act's sections compete in RRF and the reranker picks the governing one.
* **nothing known** — ``None``. No arm fires. Guessing here is what produces
  regressions, so this module never guesses.

The arm is *additive*: it only ever adds chunks to the RRF fusion, so it
cannot displace gold chunks that the existing arms already retrieved.

## Safety

Everything here is deterministic and offline: regex identifier detection plus
a payload filter built from a static act vocabulary. No LLM, no embeddings, no
network beyond the Qdrant query that would have happened anyway. It is gated
behind ``RAG_SECTION_ARM`` (default ``False``) so the default path is
unchanged until validated.
"""

from __future__ import annotations

import logging
from typing import Any

from app.rag.retrieval.identifier import identifier_query

logger = logging.getLogger(__name__)

#: Payload fields that can carry an Act's identity. ``payload_to_keys`` in
#: ``evaluation/resolution.py`` resolves a chunk's family from BOTH
#: ``act_name`` and ``document_title`` (the V5 fix), because some payloads are
#: stamped with the parent Act's ``act_name`` while the actual instrument is
#: only named in ``document_title``. Mirroring that union here keeps the
#: filter's notion of "this Act" the same as the gold resolver's.
ACT_SCOPE_FIELDS: tuple[str, ...] = ("act_name", "document_title")


def section_scope_filter(act: str | None, section: str | None) -> dict[str, Any] | None:
    """Build the payload filter for a section/act-scoped retrieval arm.

    Returns ``None`` when the query does not identify enough structure to
    scope on — the caller then runs no structural arm.

    The shape is ``{"must": [...], "should": [...]}`` — a section pin in
    ``must`` (an AND) combined with the Act identity ORs in ``should``. The
    ``should`` clause must sit at the **top level of the filter**: nesting it
    inside ``must`` is rejected by qdrant-client's model (an item of ``must``
    must be a ``FieldCondition`` or a ``Filter``, and ``min_should`` expects a
    ``MinShould`` model in this client version). The top-level form needs no
    explicit ``min_should`` — Qdrant defaults it to 1, so the semantics are
    ``section_number == N AND (act_name == Act OR document_title == Act)``.

    Verified live: filtering section 8 of the FSSAI Act with this shape
    returns 4 points, and the same shape scoped to the Air Act returns 0 —
    the ``should`` clause is applied, not ignored.

    ``section`` arrives already normalised to its base number by
    :func:`app.rag.retrieval.identifier.detect_section` (it captures
    ``\\d{1,4}`` only), so it matches the ``section_number`` payload field as
    the backfill stamped it.
    """
    if not act and not section:
        return None
    built: dict[str, Any] = {}
    if section:
        built["must"] = [{"key": "section_number", "match": {"value": section}}]
    if act:
        built["should"] = [{"key": field, "match": {"value": act}} for field in ACT_SCOPE_FIELDS]
    return built


def merge_caller_filters(
    base: dict[str, Any] | None, caller_filters: dict[str, Any] | None,
) -> dict[str, Any]:
    """AND a base Qdrant-style filter with the caller's payload filters.

    ``base`` is the section/act structural arm filter (e.g. ``{"must": [...],
    "should": [...}]}`` from :func:`section_scope_filter` — a section pin plus
    the Act identity). ``caller_filters`` are the payload filters parsed from
    the query (section number, subsection, authority) — plus anything the HTTP
    body supplied. Combining them with AND means the structural arm can never
    retrieve outside the caller's declared scope: a chunk must satisfy the
    structural requirement AND all of the caller's payload conditions.

    The caller's filters are always a flat ``{field: value}`` dict produced by
    ``QueryParser`` (or empty), so they are expanded into ``must`` clauses
    via :meth:`DenseRetriever._build_filter` and stacked onto the base's
    ``must``. The base's ``should`` (Act identity) is always preserved. If the
    caller itself pins ``section_number`` (a section query), that clause is
    authoritative: the base's single-number pin is dropped so that a caller
    OR-list (e.g. "Sections 55, 56, 58" -> ``["55", "56", "58"]``) does not
    AND against ``section_number == 55`` and silently match nothing.

    Args:
        base: The section-scope filter. ``None``/empty is tolerated so a
            caller with no structural scope can still pass its own filters.
        caller_filters: The filters ``retrieve()`` was called with, possibly
            ``None`` or empty.

    Returns:
        A filter dict; ``{}`` only when there is nothing to express.
    """
    # Imported lazily: ``hybrid_retriever`` imports this module, so a
    # module-level import here would be circular.
    from app.rag.retrieval.dense_retriever import DenseRetriever

    base = base or {}
    caller = caller_filters or {}
    if not caller:
        return dict(base)

    if _is_shaped_filter(caller):
        # Already in Qdrant's boolean form. Flattening it onto the base's
        # top-level ``should`` would turn the caller's AND into an OR, so it
        # travels as a single nested ``filter`` clause instead.
        caller_must: list[Any] = [{"filter": caller}]
    else:
        caller_must = list(DenseRetriever._build_filter(dict(caller)).get("must", []))

    merged: dict[str, Any] = dict(base)
    base_must = list(base.get("must", []))
    if any(_is_section_clause(c) for c in caller_must):
        # The caller's section pin is authoritative. Drop only the arm's own
        # section clause -- dropping the whole ``must`` key, as an earlier
        # revision did, discarded the caller's clauses along with it and left
        # no ``must`` at all. An OR-list of sections ("Sections 55, 56, 58")
        # ANDed against a single ``section_number == 55`` matches nothing.
        base_must = [c for c in base_must if not _is_section_clause(c)]
    must = base_must + caller_must
    if must:
        merged["must"] = must
    elif "must" in merged:
        merged.pop("must")
    # The base's ``should`` (Act identity) is preserved untouched.
    return merged


def _is_shaped_filter(value: Any) -> bool:
    """True when ``value`` is already a Qdrant boolean filter, not a flat map."""
    return isinstance(value, dict) and any(k in value for k in ("must", "should", "must_not", "min_should"))


def _is_section_clause(clause: Any) -> bool:
    """True when ``clause`` is a Qdrant condition pinning ``section_number``."""
    return isinstance(clause, dict) and clause.get("key") == "section_number"


def section_scope_meta(query: str) -> dict[str, Any] | None:
    """Detect the Act/section in ``query`` and describe the structural arm to run.

    Returns ``None`` when no structural arm should fire, otherwise::

        {"mode": "act_section" | "act_only",
         "act": str | None,
         "section": str | None,
         "filter": {...}}            # Qdrant filter dict

    ``mode`` records which of the two trigger classes fired so callers and
    tests can assert coverage without re-deriving it.
    """
    _lexical, meta = identifier_query(query)
    act = meta.get("act")
    section = meta.get("section")
    built = section_scope_filter(act, section)
    if built is None:
        return None
    return {
        "mode": "act_section" if (act and section) else ("act_only" if act else "section_only"),
        "act": act,
        "section": section,
        "filter": built,
    }
