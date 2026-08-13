# Legal KG — Implementation Plan

> **Phase 3 — Pilot ingestion implementation plan**

## Overview

Build a foundational multi-domain legal Knowledge Graph in the existing Neo4j Aura instance that:

1. Keeps FSSAI/food safety as the primary domain with additional West Bengal / Indian law domains
2. Represents relationships between laws, sections, rules, regulations, authorities, obligations, offences, penalties
3. Allows cross-domain legal retrieval
4. Preserves source provenance (Provision → Chunk → Document → Source)
5. Supports temporal/legal-status information
6. Can be queried from an LLM via structured JSON
7. Can be enriched later without schema redesign

## Deliverables

```
kg/
├── __init__.py              # Module init
├── schema.py                # Constraint + index setup Cypher (idempotent)
├── domain_manifest.py       # Canonical domain/instrument/provision registry
├── ingestion.py             # Pilot ingestion: LegalDocument/LegalChunk → Neo4j
├── queries.py               # Cypher query functions (retrieval contract)
├── validation.py            # Structural + legal validation queries
├── models.py                # Dataclasses: LegalInstrument, LegalProvision, etc.
└── test_pilot_kg.py         # 25 acceptance tests (5 criteria × 5 sub-tests)
```

## Phase 3 Execution

### Step 1: Schema setup (`schema.py`)
- Create constraints for `Act`, `Regulation`, `Notification`, `LegalProvision`, `Authority`, `LegalDomain`, `LegalConcept`, `Chunk`, `Document`
- Create indexes for fast lookups (domain, status, provision_id, instrument_id)
- Idempotent via `IF NOT EXISTS` — safe to run multiple times
- **Does NOT touch existing case-file labels** (Case, FBO, etc.)

### Step 2: Domain manifest (`domain_manifest.py`)
- Canonical `LegalDomain` nodes: FOOD_SAFETY, ANIMAL_SLAUGHTER, ENVIRONMENT_POLLUTION, MUNICIPAL, PUBLIC_HEALTH, BUSINESS_CIVIL, LAND_PREMISES
- Canonical `Authority` nodes: FSSAI, WBPCB, KMC, CPCB, MOHFW, Courts
- Canonical `LegalConcept` nodes: FoodBusiness, Slaughterhouse, Wastewater, Premises, Licence, etc.
- Canonical instrument IDs: FSS_ACT_2006, ENV_PROTECTION_ACT_1986, etc.
- Pilot corpus: one instrument per domain (7 instruments total)

### Step 3: Ingestion (`ingestion.py`)
- Read from local `LegalDocument` / `LegalChunk` DB tables (existing FSSAI corpus: 29 docs, 12,819 chunks)
- Read from domain manifest for non-FSSAI instruments (structured provision stubs)
- Create `Act`/`Regulation`/`Notification` nodes with full §7.1 metadata
- Create `LegalProvision` nodes from chunk section headers + section_number
- Create `Chunk` nodes linked to `LegalProvision` via `SUPPORTED_BY`
- Create `Document` nodes linked to chunks via `HAS_CHUNK`
- Wire `CONTAINS` / `HAS_SUBSECTION` / `AMENDED_BY` / `BELONGS_TO_DOMAIN` / `ENFORCED_BY` edges
- Apply entity resolution (canonical names, aliases)
- **Memory-bounded**: process one document at a time, batch writes (1000 nodes/transaction)
- **Provenance chain**: every provision → chunk → document → source

### Step 4: Retrieval queries (`queries.py`)
- `get_provision(provision_id)` → full node + provenance chain
- `get_instrument(instrument_id)` → instrument + all provisions
- `get_related_provisions(provision_id)` → cross-domain relationships
- `get_cross_domain_laws(concept)` → all domains where concept appears
- `get_applicable_laws(business_activity)` → provisions for a concept
- `get_authorities(provision)` → authorities linked to a provision
- `get_enforcement_powers(provision)` → offences, penalties, notices
- `get_source_evidence(provision)` → chunk + document + URL
- `get_current_provisions(concept)` → only current (non-repealed) provisions
- `get_domain_provisions(domain)` → all provisions in a domain

### Step 5: Validation (`validation.py`)
- Structural: orphan provisions, provisions without instruments, chunks without sources
- Legal: wrong-parent links, unsupported authority relationships
- Cross-domain: test queries from §20 of the task spec
- Confidence filtering: verify no relationship has `confidence: 0` unless source is `unknown`

### Step 6: Tests (`test_pilot_kg.py`)
- **Test 1**: Domain separation — FSSAI query does not return land-revenue provisions
- **Test 2**: Cross-domain retrieval — slaughterhouse returns FOOD_SAFETY + ANIMAL_SLAUGHTER + ENVIRONMENT + MUNICIPAL
- **Test 3**: Provenance — every provision traces to Instrument → Chunk → Document
- **Test 4**: Authority identification — provision → authority is source-supported
- **Test 5**: Temporal correctness — current vs repealed provisions are distinguishable
- **Test 6**: No unsupported inference — no `CONFLICTS_WITH` / `OVERRIDES` / `APPLIES` relationships created

## Phase 3 Non-Goals

- NOT ingesting the full 12,819-chunk corpus into Neo4j at pilot scale (only section headers + key provisions)
- NOT adding Pydantic/Instructor for structured LLM output (not in scope)
- NOT implementing IRAC / counterarguments / game theory (not in the existing specification)
- NOT replacing Qdrant vector retrieval — KG is the structured reasoning layer alongside it
- NOT modifying existing `KnowledgeGraphEngine` (case-file extraction) — completely separate
