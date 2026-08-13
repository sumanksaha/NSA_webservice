# LEGAL_KG_SCHEMA.md — Multi-Domain Legal Knowledge Graph Ontology

> **Phase 1 — Ontology design**  
> This schema defines the node labels, relationship types, properties, and constraints for the legal KG in Neo4j.

---

## 1. Design Principles

### 1.1 Node-Type Granularity

**Use the minimum set necessary for the current corpus, but design for future expansion.** This means we use a small set of primary node labels for legal instruments and provisions, with semantic concept nodes that are shared across domains.

### 1.2 No Hallucinated Relationships

Relationships are only created when:
- The source document explicitly references another instrument/provision
- A known legal hierarchy exists (Section → Subsection, Act → Section)
- The metadata extractor or classifier assigns a source-supported relationship

No inferred logical connections (`CONFLICTS_WITH`, `OVERRIDES`, `APPLIES`) unless the source text establishes them.

### 1.3 Provenance Chain

Every `LegalProvision` node links back to its `Chunk` node via `SUPPORTED_BY`, and every `Chunk` links to its `Document` via `HAS_CHUNK`. This preserves the trace chain: `Provision → Chunk → Document → Source`.

### 1.4 Temporal Model

Provisions are versioned: `Provision_v1` and `Provision_v2` coexist with an `AMENDS` relationship. The `effective_from` / `effective_to` / `status` fields on each provision allow time-bounded queries.

---

## 2. Node Types

### 2.1 Legal Instruments (7 labels)

| Label | Domain | Description | Key Property |
|---|---|---|---|
| `Act` | All | Primary legislation (e.g., FSS Act 2006, Water Act 1974) | `instrument_id` |
| `Rule` | All | Subordinate to an Act (e.g., FSS (Central) Rules 2011) | `instrument_id` |
| `Regulation` | All | FSSAI regulations, environmental rules | `instrument_id` |
| `Notification` | All | FSSAI notifications, Gazette notifications | `instrument_id` |
| `Order` | All | Tribunal/court orders, government orders | `instrument_id` |
| `Circular` | All | Advisory circulars from authorities | `instrument_id` |
| `Guideline` | All | Non-binding guidance documents | `instrument_id` |
| `Judgment` | All | Court/tribunal judgments | `instrument_id` |

**All inherit the same core properties** (see §4). The `instrument_type` property distinguishes them for queries within a single label.

### 2.2 Legal Provisions (4 labels)

| Label | Parent | Description | Key Property |
|---|---|---|---|
| `LegalProvision` | Instrument | Any articulable provision (section/subsection/clause/rule) | `provision_id` |
| `Section` | Act/Rule/Regulation | Numbered section (e.g., "Section 32") | `provision_id` |
| `Subsection` | Section | Sub-section (e.g., "Section 32(1)") | `provision_id` |
| `Clause` | Subsection/Rule | Clause within a provision | `provision_id` |
| `Schedule` | Instrument | Schedules attached to acts/rules | `provision_id` |
| `RuleProvision` | Rule | Individual rule within a rule document | `provision_id` |
| `RegulationProvision` | Regulation | Individual regulation | `provision_id` |

> `LegalProvision` is the umbrella label. `Section`, `Subsection`, `Clause`, `Schedule`, `RuleProvision`, `RegulationProvision` are sub-types. For the pilot we use `LegalProvision` + `Section` + `Subsection` + `Clause`.

### 2.3 Legal Concepts & Relations (shared, domain-neutral)

| Label | Description | Examples |
|---|---|---|
| `LegalConcept` | Reusable semantic concept | FoodBusiness, Slaughterhouse, Wastewater, Premises, Licence, Inspection |
| `LegalDomain` | Domain categorization node | FOOD_SAFETY, ANIMAL_SLAUGHTER, ENVIRONMENT, MUNICIPAL, PUBLIC_HEALTH, BUSINESS_CIVIL, LAND_PREMISES |
| `Subject` | Topic classification | Hygiene, Sanitation, Licensing, Waste, Pollution |
| `Obligation` | A duty imposed by law | "Maintain hygiene", "Obtain licence" |
| `Prohibition` | A forbidden act | "Do not slaughter on Sundays" |
| `Permission` | An authorised act | "May operate with licence" |
| `Power` | Authority's delegated power | "Power to inspect", "Power to close" |
| `Duty` | Specific duty imposed | "Display licence", "Maintain records" |
| `Offence` | Criminal/proceeding-worthy act | "Adulteration", "Operating without licence" |
| `Penalty` | Consequence of offence/violation | "Fine up to ₹10 lakh", "Imprisonment" |
| `Procedure` | Prescribed process | "Sampling procedure", "Adjudication procedure" |

### 2.4 Authorities & Jurisdictions (2 labels)

| Label | Description | Examples |
|---|---|---|
| `Authority` | Regulatory body or official | FSSAI, State Food Safety Authority, KMC, WBPCB, Court |
| `Jurisdiction` | Geographic scope | INDIA, WEST_BENGAL, KOLKATA |

### 2.5 Documents & Chunks (3 labels)

| Label | Description |
|---|---|
| `Document` | Source document (PDF/DOCX/TXT) — links to Qdrant chunks |
| `Chunk` | Individual text chunk — links back to Qdrant point_id |
| `Source` | Origin metadata (official gazette, URL, retrieval date) |

### 2.6 Enforcement & Business Entities (6 labels)

| Label | Description |
|---|---|
| `Inspection` | Inspection event |
| `Violation` | Finding of non-compliance |
| `Notice` | Legal notice (improvement/show-cause/etc.) |
| `FoodBusiness` | A business entity |
| `BusinessActivity` | Type of business (e.g., slaughterhouse, restaurant) |
| `Premises` | Physical location |

---

## 3. Relationship Types

### 3.1 Instrument Hierarchy

```
(:Act)-[:CONTAINS]->(:LegalProvision)
(:Rule)-[:CONTAINS]->(:LegalProvision)
(:Regulation)-[:CONTAINS]->(:LegalProvision)
(:LegalProvision)-[:HAS_SUBSECTION]->(:Subsection)
(:LegalProvision)-[:HAS_CLAUSE]->(:Clause)
(:LegalProvision)-[:HAS_SCHEDULE]->(:Schedule)
(:Rule)-[:MADE_UNDER]->(:Act)
(:Regulation)-[:MADE_UNDER]->(:Act)
(:Notification)-[:AMENDS]->(:LegalInstrument)
(:LegalInstrument)-[:REPEALS]->(:LegalInstrument)
(:LegalInstrument)-[:REPLACES]->(:LegalInstrument)
(:Section)-[:PART_OF]->(:Act)
```

### 3.2 Provision → Concept (the core legal-reasoning layer)

```
(:LegalProvision)-[:APPLIES_TO]->(:LegalConcept)
(:LegalProvision)-[:RELATES_TO]->(:LegalConcept)
(:LegalProvision)-[:IMPOSES_DUTY]->(:Duty)
(:LegalProvision)-[:CREATES_OBLIGATION]->(:Obligation)
(:LegalProvision)-[:CREATES_PROHIBITION]->(:Prohibition)
(:LegalProvision)-[:GRANTS_PERMISSION]->(:Permission)
(:LegalProvision)-[:GRANTS_POWER_TO]->(:Authority)
(:LegalProvision)-[:CREATES_OFFENCE]->(:Offence)
(:LegalProvision)-[:PRESCRIBES_PENALTY]->(:Penalty)
(:LegalProvision)-[:PRESCRIBES]->(:Procedure)
(:LegalProvision)-[:REQUIRES]->(:Permission|License|Certificate)
(:LegalProvision)-[:REQUIRES_AUTHORIZATION_FROM]->(:Authority)
(:LegalProvision)-[:ENFORCED_BY]->(:Authority)
```

### 3.3 Cross-Domain Relationships

```
(:LegalInstrument)-[:RELATED_TO]->(:LegalInstrument)
(:LegalProvision)-[:INTERACTS_WITH]->(:LegalProvision)
(:LegalProvision)-[:COMPLEMENTS]->(:LegalProvision)
(:LegalProvision)-[:CROSS_REFERENCES]->(:LegalProvision)
(:LegalProvision)-[:DEPENDS_ON]->(:LegalProvision)
(:LegalProvision)-[:OVERLAPS_WITH]->(:LegalProvision)
```

**No hallucinated relationships:** `CONFLICTS_WITH`, `OVERRIDES`, `INVALIDATES`, `APPLIES` are NOT created unless the source explicitly establishes them.

### 3.4 Provenance Chain

```
(:LegalProvision)-[:SUPPORTED_BY]->(:Chunk)
(:LegalProvision)-[:SOURCE_OF]->(:Document)
(:Chunk)-[:HAS_SOURCE]->(:Source)
(:LegalProvision)-[:BELONGS_TO_DOMAIN]->(:LegalDomain)
(:LegalProvision)-[:BELONGS_TO]->(:Jurisdiction)
```

### 3.5 Temporal Model

```
(:LegalProvision)-[:AMENDS]->(:LegalProvision)
(:LegalProvision)-[:REPLACED_BY]->(:LegalProvision)
(:LegalProvision)-[:REPEALED_BY]->(:LegalProvision)
```

### 3.6 Enforcement Chain

```
(:LegalProvision)-[:CREATES_OFFENCE]->(:Offence)
(:Offence)-[:HAS_PENALTY]->(:Penalty)
(:LegalProvision)-[:TRIGGERS]->(:Inspection)
(:Inspection)-[:FINDS]->(:Violation)
(:Violation)-[:REFERENCE]->(:LegalProvision)
(:LegalProvision)-[:TRIGGERS_NOTICE]->(:Notice)
```

### 3.7 Business + Enforcement

```
(:FoodBusiness)-[:OPERATES]->(:BusinessActivity)
(:FoodBusiness)-[:LOCATED_AT]->(:Premises)
(:LegalProvision)-[:APPLIES_TO]->(:FoodBusiness)
(:LegalProvision)-[:APPLIES_TO]->(:BusinessActivity)
```

---

## 4. Node Properties

### 4.1 Every Legal Instrument

```cypher
instrument_id   : str  // canonical ID, e.g. "FSS_ACT_2006"
short_title     : str  // "Food Safety and Standards Act, 2006"
title           : str  // full title
instrument_type : str  // enum: "act"|"rule"|"regulation"|"notification"|"order"|"circular"|"guideline"|"judgment"
jurisdiction    : str  // "INDIA"|"WEST_BENGAL"|"KOLKATA" (canonical)
legal_domain    : str  // "FOOD_SAFETY"|"ANIMAL_SLAUGHTER"|"ENVIRONMENT_POLLUTION"|"MUNICIPAL"|"PUBLIC_HEALTH"|"BUSINESS_CIVIL"|"LAND_PREMISES"
issuing_authority : str  // "FSSAI"|"Ministry of Health"|"KMC" etc.
enactment_date  : date
effective_date  : date
repeal_date     : date  // null until repealed
status          : str  // "current"|"repealed"|"superseded"|"amended"|"draft"
version         : str  // "1.0"|"as amended by Act 3 of 2023"
source_url      : str  // official source (PDF/URL)
source_type     : str  // "gazette"|"website"|"court_record"|"manual"
official_source : str  // "FSSAI website"|"Ministry of Law" etc.
last_verified   : date  // when the data was last checked against source
canonical_name  : str  // normalised name for entity resolution
aliases         : list[str] // alternative titles used in source text
```

### 4.2 Every Legal Provision

```cypher
provision_id    : str  // canonical, e.g. "FSS_ACT_2006_SEC_32"
title           : str  // "Powers of Food Safety Officer to take samples"
text            : str  // full provision text (if available)
provision_number: str  // "32"|"32(1)"|"32(1)(a)"|"Rule 3"|"Regulation 2.1"
instrument_id   : str  // back-reference to parent instrument
legal_domain    : str  // domain of the parent instrument
jurisdiction    : str  // "INDIA"|"WEST_BENGAL"
status          : str  // "current"|"repealed"|"amended"
effective_from  : date
effective_to    : date  // null if still current
source_document : str  // source_uri of the Document node
source_chunk_id : str  // Qdrant point_id (back-reference)
confidence      : float // 0.0-1.0 — how certain the extraction is
is_section_header: bool  // true for "Section N" headers vs body text
```

### 4.3 Other Nodes

**LegalDomain:**
```cypher
domain_name : str  // "FOOD_SAFETY" etc.
description : str
```

**Authority:**
```cypher
authority_id : str
name         : str
short_name   : str
jurisdiction : str
type         : str  // "regulator"|"department"|"court"|"commissioner"
```

**Chunk:**
```cypher
chunk_id     : str  // Qdrant point_id
document_id  : str  // back-reference to Document
chunk_index  : int
chunk_text   : str  // the actual text (or summary)
```

**Document:**
```cypher
document_id    : str
source_uri     : str  // local path or URL
title          : str
document_type  : str  // act|rule|regulation|notification|...
authority      : str
jurisdiction   : str
legal_domain   : str
file_hash      : str  // SHA-256
page_count     : int
```

**LegalConcept:**
```cypher
concept_id   : str  // canonical, e.g. "FoodBusiness"
name         : str
description  : str
domain       : str  // which domains this concept is relevant to
```

---

## 5. Constraints & Indexes

```cypher
// --- Instrument uniqueness + lookup ---
CREATE CONSTRAINT FOR (a:Act) REQUIRE a.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (r:Rule) REQUIRE r.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (rg:Regulation) REQUIRE rg.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (n:Notification) REQUIRE n.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (o:Order) REQUIRE o.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (c:Circular) REQUIRE c.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (g:Guideline) REQUIRE g.instrument_id IS UNIQUE;
CREATE CONSTRAINT FOR (j:Judgment) REQUIRE j.instrument_id IS UNIQUE;

// --- Provision uniqueness ---
CREATE CONSTRAINT FOR (p:LegalProvision) REQUIRE p.provision_id IS UNIQUE;
CREATE CONSTRAINT FOR (s:Section) REQUIRE s.provision_id IS UNIQUE;
CREATE CONSTRAINT FOR (ss:Subsection) REQUIRE ss.provision_id IS UNIQUE;
CREATE CONSTRAINT FOR (cl:Clause) REQUIRE cl.provision_id IS UNIQUE;

// --- Authority uniqueness ---
CREATE CONSTRAINT FOR (au:Authority) REQUIRE au.authority_id IS UNIQUE;

// --- Concept uniqueness ---
CREATE CONSTRAINT FOR (c:LegalConcept) REQUIRE c.concept_id IS UNIQUE;

// --- Domain uniqueness ---
CREATE CONSTRAINT FOR (d:LegalDomain) REQUIRE d.domain_name IS UNIQUE;

// --- Chunk / Document uniqueness ---
CREATE CONSTRAINT FOR (ch:Chunk) REQUIRE ch.chunk_id IS UNIQUE;
CREATE CONSTRAINT FOR (d:Document) REQUIRE d.document_id IS UNIQUE;

// --- Indexes for fast lookup ---
CREATE INDEX FOR (i:Act) ON (i.legal_domain, i.status);
CREATE INDEX FOR (r:Rule) ON (r.legal_domain, r.status);
CREATE INDEX FOR (rg:Regulation) ON (rg.legal_domain, rg.status);
CREATE INDEX FOR (n:Notification) ON (n.legal_domain, n.status);
CREATE INDEX FOR (p:LegalProvision) ON (p.provision_id);
CREATE INDEX FOR (p:LegalProvision) ON (p.section_number, p.instrument_id);
CREATE INDEX FOR (p:LegalProvision) ON (p.legal_domain, p.status);
CREATE INDEX FOR (s:Section) ON (s.instrument_id);
CREATE INDEX FOR (au:Authority) ON (au.name);
CREATE INDEX FOR (d:Document) ON (d.legal_domain);
```

**Note on constraints:** The existing case-file constraints (`Case.local_id`, `FBO.local_id`, etc.) are on different labels entirely and will NOT conflict with these new legal-instrument constraints. Both schemas coexist peacefully.

---

## 6. Entity Resolution

### 6.1 Canonical Instrument IDs

```
FSS_ACT_2006                    → Food Safety and Standards Act, 2006
FSS_LICENSING_REG_2011          → Food Safety and Standards (Licensing and Registration) Regulations, 2011
FSS_FOOD_ADDITIVES_REG_2011     → Food Safety and Standards (Food Products Standards and Food Additives) Regulations, 2011
WATER_ACT_1974                  → The Water (Prevention and Control of Pollution) Act, 1974
AIR_ACT_1981                  → The Air (Prevention and Control of Pollution) Act, 1981
ENV_PROTECTION_ACT_1986          → Environment (Protection) Act, 1986
WB_ANIMAL_SLAUGHTER_RULE_2023     → West Bengal Animal Slaughter House Rules, 2023
KMC_MUNICIPAL_ACT_2009            → Kolkata Municipal Corporation Act, 2009
IPC_1860                            → Indian Penal Code, 1860
CONTRACT_ACT_1872                   → Indian Contract Act, 1872
```

### 6.2 Canonical Provision IDs

```
FSS_ACT_2006_SEC_32
FSS_ACT_2006_SEC_32_SUB_1
FSS_ACT_2006_SEC_32_SUB_1_CLR_A
WATER_ACT_1974_SEC_26
KMC_ACT_2009_SEC_302
```

### 6.3 Citation Normalisation

Input formats normalised to structured properties:

| Raw | `provision_number` | `parent_provision_id` |
|---|---|---|
| Section 32 | "32" | FSS_ACT_2006 |
| Section 32(1) | "32" | FSS_ACT_2006_SEC_32 |
| Section 32(1)(a) | "32" | FSS_ACT_2006_SEC_32_SUB_1 |
| Rule 3 | "3" | env instrument root |
| Regulation 2.1 | "2.1" | env instrument root |

---

## 7. Domain Taxonomy

| Domain Constant | Description | Jurisdiction | Acts |
|---|---|---|---|
| `FOOD_SAFETY` | FSS Act + FSSAI regulations/notifications | INDIA (central) | FSS Act 2006 + 15 regulations + 8 notifications |
| `ANIMAL_SLAUGHTER` | West Bengal slaughter laws + animal welfare | WEST_BENGAL | WB Animal Slaughter Rules, PCA |
| `ENVIRONMENT_POLLUTION` | Central + state environmental laws | INDIA/WEST_BENGAL | EPA 1986, Water Act, Air Act, SWDM Rules, PBPCB/WBPCB rules |
| `MUNICIPAL` | Kolkata Municipal Corporation legislation | KOLKATA | KMC Act 2009 + by-laws |
| `PUBLIC_HEALTH` | Public health laws relevant to food businesses | INDIA | Various |
| `BUSINESS_CIVIL` | Contracts, sale of goods, consumer protection | INDIA | Contract Act, SOGA, CP Act |
| `LAND_PREMISES` | Land/premises legislation | WEST_BENGAL | WB Land Reforms, premises laws |

---

## 8. RAG Integration Contract

The graph serves structured evidence to the LLM via this JSON shape:

```json
{
  "query": "What laws apply to a slaughterhouse as a food business?",
  "domains": ["FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION", "MUNICIPAL"],
  "instruments": [
    {
      "instrument_id": "FSS_ACT_2006",
      "title": "Food Safety and Standards Act, 2006",
      "legal_domain": "FOOD_SAFETY",
      "provision_id": "FSS_ACT_2006_SEC_32",
      "provision_number": "32",
      "text": "Powers of Food Safety Officer...",
      "authority": "FSSAI",
      "confidence": 0.95,
      "source": {
        "document_id": "doc-uuid",
        "chunk_id": "qdrant-point-id",
        "source_uri": "FSSAI_rules documents/Food_Safety_and_Standards_Act_2006.pdf"
      }
    }
  ],
  "relationships": [
    {"type": "RELATED_TO", "source": "FSS_ACT_2006", "target": "WB_ANIMAL_SLAUGHTER_RULE_2023"},
    {"type": "APPLIES_TO", "source": "FSS_ACT_2006_SEC_32", "target": "FoodBusiness"}
  ],
  "authorities": ["FSSAI", "WBPCB", "KMC"]
}
```

---

## 9. Ingestion Flow (pilot)

1. **Document classification** → `document_type`, `authority`, `jurisdiction`, `legal_domain` via `DocumentClassifier` + domain mapping
2. **Instrument node creation** → `CREATE (i:Act {instrument_id: ..., ...})` with `instrument_id` uniqueness
3. **Provision extraction** → Section parser from `legal_paragraph_detection_engine` → `CREATE (p:LegalProvision {provision_id: ...})` + `CONTAINS`/`HAS_SUBSECTION` edges
4. **Chunk linkage** → `MATCH (p:LegalProvision {provision_id: ...})-[:SUPPORTED_BY]->(c:Chunk {chunk_id: ...})`
5. **Cross-reference wiring** → `CitationAdapter` output → `CROSS_REFERENCES` edges
6. **Concept mapping** → deterministic keyword matching → `APPLIES_TO`/`IMPOSES_DUTY` edges
7. **Temporal metadata** → `effective_from`/`effective_to`/`status` from `MetadataAdapter`

---

## 10. Validation Queries (Preview)

```cypher
// Domain separation: FSSAI provisions do not return land-revenue
MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {domain_name: 'FOOD_SAFETY'})
WHERE NOT (p)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {domain_name: 'LAND_PREMISES'})
RETURN count(p)

// Cross-domain retrieval: slaughterhouse + food business
MATCH (p1:LegalProvision)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {domain_name: 'FOOD_SAFETY'})
MATCH (p2:LegalProvision)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {domain_name: 'ANIMAL_SLAUGHTER'})
MATCH (p1)-[:APPLIES_TO]->(:LegalConcept {name: 'FoodBusiness'})
MATCH (p2)-[:APPLIES_TO]->(:LegalConcept {name: 'Slaughterhouse'})
RETURN p1.provision_id, p2.provision_id

// Orphan provisions
MATCH (p:LegalProvision) WHERE NOT (p)--() RETURN p LIMIT 10

// Unsupported authority relationships
MATCH (p:LegalProvision)-[r:ENFORCED_BY]->(a:Authority)
WHERE r.confidence < 0.8 RETURN p, a, r
```

---

## 11. Constraints Summary

| Constraint Type | Key | Label(s) |
|---|---|---|
| Uniqueness | `instrument_id` | Act, Rule, Regulation, Notification, Order, Circular, Guideline, Judgment |
| Uniqueness | `provision_id` | LegalProvision, Section, Subsection, Clause |
| Uniqueness | `authority_id` | Authority |
| Uniqueness | `concept_id` | LegalConcept |
| Uniqueness | `domain_name` | LegalDomain |
| Uniqueness | `chunk_id` | Chunk |
| Uniqueness | `document_id` | Document |
| Index | `legal_domain`, `status` | Act, Rule, Regulation, Notification |
| Index | `provision_id` | LegalProvision |
| Index | `section_number`, `instrument_id` | Section |
| Index | `name` | Authority |
| Index | `legal_domain` | Document |

---

## 12. Migration from Existing Schema

The existing Neo4j constraints are on completely different labels (`Case`, `FBO`, `Inspector`, `Sample`, `Lab`, `Section`, `Evidence`, `Ancillary`, `Entity`). The new legal KG schema uses different labels (`Act`, `Rule`, `Regulation`, `Notification`, `LegalProvision`, `Section` as a sub-type of LegalProvision, `Authority`, `LegalConcept`, `LegalDomain`, `Chunk`, `Document`).

**Key conflict:** The existing `Section` label (used for case-file legal sections like Section 55, 56, etc.) vs. the new `Section` label (legal instrument sections). These coexist on different labels but could be confusing. The new `Section` nodes will have `provision_id` and `instrument_id` properties that the existing ones don't have, making them distinguishable.

The existing `Entity` label (fallback when APOC is unavailable) and `RELATIONSHIP` edge type remain unchanged — they are used by the case-file sync path which is separate from the legal KG path.
