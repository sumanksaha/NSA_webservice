# Parallel Legal Structure & Evidence Layer

## Overview

This document describes the parallel legal structure and evidence layer built
**independently** of the CE reranking experiment (K=500 pool, CE weight 0.5,
CE head 30).  The layer provides canonical legal identity, hierarchy,
cross-reference graph, temporal validity, provision versioning, and
evidence-set construction — all feature-flagged and independently evaluable.

The current production baseline remains unchanged.  These components are
designed to be integrated **after** the main reranking experiments finish,
testing whether better legal structure + better ranking + better evidence
selection can move the system beyond the current 42.7% R@10 ceiling.

## Data Flow

```
Ranked Candidates (from CE reranker)
       ↓
parse_legal_identity(chunk) → LegalIdentity
       ↓
extract_references(chunk.text) → [Reference]  (HIGH/MEDIUM/LOW confidence)
       ↓
ReferenceGraph.seed_from_chunks(chunks) → in-memory graph
       ↓
is_valid(chunk, query_date) → ValidityResult  (valid / invalid / unknown)
       ↓
extract_provision_version(chunk) → ProvisionVersion
       ↓
select_evidence_set(query, chunks) → EvidenceSet  (2–5 complementary provisions)
       ↓
evaluate_evidence_set(es, gold_ids) → EvidenceSetRecall, Precision, F1
```

## Modules

### 1. Legal Identity (`app/rag/retrieval/legal_identity.py`)

**Purpose:** Normalized legal identity for every legal chunk.

**Canonical identifier format:** `ACT::SECTION::SUBSECTION::CLAUSE`

**Key functions:**
- `parse_legal_identity(chunk) -> LegalIdentity` — parses from `RetrievedChunk` payload + text
- `LegalIdentity.canonical_id()` — builds canonical identifier from non-None fields
- `LegalIdentity.to_dict()` — full serialization

**Fields:** `act`, `act_alias`, `chapter`, `part`, `section`, `subsection`, `clause`,
`rule`, `schedule`, `authority`, `jurisdiction`, `effective_from`, `effective_to`,
`status`, `source_document`, `version`, `raw_section`

**Principle:** Never fabricates legal identifiers.  Missing fields stay `None`/empty.

**Feature flag:** `ENABLE_LEGAL_IDENTITY` (default: true)

### 2. Legal Hierarchy (`app/rag/retrieval/legal_hierarchy.py`)

**Purpose:** Act → Chapter → Section → Subsection → Clause hierarchy with
bidirectional traversal.

**Key functions:**
- `parse_section_chain("31(2)(a)") -> ["31", "2", "a"]`
- `hierarchy_depth("31(2)(a)") -> 5`
- `exact_section_match(a, b)`
- `same_act(a_act, b_act)`
- `same_chapter(chunk_a, chunk_b)`
- `same_section_family(a, b)`
- `parent_child(a, b)`
- `sibling(a, b)`
- `adjacent_section(a, b)`
- `subsection_relationship(a, b)`
- `hierarchy_proximity(a, b) -> [0, 1]` (proximity score)

**Feature flag:** `ENABLE_HIERARCHY` (default: true)

### 3. Reference Extractor (`app/rag/retrieval/reference_extractor.py`)

**Purpose:** Extract legal cross-references from text with confidence levels.

**Reference patterns:**
- `Section X`, `Sec. X`, `s. X`, `u/s X`
- `Section X(1)`, `Section X(1)(a)` (subsection chains)
- `Rule X`, `Schedule X`, `Chapter X`
- Textual relations: "subject to", "as provided under", "in accordance with",
  "read with", "notwithstanding", "referred to in", "as specified in",
  "except as provided by", etc.

**Confidence levels:**
- `HIGH` — explicit Section N with subsection chain
- `MEDIUM` — explicit Section N without subsection, or Rule/Schedule/Chapter
- `LOW` — textual relation pattern without explicit section

**Key functions:**
- `extract_references(text, act_hint=None, min_confidence=LOW) -> [Reference]`
- `high_confidence_refs(refs) -> [Reference]`
- `resolve_ref_to_provision(ref) -> str | None` (advisory, uses section registry)

**Feature flag:** `ENABLE_REFERENCE_EXTRACTION` (default: true)

### 4. Reference Graph (`app/rag/retrieval/reference_graph.py`)

**Purpose:** Graph of cross-reference relationships between legal provisions.

**Edge types:**
- `references` — general cross-reference
- `exception` — exception relationship
- `subject_to` — subject to
- `defined_by` — definition reference
- `complements` — complementary provision
- `depends_on` — depends on

**Key functions:**
- `ReferenceGraph` — in-memory graph with `add_edge()`, `neighbors()`, `inbound()`
- `ReferenceGraph.seed_from_chunks(chunks)` — extract refs from chunk text
- `ReferenceGraph.seed_from_neo4j(provision_id)` — best-effort Neo4j enrichment
- `expand_references(document_id, depth=1) -> [ReferenceEdge]` — BFS traversal
- `expand_candidates(chunks, top_k=10, depth=1) -> [str]` — candidate expansion

**Integration route (spec §7):**
```
normal retrieval + identifier retrieval + cross-reference expansion
```

**Feature flag:** `ENABLE_REFERENCE_EXPANSION` (default: **false** — production baseline unchanged)

### 5. Temporal Validity (`app/rag/retrieval/temporal_validity.py`)

**Purpose:** Determine if a provision was valid at a query date.

**Validity statuses:**
- `VALID` — current and date range valid
- `INVALID` — repealed/superseded or date outside effective range
- `UNKNOWN` — insufficient evidence (never fabricates)

**Key functions:**
- `is_valid(document_id, query_date, chunk=None) -> ValidityResult`
- `temporal_validity_score(document_id, query_date) -> [0, 1]`
  - `1.0` = explicitly valid, `0.5` = uncertain, `0.0` = invalid
- Best-effort Neo4j enrichment (degrades to `unknown` when unavailable)

**Principle:** Never infers validity when the corpus lacks sufficient evidence.

**Feature flag:** `ENABLE_TEMPORAL_FILTER` (default: true)

### 6. Provision Versions (`app/rag/retrieval/provision_versions.py`)

**Purpose:** Detect historical versions of the same provision.

**Key functions:**
- `extract_provision_version(chunk) -> ProvisionVersion`
- `group_versions(chunks) -> {family_id: VersionFamily}`
- `build_provision_family_id(act, section) -> str`
- `is_current_version(chunk) -> bool | None`

**Version detection:**
1. Explicit `version`/`provision_version` field
2. Sub-section text parsing for amendment markers ("as amended by", etc.)
3. Effective date grouping
4. Neo4j `AMENDS`/`REPLACES`/`REPEALS` edges (best-effort)

**Feature flag:** `ENABLE_PROVISION_VERSIONS` (default: true)

### 7. Evidence Selector (`app/rag/retrieval/evidence_selector.py`)

**Purpose:** Post-reranking evidence-set construction — select 2–5 complementary
provisions from the ranked candidate pool.

**Evidence types:**
- `primary_provision` — matches query section
- `definition` — contains "means", "includes"
- `exception` — contains "except", "notwithstanding"
- `penalty` — contains "penalty", "fine", "imprisonment"
- `cross_reference` — contains section references to other sections
- `authority` — contains "authority", "power"
- `subsection` — subsection of primary section
- `adjacent_section` — numerically adjacent section

**Algorithm (deterministic, no ML):**
1. Classify each chunk's evidence type
2. Score: `ce_score * 0.6 + type_priority * 0.2 + complementarity * 0.2 - redundancy * 0.3`
3. Greedy selection: always pick primary first, then maximize complementarity,
   skip high-redundancy duplicates
4. Ensure minimum size

**Scoring:**
- **Redundancy** [0,1]: 1.0 = exact section duplicate, based on token Jaccard
- **Complementarity** [0,1]: 1.0 = different evidence type or different section family

**Feature flag:** `ENABLE_EVIDENCE_SELECTOR` (default: **false** — opt-in)

### 8. Evidence Metrics (`app/rag/retrieval/evidence_metrics.py`)

**Purpose:** Metrics for evidence-set quality, separate from R@10.

**Key metrics:**
- `Evidence Set Recall` = |selected ∩ gold| / |gold| (per spec §13)
- `Evidence Set Precision` = |selected ∩ gold| / |selected|
- `Evidence Set F1` = harmonic mean
- `Evidence Coverage@K` = recall when selecting top-K ranked items

**Batch evaluation:**
- `evaluate_evidence_batch(evidence_sets, gold_sets) -> EvidenceBatchResult`

## Schema

### Legal Identity Schema

```
act: str | None          — canonical Act name
act_alias: str | None    — original act_name if differs from canonical
chapter: str | None      — chapter number
part: str | None         — part number
section: str | None      — base section number
subsection: list[str]    — subsection chain (e.g. ["2"])
clause: list[str]        — clause chain (e.g. ["a"])
rule: str | None         — rule number
schedule: str | None     — schedule reference
authority: str | None    — issuing authority
jurisdiction: str | None
effective_from: str | None
effective_to: str | None
status: str | None       — current, repealed, suppressed
source_document: str | None
version: str | None
```

### Reference Graph Schema

```
nodes: chunk_id / document_id (source, target)
edges: ReferenceEdge {
  source_document: str
  target_document: str
  relationship: str  (references, exception, subject_to, etc.)
  confidence: HIGH|MEDIUM|LOW
  source: "text"|"graph"
  evidence: str
  target_provision_id: str | None
  depth: int
}
```

### Temporal Validity Model

```
provision_status: current|repealed|superseded|unknown
effective_from: date | None
effective_to: date | None
query_date: date
→ valid (current + date in range)
→ invalid (repealed/superseded, or date outside range)
→ unknown (no sufficient metadata)
```

## Feature Flags

| Flag                       | Default | Description                                         |
| -------------------------- | ------- | --------------------------------------------------- |
| `ENABLE_LEGAL_IDENTITY`    | true    | Legal identity parsing                              |
| `ENABLE_HIERARCHY`         | true    | Hierarchy relationship functions                    |
| `ENABLE_REFERENCE_EXTRACTION` | true | Extract references from chunk text                 |
| `ENABLE_REFERENCE_EXPANSION` | false | Graph-based candidate expansion (opt-in for A/B)   |
| `ENABLE_TEMPORAL_FILTER`   | true    | Temporal validity checking                          |
| `ENABLE_PROVISION_VERSIONS`| true    | Historical version detection                        |
| `ENABLE_EVIDENCE_SELECTOR` | false   | Evidence-set construction (opt-in for A/B)          |

## Evaluation Matrix

| Query class       | Primary mechanism                          |
| ----------------- | ------------------------------------------ |
| Penalty           | hierarchy + CE                             |
| Direct provision  | CE + legal identity                        |
| Exception         | hierarchy + relationship                   |
| Obligation        | CE + hierarchy                             |
| Procedure         | hierarchy + authority                      |
| Authority         | authority/jurisdiction                     |
| Prohibition       | query-type-aware ranking                   |
| Cross-reference   | identifier + graph                         |
| Offence           | dedicated retrieval                        |
| Multi-provision   | evidence-set selection                     |

## Integration Points

When the CE/hard-negative experiment finishes, the integration plan is:

1. **Baseline** → current CE reranker (R@10 ≈ 42.7%)
2. **+ legal identity** → attach canonical identifiers to candidates
3. **+ temporal validity** → filter invalid provisions, score valid ones
4. **+ reference expansion** → expand candidate pool via cross-reference graph
5. **+ evidence-set selection** → select complementary provisions for LLM context

Each layer is independently toggleable for ablation studies.

## Test Count

- `tests/test_legal_identity.py` — 20 tests (identity + hierarchy)
- `tests/test_reference_graph.py` — 11 tests (extraction + graph)
- `tests/test_temporal_validity.py` — 19 tests (validity + versions)
- `tests/test_evidence_selector.py` — 23 tests (selection + metrics)
- `tests/test_evidence_structure_integration.py` — 10 tests (end-to-end)
- **Total: 83 new tests, all passing**

## Current CE Experiment (untouched)

```
K=500 pool ceiling: 90.7%
R@10: ~42.7%
CE weight: 0.5
CE head: 30 (updated from 20)
Hierarchy-aware reranking: enabled
Query-type-aware reranking: enabled
```

The parallel layer does NOT modify any of the above.
