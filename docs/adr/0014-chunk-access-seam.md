# ADR-0014: One chunk access seam for object and payload shapes

- **Date:** 2026-10-20
- **Status:** Accepted
- **Context:** `app/rag/retrieval/`, `app/rag/provision_extractor/`

## Context

Retrieval stages accept two interchangeable chunk shapes:

- `RetrievedChunk` (and other chunk-like objects), and
- the serialized `to_dict()` / Qdrant payload dict.

Eleven modules each implemented their own object/dict branch:

- `evidence_selector.py` — `_chunk_id`, `_chunk_text`
- `decision_selector.py` — `_chunk_id`, plus inline reads in `_candidate` / `_take`
- `legal_reranker.py` — `_cid`, `_ctext`, `_cscore`, `_set_score`
- `parent_reconstruction.py` — `_f`, `_cid`, `_ctext`
- `validation.py` — `_ctext`
- `provision_metadata.py`, `legal_identity.py` — `_chunk_field`
- `provision_extractor/isolator.py` — `_chunk_field`, `_set_chunk_field`
- `chunk_quality.py` — `_value`
- `evaluation/textmatch.py` — `chunk_text`, `chunk_id`

The copies had drifted: missing ids returned `None` in some paths and `""` in
others; `EvidenceSet.chunk_ids` only worked for object chunks; score coercion
caught exceptions in some copies but not all. A payload dict could therefore
behave differently depending on which stage read it.

## Decision

`app/rag/retrieval/chunk.py` is the **single access policy**:

```python
field(chunk, name, default=None) -> Any
text(chunk, default="") -> str
chunk_id(chunk, default=None) -> str | None
score(chunk, default=0.0) -> float
set_field(chunk, name, value) -> None
set_score(chunk, value) -> None
```

Contract:

- dict payloads are read with `.get`; everything else with `getattr`;
- `text` is always a `str`;
- `chunk_id` is a `str` when present, otherwise the caller's default (`None`
  for identity/dedup paths, `""` for string-key paths);
- `score` coerces to `float` and returns the default for missing/malformed
  values;
- writes go through `set_field` (dict assignment or attribute write).

`RetrievedChunk`'s interface is unchanged. `canonical_unit_id` remains in the
evidence selector because it owns legal-unit parse rules, not field access.

## Consequences

- **Positive:** one place to fix dict/object parity; all stages now agree.
- **Positive:** `EvidenceSet.chunk_ids` works for payload dicts, which the
  agent pipeline passes through.
- **Positive:** score/ID coercion is consistent across rerankers, selectors and
  provision extraction.
- **Risk:** none material — defaults preserve the previous public behaviour at
  each call site; the only changes are shapes that previously failed or
  diverged.
- **Boundary:** this is field access only. Parsing (legal identity, metadata,
  section chains) stays in its owning module.

See `docs/RAG_ARCHITECTURE_IMPROVEMENT_PLAN.md` (Candidate 4) and
`tests/test_chunk_access.py`.
