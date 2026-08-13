# LEGAL_DOMAIN_TAXONOMY.md — Multi-Domain Legal Domain Classification

> **Phase 2 — Domain taxonomy**  
> Defines the seven legal domains, their jurisdictions, canonical names, and the relationship to existing act registries.

---

## 1. Domain Overview

The legal KG is structured around **seven domains**, each representing a distinct area of law that may be relevant to a food business operating in West Bengal, India. Every legal instrument and provision node carries a `legal_domain` property.

Domains are **never merged** — a document from one domain never becomes another domain's document. Cross-domain connections are expressed via explicit `RELATED_TO`/`INTERACTS_WITH`/`CROSS_REFERENCES` relationships between provisions, never by reclassifying the document.

---

## 2. Domains

### 2.1 FOOD_SAFETY (Primary)

**Jurisdiction:** India (central)

**Description:** All laws, regulations, rules, and notifications related to food safety, including the food safety and standards legislation, FSSAI licensing, hygiene requirements, inspection, sampling, analysis, improvement notices, offences, penalties, adjudication, and enforcement.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `FSS_ACT_2006` | Food Safety and Standards Act, 2006 | 2006 |

**Key Regulations:**
| Canonical ID | Title | Year |
|---|---|---|
| `FSS_LICENSING_REG_2011` | Food Safety and Standards (Licensing and Registration) Regulations, 2011 | 2011 |
| `FSS_FOOD_ADDITIVES_REG_2011` | Food Safety and Standards (Food Products Standards and Food Additives) Regulations, 2011 | 2011 |
| `FSS_PACKAGING_LABELLING_REG_2011` | Food Safety and Standards (Packaging and Labelling) Regulations, 2011 | 2011 |
| `FSS_NUTRACEUTICALS_REG_2011` | Food Safety and Standards (Nutraceuticals) Regulations, 2011 | 2011 |
| `FSS_FORTIFICATION_REG_2017` | Food Safety and Standards (Club, Resort and Catering Services) Regulations, 2017 | 2017 |
| `FSS_ALCOHOLIC_BEVERAGES_REG_2020` | Food Safety and Standards (Alcoholic Beverages) Regulations, 2020 | 2020 |
| `FSS_CONTAMINANTS_REG_2010` | Food Safety and Standards (Contaminants, Toxins and Residues) Rules, 2010 | 2010 |
| `FSS_ORGANIC_FOOD_REG_2010` | Food Safety and Standards (Organic Foods) Regulations, 2010 | 2010 |

**Key Notifications:**
| Canonical ID | Title | Year |
|---|---|---|
| `FSS_NOTIF_QUALITY_VEGETABLE_OIL_2017` | Gazette Notification — Quality of Vegetable Oils | 2017 |
| `FSS_NOTIF_ALCOHOLIC_BEVERAGES_2021` | Notification — Alcoholic Beverages | 2021 |
| `FSS_NOTIF_FBO_EXTENSION_2016` | Notification — FBO Extension Time | 2016 |
| `FSS_NOTIF_PROHIBITION_2026` | Notification — Prohibition in certain areas | 2026 |
| `FSS_NOTIF_MINOR_SEED_OILS_2026` | Notification — Minor/Cold-pressed seed oils | 2026 |
| `FSS_NOTIF_LICREG_2024` | Notification — Licensing and Registration Guidelines | 2024 |

**Key Authorities:**
- `FSSAI` — Food Safety and Standards Authority of India
- `STATE_FSA` — State Food Safety Authority (per-state)
- `FSO` — Food Safety Officer
- `FOOD_SAFETY_COMMISSIONER` — State Food Safety Commissioner
- `ADJUDICATING_OFFICER` — FSSAI adjudicating officer

**Key Concepts:**
FoodBusiness, FoodBusinessOperator, Licence, Registration, Hygiene, Sanitation, Sampling, Analysis, ImprovementNotice, Misbranded, Substandard, Adulteration, Recall, Seizure, Adjudication, Offence, Penalty, Compliance

### 2.2 ANIMAL_SLAUGHTER

**Jurisdiction:** West Bengal (state), India (central for PCA)

**Description:** Laws governing slaughterhouses, animal welfare in slaughter, meat processing, and livestock regulation as they relate to food businesses.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `WB_ANIMAL_SLAUGHTER_RULE_2023` | West Bengal Animal Slaughter House Rules, 2023 | 2023 |
| `PCA_1960` | Prevention of Cruelty to Animals Act, 1890 (am. 1960) | 1960 |

**Key Concepts:**
Slaughterhouse, AnimalSlaughter, Meat, Livestock, AnimalWelfare, Veterinary, Quarantine, Disease, SlaughterActivity

**Key Authorities:**
- `WB_FODDER_DEPT` — West Bengal Department of Animal Husbandry & Fisheries
- `WB_POLLUTION_BOARD` — West Bengal Pollution Control Board (if separate jurisdiction)

### 2.3 ENVIRONMENT_POLLUTION

**Jurisdiction:** India (central), West Bengal (state)

**Description:** Environmental laws applicable to food businesses generating wastewater, solid waste, emissions, plastic waste, and operating under environmental consent requirements.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `ENV_PROTECTION_ACT_1986` | Environment (Protection) Act, 1986 | 1986 |
| `WATER_ACT_1974` | The Water (Prevention and Control of Pollution) Act, 1974 | 1974 |
| `AIR_ACT_1981` | The Air (Prevention and Control of Pollution) Act, 1981 | 1981 |

**Key Rules/Notifications:**
| Canonical ID | Title | Year |
|---|---|---|
| `SWDM_RULES_2016` | Solid Waste Management Rules, 2016 | 2016 |
| `PLASTIC_WASTE_RULES_2016` | Plastic Waste Management Rules, 2016 | 2016 |
| `PLASTIC_WASTE_RULES_2022` | Plastic Waste Management (Amendment) Rules, 2022 | 2022 |

**Key Concepts:**
Wastewater, Effluent, SolidWaste, Pollution, PlasticWaste, ConsentToOperate, Emission, EnvironmentalRequirement, Waste

**Key Authorities:**
- `CPCB` — Central Pollution Control Board
- `WBPCB` — West Bengal Pollution Control Board
- `MOEFCC` — Ministry of Environment, Forest and Climate Change

### 2.4 MUNICIPAL

**Jurisdiction:** Kolkata (municipal), West Bengal (state)

**Description:** Municipal laws applicable to food businesses operating from urban premises, including trade licensing, sanitation, drainage, waste, nuisance, and public health powers.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `KMC_ACT_2009` | Kolkata Municipal Corporation Act, 2009 | 2009 |

**Key By-laws:**
| Canonical ID | Title |
|---|---|
| `KMC_SANITATION_BYLAW` | Kolkata Municipal Corporation Sanitation By-laws |
| `KMC_TRADE_LICENCE_BYLAW` | Trade Licensing By-laws |
| `KMC_FOOD_STALLS_BYLAW` | Food Stalls and Temporary Encroachments By-laws |
| `KMC_NUISANCE_BYLAW` | Nuisance and Offensive Trades By-laws |

**Key Concepts:**
Premises, TradeLicence, Sanitation, Drainage, Waste, Nuisance, DangerousTrade, PublicHealth, MunicipalTrade, OffensiveTrade

**Key Authorities:**
- `KMC` — Kolkata Municipal Corporation
- `KMC_HEALTH_OFFICER` — Chief Medical Officer of Health, KMC
- `KMC_TRADE_LICENSING` — Trade Licensing Department, KMC

### 2.5 PUBLIC_HEALTH

**Jurisdiction:** India (central), West Bengal (state)

**Description:** Public health legislation directly relevant to food businesses — food adulteration, disease prevention, public health emergencies, and related enforcement powers.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `IPC_1860` | Indian Penal Code, 1860 (public health offences) | 1860 |

**Key Rules:**
| Canonical ID | Title | Year |
|---|---|---|
| `DAIRY_FOOD_SAFETY_RULES_2011` | Dairy (Food Safety and Standards) Rules, 2011 | 2011 |
| `FOOD_SAFETY_CONTINGENCY_PLAN_2020` | Food Safety Contingency Plan | 2020 |

**Key Concepts:**
FoodAdulteration, DiseaseOutbreak, PublicHealthEmergency, Contamination, HealthHazard

**Key Authorities:**
- `MOHFW` — Ministry of Health and Family Welfare
- `WB_HEALTH_DEPT` — West Bengal Department of Health & Family Welfare

### 2.6 BUSINESS_CIVIL

**Jurisdiction:** India (central)

**Description:** Business and civil laws with meaningful relevance to food businesses — contracts, sale of goods, consumer protection, premises/tenancy, and partnership. Only laws that materially affect food business operations are included.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `CONTRACT_ACT_1872` | Indian Contract Act, 1872 | 1872 |
| `SALE_OF_GOODS_ACT_1930` | Sale of Goods Act, 1930 | 1930 |
| `CONSUMER_PROTECTION_ACT_2019` | Consumer Protection Act, 2019 | 2019 |
| `PARTNERSHIP_ACT_1932` | Indian Partnership Act, 1932 | 1932 |

**Key Concepts:**
Contract, SaleOfGoods, ConsumerProtection, Partnership, Tenancy, PremisesAgreement, Liability, Damages, Compensation, BreachOfContract

**Key Authorities:**
- `COURTS` — Civil courts

### 2.7 LAND_PREMISES

**Jurisdiction:** West Bengal (state), India (central)

**Description:** Land and premises legislation that can materially affect the establishment or operation of a food business — land ownership, occupancy, building permissions, and premises usage.

**Key Acts:**
| Canonical ID | Title | Year |
|---|---|---|
| `WB_LAND_REFORMS_ACT_1955` | West Bengal Land Reforms and Tenancy (Amendment) Act, 1955 | 1955 |
| `BUILDING_NUISANCE_ACT_1885` | Public Nuisance (Buildings) Act, 1885 | 1885 |

**Key Concepts:**
LandOwnership, PremisesOccupancy, BuildingPermission, TradeLicence, Nuisance, LandUse, Tenancy, Rent, OccupancyCertificate

**Key Authorities:**
- `WB_LAND_RECORDS` — West Bengal Board of Revenue (Land Records)
- `KMC_BUILDING` — KMC Building Department
- `WB_RERA` — West Bengal Real Estate Regulatory Authority

---

## 3. Domain ↔ Jurisdiction Mapping

| Domain | Jurisdiction | Level |
|---|---|---|
| FOOD_SAFETY | INDIA | Central |
| ANIMAL_SLAUGHTER | WEST_BENGAL | State |
| ENVIRONMENT_POLLUTION | INDIA + WEST_BENGAL | Central + State |
| MUNICIPAL | KOLKATA | Municipal |
| PUBLIC_HEALTH | INDIA + WEST_BENGAL | Central + State |
| BUSINESS_CIVIL | INDIA | Central |
| LAND_PREMISES | WEST_BENGAL | State |

### Jurisdiction Canonical Names

| Canonical | Full Name |
|---|---|
| `INDIA` | India (central government) |
| `WEST_BENGAL` | West Bengal (state government) |
| `KOLKATA` | Kolkata (municipal corporation) |
| `MAHARASHTRA` | Maharashtra (state government) |
| `DELHI_NCR` | Delhi NCR (national capital region) |
| `TAMIL_NADU` | Tamil Nadu (state government) |

---

## 4. Cross-Domain Concept Mapping

The following concepts appear across multiple domains and are represented as **shared** `LegalConcept` nodes:

| Concept | Domains |
|---|---|
| `Licence` | FOOD_SAFETY, ANIMAL_SLAUGHTER, MUNICIPAL |
| `Registration` | FOOD_SAFETY, MUNICIPAL |
| `TradeLicence` | MUNICIPAL, BUSINESS_CIVIL |
| `Premises` | MUNICIPAL, LAND_PREMISES, FOOD_SAFETY |
| `Waste` | ENVIRONMENT, MUNICIPAL, FOOD_SAFETY |
| `Sanitation` | FOOD_SAFETY, MUNICIPAL, PUBLIC_HEALTH |
| `Inspection` | FOOD_SAFETY, ENVIRONMENT, MUNICIPAL |
| `Authority` | All |
| `Offence` | FOOD_SAFETY, ENVIRONMENT, BUSINESS_CIVIL |
| `Penalty` | FOOD_SAFETY, ENVIRONMENT, BUSINESS_CIVIL |

---

## 5. Authority ↔ Domain Mapping

| Authority | Domains | Jurisdiction |
|---|---|---|
| FSSAI | FOOD_SAFETY | INDIA |
| State Food Safety Authority | FOOD_SAFETY | State (e.g., WB) |
| Food Safety Officer | FOOD_SAFETY | Local |
| WB Animal Husbandry Dept | ANIMAL_SLAUGHTER | WEST_BENGAL |
| CPCB | ENVIRONMENT | INDIA |
| WBPCB | ENVIRONMENT | WEST_BENGAL |
| KMC | MUNICIPAL | KOLKATA |
| MOHFW | PUBLIC_HEALTH | INDIA |
| Courts | BUSINESS_CIVIL, LAND_PREMISES | Depends |
| WB Land Records | LAND_PREMISES | WEST_BENGAL |

---

## 6. Pilot Corpus Selection

For Phase 3 (pilot ingestion), one instrument per domain is selected:

| Domain | Instrument | Canonical ID | Source |
|---|---|---|---|
| FOOD_SAFETY | Food Safety and Standards Act, 2006 | `FSS_ACT_2006` | `FSSAI_rules documents/Food_Safety_and_Standards_Act_2006.pdf` |
| ANIMAL_SLAUGHTER | West Bengal Animal Slaughter House Rules, 2023 | `WB_ANIMAL_SLAUGHTER_RULE_2023` | *(to be sourced/created for pilot)* |
| ENVIRONMENT_POLLUTION | Environment (Protection) Act, 1986 | `ENV_PROTECTION_ACT_1986` | *(to be sourced/created for pilot)* |
| MUNICIPAL | Kolkata Municipal Corporation Act, 2009 | `KMC_ACT_2009` | *(to be sourced/created for pilot)* |
| PUBLIC_HEALTH | *(minimal — IPC public health offences)* | `IPC_1860` | *(to be sourced/created for pilot)* |
| BUSINESS_CIVIL | Indian Contract Act, 1872 | `CONTRACT_ACT_1872` | *(to be sourced/created for pilot)* |
| LAND_PREMISES | West Bengal Land Reforms Act, 1955 | `WB_LAND_REFORMS_ACT_1955` | *(to be sourced/created for pilot)* |

For the pilot, non-FSSAI documents will be represented as **structured instrument nodes with key provisions** (not full corpus ingestion) — this is sufficient to validate the cross-domain graph structure, provenance chain, and query layer.

---

## 7. Act Registry Alignment

The existing `app/rag/legal_sections.py` `ACT_SECTION_RANGES` already registers these acts (as advisory "known sections" for cross-reference validation):

| Act Name (from `legal_sections.py`) | Canonical ID (this taxonomy) | Domain |
|---|---|---|
| Food Safety and Standards Act, 2006 | `FSS_ACT_2006` | FOOD_SAFETY |
| Environment (Protection) Act, 1986 | `ENV_PROTECTION_ACT_1986` | ENVIRONMENT |
| Water (Prevention and Control of Pollution) Act, 1974 | `WATER_ACT_1974` | ENVIRONMENT |
| Air (Prevention and Control of Pollution) Act, 1981 | `AIR_ACT_1981` | ENVIRONMENT |
| Companies Act, 2013 | `COMPANIES_ACT_2013` | BUSINESS_CIVIL |
| Indian Contract Act, 1872 | `CONTRACT_ACT_1872` | BUSINESS_CIVIL |
| Sale of Goods Act, 1930 | `SALE_OF_GOODS_1930` | BUSINESS_CIVIL |
| Indian Partnership Act, 1932 | `PARTNERSHIP_1932` | BUSINESS_CIVIL |
| Limited Liability Partnership Act, 2008 | `LLP_ACT_2008` | BUSINESS_CIVIL |
| Limitation Act, 1963 | `LIMITATION_1963` | BUSINESS_CIVIL |
| Specific Relief Act, 1963 | `SPECIFIC_RELIEF_1963` | BUSINESS_CIVIL |
| Consumer Protection Act, 2019 | `CONSUMER_PROTECTION_2019` | BUSINESS_CIVIL |

**Actions:**
1. Extend `legal_sections.py` with `ANIMAL_SLAUGHTER`, `MUNICIPAL`, `LAND_PREMISES` acts
2. Add canonical ID mappings for all 12 existing acts to this taxonomy
3. All existing acts are centrally-jurisdictioned (INDIA), which aligns with the taxonomy above
