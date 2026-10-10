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
    the query (document type, instrument, authority, etc.) — plus anything the
    HTTP body supplied. Combining them with AND means the structural arm can
    never retrieve outside the caller's declared scope: a chunk must satisfy
    the structural requirement AND all of the caller's payload conditions.

    ``must`` clauses from both sides are concatenated (every item must match).
    ``should`` clauses are also concatenated: each side's disjunctions are
    independent, so the union keeps both sides satisfiable without loosening
    the ``must`` side. The resulting shape stays at the Qdrant model's
    ``{"must": [...], "should": [...]}`` top-level form.
    """
    base = base or {}
    caller = caller_filters or {}
    result: dict[str, Any] = {}
    # Every ``must`` item from both sides must hold together.
    base_must = base.get("must", [])
    caller_must = caller.get("must", [])
    if base_must or caller_must:
        result["must"] = [*base_must, *caller_must]
    # Independent disjunctions on each side: union keeps both satisfiable.
    base_should = base.get("should", [])
    caller_should = caller.get("should", [])
    if base_should or caller_should:
        result["should"] = [*base_should, *caller_should]
    return result


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
