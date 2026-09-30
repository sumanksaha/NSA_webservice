# ADR-0009: Tiered statutory provision extraction engine

- **Status:** Accepted (Design & Architecture Spec; Implementation scheduled)
- **Date:** 2026-09-27
- **Context:** Ingestion & retrieval subsystems (`app/rag/`), Legal Metadata Engine (`app/metadata_extractor/`), and Legal Knowledge Graph (`kg/`)
- **Deciders:** Architecture review
- **Related terms in `CONTEXT.md`:** `Provision currency`, `QueryUnderstanding`, `FSO strategic advisory`, `DeterministicActSelector`

---

## 1. Context

Statutory provisions in the food safety regulatory domain (the Food Safety and Standards Act 2006, FSS Regulations, gazette notifications, and state amendments) carry high structural and deontic density:
1. **Hierarchical structure:** Sections, sub-sections, clauses, sub-clauses, provisos, and explanations.
2. **Deontic modalities:** Mandatory obligations on Food Business Operators (FBOs), statutory powers granted to Food Safety Officers (FSOs), prohibitions, and penal schedules (§§ 50–67).
3. **Complex relational semantics:** Enabling clauses (*"as may be prescribed"*), subordinations (*"subject to the provisions of"*), non-obstante overrides (*"notwithstanding anything contained in"*), and procedural linkages (*"read with"*).
4. **Temporal dynamics:** Continuous amendments, substitutions, and omissions promulgated through Central and State gazettes.

### Existing Limitations
Currently, provision extraction in `NSA_webservice` relies predominantly on static regular expressions (`app/rag/retrieval/reference_extractor.py`, `app/metadata_extractor/extractors/`) and generic token NER (`app/metadata_extractor/ner.py`, `app/rag/entity_extractor.py`). This creates key bottlenecks:
- **Brittleness under OCR & typography variations:** Punctuation drops, inline numbering, and degraded scans frequently break regex matchers.
- **Absence of deontic/norm extraction:** The system detects provision references (e.g., `Section 31`), but cannot isolate whether the text mandates a duty, defines a standard, or imposes a penalty.
- **Inability to parse compound penalty schedules:** FSO advisory requires exact penalty thresholds (§ 51 ₹5L, § 52 ₹3L, § 55 ₹2L, § 58 ₹2L, § 63 up to 6 months + ₹5L) to parameterize the game-theoretic payoff matrix without manual hardcoding.
- **Truncated chunk boundaries:** Vector indexing at arbitrary token splits separates provisos from their parent operative sections.

### Core Architecture Requirements
1. **Zero-overhead fast path:** Clean, standard legal texts must be parsed in sub-millisecond time.
2. **Fail-closed & offline-safe:** Absence of optional dependencies (`scikit-learn`, `torch`, external LLM APIs) must gracefully degrade to deterministic rule-based parsing without crashing.
3. **Immutable Chunk ID Contract:** Provision re-segmentation and extraction must never mutate existing Qdrant `chunk_id` values, preserving retrieval caches, gold evaluation benchmarks, and hash-chained audit trails.

---

## 2. Decision

We establish a **Tiered Hybrid Provision Extraction Engine** encapsulated in the new package `app/rag/provision_extractor/` and integrated across ingestion, retrieval, and Knowledge Graph pipelines.

```mermaid
flowchart TD
    InputText["Raw Text / Document Chunk / Gazette"] --> Stage1["1. Candidate Generator<br/>(Boundary proposals: Sections, Rules, Schedules)"]
    Stage1 --> Stage2["2. Feature Extraction<br/>(Position, capitalization, punctuation, noise likelihood)"]
    Stage2 --> TierSelector{"Extraction Tier Selection"}
    
    TierSelector -- "Tier 1: High Confidence" --> FastPath["Tier 1: Deterministic Rules & Normalizer<br/>(0ms, zero external deps)"]
    TierSelector -- "Tier 2: Ambiguous / Dotted" --> MLPath["Tier 2: Statistical Disambiguator<br/>(Scikit-Learn Logistic Regression / Heuristic Weights)"]
    TierSelector -- "Tier 3: Complex / Degraded" --> LLMPath["Tier 3: Schema-Constrained LLM Extractor<br/>(GroundedLLMClient Pydantic Schema)"]
    
    FastPath --> Stage3["3. Provision Isolator & Deontic Classifier<br/>(Normalize spans, extract modalities, map penalties)"]
    MLPath --> Stage3
    LLMPath --> Stage3
    
    Stage3 --> Output["ProvisionRecord Stream"]
    Output --> Qdrant["Qdrant Payload Stamping<br/>(provision_spans, modalities)"]
    Output --> Neo4j["Neo4j Legal KG<br/>(Provision nodes, RELATIONS)"]
    Output --> Advisory["FSO Advisory & Notice Synthesis<br/>(Statutory Anchors & Penalty Schedules)"]
```

### 2.1 The 3-Tier Cascade Model

1. **Tier 1 — Deterministic Fast-Path (Always Active):**
   - Grammar-aware regular expression engine combining section base parsers, dotted regulation patterns (`\d+\.\d+\.\d+`), and em-dash legal headers (`NN. Title.—`).
   - Handles standard, clean statutory text with $O(1)$ overhead.
2. **Tier 2 — Statistical ML Disambiguator (Optional In-Process):**
   - Lightweight logistic regression classifier trained on boundary features (line-start status, punctuation signatures, page-number proximity, gazette running-head indicators, year-token patterns).
   - Lazily imported via `scikit-learn`; automatically falls back to deterministic heuristic weights when `scikit-learn` or the model artifact (`models/provision_boundaries.joblib`) is unavailable.
3. **Tier 3 — Schema-Constrained LLM Extractor (Gated Fallback):**
   - Activated via `GroundedLLMClient` only for complex gazette notifications, multi-line nested provisos, or severely degraded OCR text where Tier 1 and Tier 2 confidence falls below `0.70`.
   - Constrained to emit strictly validated JSON satisfying the `ProvisionRecord` schema.

### 2.2 Domain & Data Contract

The engine emits immutable, structured `ProvisionRecord` instances:

```python
class DeonticModality(str, Enum):
    OBLIGATION = "obligation"          # "shall ensure", "must maintain"
    PROHIBITION = "prohibition"        # "no person shall manufacture/sell"
    POWER = "power"                    # "Food Safety Officer may seize"
    PENALTY = "penalty"                # "liable to penalty not exceeding..."
    DEFINITION = "definition"          # "means and includes"
    EXEMPTION = "exemption"            # "Provided that nothing shall apply..."
    PROCEDURE = "procedure"            # "sample shall be sent to food analyst"

class ProvisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    provision_id: str                  # Canonical: e.g. "FSSA_2006::s31(2)(a)"
    act_name: str                      # Canonical Act / Regulation title
    family_id: str                     # Grouping ID: "FSSA_2006::31"
    section: str                       # Base section / rule / regulation number
    subsection: list[str] = []         # Ordered subsection chain
    clause: list[str] = []             # Ordered clause / subclause chain
    title: str = ""                    # Provision header / margin title
    text: str                          # Operative isolated provision text
    modality: DeonticModality          # Primary legal modality
    subject_entity: str | None = None  # FBO, FSO, Food Analyst, Commissioner
    penalty_max_inr: float | None = None # Numerical fine ceiling if applicable
    imprisonment_max_months: int | None = None # Imprisonment limit if applicable
    cross_references: list[str] = []   # Cited sections / rules ("read with § 58")
    is_proviso: bool = False           # Flag for conditional exception clauses
    source_chunk_ids: list[str] = []   # Provenance links to underlying chunks
    confidence: float                  # Extraction confidence [0.0 - 1.0]
    extraction_tier: str               # "tier1_regex" | "tier2_ml" | "tier3_llm"
```

### 2.3 Integration Seams & Feature Flags

1. **Configuration Seam ([`app/shared/config.py`](file:///C:/github/NSA_webservice/app/shared/config.py)):**
   - `PROVISION_EXTRACTOR_ENABLED` (boolean, default: `true`).
   - `PROVISION_EXTRACTOR_MODE` (enum: `rules` | `hybrid` | `llm_fallback`, default: `rules`).
   - `PROVISION_EXTRACTOR_MIN_CONFIDENCE` (float, default: `0.70`).
2. **Ingestion Seam ([`kg/corpus_ingestion.py`](file:///C:/github/NSA_webservice/kg/corpus_ingestion.py), `app/rag/ingestion.py`):**
   - Provision extraction runs as an enrichment stage following initial chunking.
   - Attaches `provision_spans` and `deontic_modality` to chunk payloads in Qdrant.
3. **Retrieval & Advisory Seam ([`app/rag/retrieval/`](file:///C:/github/NSA_webservice/app/rag/retrieval/), [`app/rag/advisor/`](file:///C:/github/NSA_webservice/app/rag/advisor/)):**
   - `QueryUnderstanding` resolves natural language intent directly to canonical `provision_id` targets.
   - `DeterministicActSelector` consumes structured `penalty_max_inr` and `modality` fields to evaluate statutory consequence payoffs.

---

## 3. Consequences

### Positive
- **High Semantic Grounding:** Enables exact sub-section and proviso citation in statutory notices (u/s 32 Improvement Notices) and adjudication complaints.
- **Robustness Across Diverse Formats:** Unifies parsing of Acts, Central Gazette Notifications, and State Food Safety Orders under a single resilient seam.
- **Auditability & Zero Regression:** Strict adherence to the `frozen=True` Pydantic models ensures thread-safety, deterministic serialization, and full testability without network dependencies.
- **Knowledge Graph Precision:** Directly populates Neo4j edges (`MANDATES`, `PROHIBITS`, `PENALIZES`, `AMENDS`, `EXEMPTS_FROM`) without heuristic post-processing.

### Trade-Offs & Mitigations
- **Optional Dependency Isolation:** `scikit-learn` and model artifacts are loaded lazily. If missing, the engine logs a warning and routes through Tier 1 heuristic disambiguation with zero downtime.
- **Corpus Backfill Safety:** Corpus re-indexing is executed via a dry-run diff script (`scripts/backfill_provision_extraction.py`). `chunk_id` hashes remain unmodified to avoid invalidating existing vector embeddings and test caches.

---

## 4. Verification & Testing Strategy

1. **Unit & Golden Fixture Suite (`tests/test_provision_extractor.py`):**
   - Verification across 100 gold-standard provision samples spanning:
     - Standard Act sections (`Section 31(2)(a)`).
     - Multi-tier Dotted Regulations (`Regulation 2.3.1.4`).
     - Conditional provisos and compound penalty provisions (§§ 50, 51, 52, 59, 63).
2. **Fallback Verification:**
   - Unit test asserting full functionality when `scikit-learn` is monkeypatched as absent (`test_rules_fallback_without_sklearn`).
3. **Round-Trip Gold Resolution:**
   - All emitted `provision_id` strings must parse through `evaluation/benchmark.py::_section_from_id` and match benchmark gold targets.
