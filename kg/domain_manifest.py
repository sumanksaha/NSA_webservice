"""Multi-domain legal KG domain manifest (Phase 3 — pilot).

Defines canonical LegalDomain, Authority, LegalConcept, and instrument
registry nodes.  These are inserted into Neo4j as static reference data
before any legal-instrument ingestion runs — they act as controlled
vocabularies so domain-segregation and cross-domain traversal are reliable.

The manifest is pure data (no imports of app internals except for the
qdrant chunker's PAYLOAD_INDEX_FIELDS, which we don't need here).  It is
safe to import in any context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Legal Domains (controlled vocabulary)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LegalDomain:
    domain_name: str
    description: str
    jurisdiction: str
    priority: int  # 1=primary, 7=secondary


DOMAINS: dict[str, LegalDomain] = {
    "FOOD_SAFETY": LegalDomain(
        "FOOD_SAFETY",
        "Food safety law: FSS Act, FSSAI regulations, licensing, inspection, sampling, offences, penalties.",
        "INDIA",
        1,
    ),
    "ANIMAL_SLAUGHTER": LegalDomain(
        "ANIMAL_SLAUGHTER",
        "West Bengal animal slaughter legislation, slaughter-house regulation, animal welfare.",
        "WEST_BENGAL",
        2,
    ),
    "ENVIRONMENT_POLLUTION": LegalDomain(
        "ENVIRONMENT_POLLUTION",
        "Environmental protection: Water Act, Air Act, EPA, solid waste, plastic waste, WBPCB.",
        "INDIA",
        3,
    ),
    "MUNICIPAL": LegalDomain(
        "MUNICIPAL",
        "Kolkata Municipal Corporation legislation: trade licensing, sanitation, drainage, nuisance.",
        "KOLKATA",
        4,
    ),
    "PUBLIC_HEALTH": LegalDomain(
        "PUBLIC_HEALTH",
        "Public-health legislation directly relevant to food businesses and premises.",
        "INDIA",
        5,
    ),
    "BUSINESS_CIVIL": LegalDomain(
        "BUSINESS_CIVIL",
        "Commercial/business law relevant to food businesses: contracts, sale of goods, consumer protection.",
        "INDIA",
        6,
    ),
    "LAND_PREMISES": LegalDomain(
        "LAND_PREMISES",
        "Land and premises legislation affecting food business establishment: land reforms, occupancy, building.",
        "WEST_BENGAL",
        7,
    ),
    "CRIMINAL": LegalDomain(
        "CRIMINAL",
        "Criminal law: Bharatiya Nyaya Sanhita, 2023 (successor to the Indian Penal Code, 1860) and related penal statutes.",
        "INDIA",
        8,
    ),
}


# --------------------------------------------------------------------------- #
# Jurisdictions (controlled vocabulary)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Jurisdiction:
    jurisdiction_id: str
    name: str
    level: str  # "central" | "state" | "municipal"


JURISDICTIONS: dict[str, Jurisdiction] = {
    "INDIA": Jurisdiction("INDIA", "India (central government)", "central"),
    "WEST_BENGAL": Jurisdiction("WEST_BENGAL", "West Bengal (state government)", "state"),
    "KOLKATA": Jurisdiction("KOLKATA", "Kolkata (municipal corporation)", "municipal"),
}


# --------------------------------------------------------------------------- #
# Authorities (controlled vocabulary)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Authority:
    authority_id: str
    name: str
    short_name: str
    jurisdiction: str
    authority_type: str  # "regulator" | "department" | "court" | "commissioner" | "officer"


AUTHORITIES: dict[str, Authority] = {
    "FSSAI": Authority("FSSAI", "Food Safety and Standards Authority of India", "FSSAI", "INDIA", "regulator"),
    "STATE_FSA": Authority("STATE_FSA", "State Food Safety Authority", "SFSA", "WEST_BENGAL", "regulator"),
    "FSO": Authority("FSO", "Food Safety Officer", "FSO", "INDIA", "officer"),
    "WB_FODDER_DEPT": Authority(
        "WB_FODDER_DEPT", "West Bengal Dept of Animal Husbandry & Fisheries", "WB-AH&F", "WEST_BENGAL", "department"
    ),
    "CPCB": Authority("CPCB", "Central Pollution Control Board", "CPCB", "INDIA", "regulator"),
    "WBPCB": Authority("WBPCB", "West Bengal Pollution Control Board", "WBPCB", "WEST_BENGAL", "regulator"),
    "MOEFCC": Authority(
        "MOEFCC", "Ministry of Environment, Forest and Climate Change", "MoEFCC", "INDIA", "department"
    ),
    "KMC": Authority("KMC", "Kolkata Municipal Corporation", "KMC", "KOLKATA", "regulator"),
    "KMC_HEALTH": Authority("KMC_HEALTH", "Chief Medical Officer of Health, KMC", "KMC-Health", "KOLKATA", "officer"),
    "MOHFW": Authority("MOHFW", "Ministry of Health and Family Welfare", "MoHFW", "INDIA", "department"),
    "MO_LAW": Authority("MO_LAW", "Ministry of Law and Justice", "MoLJ", "INDIA", "department"),
    "COURTS": Authority("COURTS", "Courts of India", "Courts", "INDIA", "court"),
    "WB_LAND_RECORDS": Authority(
        "WB_LAND_RECORDS", "West Bengal Board of Revenue (Land Records)", "WB-Land", "WEST_BENGAL", "department"
    ),
    "PARLIAMENT_OF_INDIA": Authority(
        "PARLIAMENT_OF_INDIA", "Parliament of India", "Parliament", "INDIA", "legislature"
    ),
    "WB_LEGISLATURE": Authority(
        "WB_LEGISLATURE", "West Bengal Legislature", "WB Legislature", "WEST_BENGAL", "legislature"
    ),
    "MOFAHD": Authority(
        "MOFAHD", "Ministry of Fisheries, Animal Husbandry and Dairying", "MoFAH&D", "INDIA", "department"
    ),
    "WB_GOVT": Authority("WB_GOVT", "Government of West Bengal", "WB Government", "WEST_BENGAL", "government"),
}


# --------------------------------------------------------------------------- #
# Legal Concepts (controlled vocabulary, shared across domains)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LegalConcept:
    concept_id: str
    name: str
    description: str
    domains: tuple[str, ...]


CONCEPTS: dict[str, LegalConcept] = {
    "FoodBusiness": LegalConcept(
        "FoodBusiness", "Food Business", "An enterprise engaged in manufacturing/distributing food.", ("FOOD_SAFETY",)
    ),
    "FoodBusinessOperator": LegalConcept(
        "FoodBusinessOperator",
        "Food Business Operator",
        "Person/firm licensed to operate a food business.",
        ("FOOD_SAFETY",),
    ),
    "Licence": LegalConcept(
        "Licence",
        "Licence",
        "Official permission to operate.",
        ("FOOD_SAFETY", "ANIMAL_SLAUGHTER", "MUNICIPAL", "ENVIRONMENT_POLLUTION"),
    ),
    "Registration": LegalConcept(
        "Registration", "Registration", "Formal enrolment in a regulatory register.", ("FOOD_SAFETY", "MUNICIPAL")
    ),
    "Slaughterhouse": LegalConcept(
        "Slaughterhouse",
        "Slaughterhouse",
        "Premises where animals are slaughtered for food.",
        ("ANIMAL_SLAUGHTER", "FOOD_SAFETY"),
    ),
    "AnimalSlaughter": LegalConcept(
        "AnimalSlaughter", "Animal Slaughter", "The act of slaughtering animals for food.", ("ANIMAL_SLAUGHTER",)
    ),
    "Meat": LegalConcept("Meat", "Meat", "Animal flesh consumed as food.", ("ANIMAL_SLAUGHTER",)),
    "Wastewater": LegalConcept(
        "Wastewater", "Wastewater", "Liquid waste generated by operations.", ("ENVIRONMENT_POLLUTION", "FOOD_SAFETY")
    ),
    "Effluent": LegalConcept("Effluent", "Effluent", "Liquid discharge from a process.", ("ENVIRONMENT_POLLUTION",)),
    "SolidWaste": LegalConcept(
        "SolidWaste",
        "Solid Waste",
        "Solid refuse from operations.",
        ("ENVIRONMENT_POLLUTION", "MUNICIPAL", "FOOD_SAFETY"),
    ),
    "Premises": LegalConcept(
        "Premises", "Premises", "Physical location of a business.", ("MUNICIPAL", "FOOD_SAFETY", "LAND_PREMISES")
    ),
    "TradeLicence": LegalConcept(
        "TradeLicence", "Trade Licence", "Municipal licence to operate a trade.", ("MUNICIPAL", "BUSINESS_CIVIL")
    ),
    "Sanitation": LegalConcept(
        "Sanitation",
        "Sanitation",
        "Cleanliness and hygiene requirements.",
        ("FOOD_SAFETY", "MUNICIPAL", "PUBLIC_HEALTH"),
    ),
    "Hygiene": LegalConcept(
        "Hygiene", "Hygiene", "Conditions preventing contamination.", ("FOOD_SAFETY", "PUBLIC_HEALTH")
    ),
    "Nuisance": LegalConcept(
        "Nuisance", "Nuisance", "Interference with public health/comfort.", ("MUNICIPAL", "ENVIRONMENT_POLLUTION")
    ),
    "ENV_POLLUTION": LegalConcept(
        "ENV_POLLUTION",
        "Environmental Pollution",
        "Environmental contamination and pollution.",
        ("ENVIRONMENT_POLLUTION",),
    ),
    "LandPremises": LegalConcept("LandPremises", "Land Premises", "Land and premises legislation.", ("LAND_PREMISES",)),
    "ConsentToOperate": LegalConcept(
        "ConsentToOperate", "Consent to Operate", "Environmental clearance for discharge.", ("ENVIRONMENT_POLLUTION",)
    ),
    "Inspection": LegalConcept(
        "Inspection",
        "Inspection",
        "Official examination of premises.",
        ("FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION", "MUNICIPAL"),
    ),
    "Sampling": LegalConcept("Sampling", "Sampling", "Collection of samples for analysis.", ("FOOD_SAFETY",)),
    "Offence": LegalConcept(
        "Offence",
        "Offence",
        "Act punishable by law.",
        ("FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION", "BUSINESS_CIVIL"),
    ),
    "Penalty": LegalConcept(
        "Penalty",
        "Penalty",
        "Consequence of a violation.",
        ("FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION"),
    ),
    "ImprovementNotice": LegalConcept(
        "ImprovementNotice", "Improvement Notice", "Notice requiring corrective action.", ("FOOD_SAFETY",)
    ),
    "Prohibition": LegalConcept(
        "Prohibition",
        "Prohibition",
        "Forbidden act.",
        (
            "FOOD_SAFETY",
            "ANIMAL_SLAUGHTER",
        ),
    ),
    "Obligation": LegalConcept(
        "Obligation", "Obligation", "A duty imposed by law.", ("FOOD_SAFETY", "ENVIRONMENT_POLLUTION")
    ),
    "Duty": LegalConcept("Duty", "Duty", "Specific responsibility imposed.", ("FOOD_SAFETY", "ENVIRONMENT_POLLUTION")),
    "Permission": LegalConcept(
        "Permission", "Permission", "Authorised exception to a rule.", ("FOOD_SAFETY", "MUNICIPAL")
    ),
    "Power": LegalConcept(
        "Power", "Power", "Delegated authority of an enforcer.", ("FOOD_SAFETY", "MUNICIPAL", "ENVIRONMENT_POLLUTION")
    ),
    "Procedure": LegalConcept(
        "Procedure", "Procedure", "Prescribed process for compliance or enforcement.", ("FOOD_SAFETY", "BUSINESS_CIVIL")
    ),
    "BUSINESS_CIVIL": LegalConcept(
        "BUSINESS_CIVIL",
        "Business Civil Law",
        "Commercial/business law relevant to food businesses.",
        ("BUSINESS_CIVIL",),
    ),
    "BusinessCivil": LegalConcept(
        "BusinessCivil",
        "Business Civil Law",
        "Commercial/business law relevant to food businesses.",
        ("BUSINESS_CIVIL",),
    ),
    "Contract": LegalConcept("Contract", "Contract", "Enforceable agreement.", ("BUSINESS_CIVIL",)),
    "ConsumerProtection": LegalConcept(
        "ConsumerProtection", "Consumer Protection", "Protection of consumer rights.", ("BUSINESS_CIVIL", "FOOD_SAFETY")
    ),
    "FoodAdulteration": LegalConcept(
        "FoodAdulteration",
        "Food Adulteration",
        "Addition of harmful/substandard substances.",
        ("FOOD_SAFETY", "PUBLIC_HEALTH"),
    ),
    "AnimalWelfare": LegalConcept(
        "AnimalWelfare", "Animal Welfare", "Humane treatment of animals.", ("ANIMAL_SLAUGHTER",)
    ),
    "Vehicles": LegalConcept("Vehicles", "Vehicles", "Mobile food vending units.", ("MUNICIPAL", "FOOD_SAFETY")),
}


# --------------------------------------------------------------------------- #
# Pilot Instruments (one per domain)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PilotInstrument:
    instrument_id: str
    title: str
    short_title: str
    instrument_type: str  # "act" | "rule" | "regulation" | "notification"
    legal_domain: str
    jurisdiction: str
    issuing_authority: str
    enactment_date: str | None  # ISO date or None
    effective_date: str | None
    status: str  # "current" | "repealed" | "amended"
    is_primary: bool  # True for the one act that defines the domain
    source_uri: str
    source_type: str  # "existing_db" | "manual"
    document_db_id: str | None  # for existing_db sources, the LegalDocument.id
    provisions: list[str] = field(default_factory=list)  # section numbers to create as stubs
    relationships: list[tuple[str, str, str]] = field(default_factory=list)  # (rel_type, target_id, description)


PILOT_INSTRUMENTS: list[PilotInstrument] = [
    # FOOD_SAFETY — primary, from existing DB
    PilotInstrument(
        instrument_id="FSS_ACT_2006",
        title="Food Safety and Standards Act, 2006",
        short_title="FSS Act, 2006",
        instrument_type="act",
        legal_domain="FOOD_SAFETY",
        jurisdiction="INDIA",
        issuing_authority="MO_LAW",
        enactment_date="2006-09-01",
        effective_date="2006-09-01",
        status="current",
        is_primary=True,
        source_uri="FSSAI_rules documents/Food_Safety_and_Standards_Act_2006.pdf",
        source_type="existing_db",
        document_db_id="60939e3b253847b9990a93fc10f5d723",
        provisions=[
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12",
            "13",
            "14",
            "15",
            "16",
            "17",
            "18",
            "19",
            "20",
            "21",
            "22",
            "23",
            "24",
            "25",
            "26",
            "27",
            "28",
            "29",
            "30",
            "31",
            "32",
            "33",
            "34",
            "35",
            "36",
            "37",
            "38",
            "39",
            "40",
            "41",
            "42",
            "43",
            "44",
            "45",
            "46",
            "47",
            "48",
            "49",
            "50",
            "51",
            "52",
            "53",
            "54",
            "55",
            "56",
            "57",
            "58",
            "59",
            "60",
            "61",
            "62",
            "63",
            "64",
            "65",
            "66",
            "67",
            "68",
            "69",
            "70",
            "71",
            "72",
            "73",
            "74",
            "75",
            "76",
            "77",
            "78",
            "79",
            "80",
            "81",
            "82",
            "83",
            "84",
            "85",
            "86",
            "87",
            "88",
            "89",
            "90",
            "91",
            "92",
            "93",
            "94",
            "95",
            "96",
            "97",
            "98",
            "99",
            "100",
            "101",
            "102",
            "103",
            "104",
        ],
        relationships=[
            ("AMENDS", "PFA_1954", "FSS Act, 2006 repealed the Prevention of Food Adulteration Act, 1954"),
        ],
    ),
    # ANIMAL_SLAUGHTER — stub
    PilotInstrument(
        instrument_id="WB_ANIMAL_SLAUGHTER_RULE_2023",
        title="West Bengal Animal Slaughter House Rules, 2023",
        short_title="WB Animal Slaughter Rules, 2023",
        instrument_type="rule",
        legal_domain="ANIMAL_SLAUGHTER",
        jurisdiction="WEST_BENGAL",
        issuing_authority="WB_FODDER_DEPT",
        enactment_date="2023-06-15",
        effective_date="2023-06-15",
        status="current",
        is_primary=True,
        source_uri="manual://wb_animal_slaughter_2023",
        source_type="manual",
        document_db_id=None,
        provisions=["1", "2", "3", "4", "5", "6", "7"],
        relationships=[
            ("MADE_UNDER", "PCA_1960", "Made under the Prevention of Cruelty to Animals Act, 1890"),
        ],
    ),
    # ENVIRONMENT_POLLUTION — stub
    PilotInstrument(
        instrument_id="ENV_PROTECTION_ACT_1986",
        title="Environment (Protection) Act, 1986",
        short_title="Environment Protection Act, 1986",
        instrument_type="act",
        legal_domain="ENVIRONMENT_POLLUTION",
        jurisdiction="INDIA",
        issuing_authority="MOEFCC",
        enactment_date="1986-11-19",
        effective_date="1986-11-19",
        status="current",
        is_primary=True,
        source_uri="manual://env_protection_act_1986",
        source_type="manual",
        document_db_id=None,
        provisions=["2", "3", "5", "6", "7", "8"],
        relationships=[
            ("RELATED_TO", "FSS_ACT_2006", "Both apply to food business operations"),
            ("RELATED_TO", "WATER_ACT_1974", "Water Act addresses wastewater discharge"),
        ],
    ),
    # MUNICIPAL — stub
    PilotInstrument(
        instrument_id="KMC_ACT_2009",
        title="Kolkata Municipal Corporation Act, 2009",
        short_title="KMC Act, 2009",
        instrument_type="act",
        legal_domain="MUNICIPAL",
        jurisdiction="KOLKATA",
        issuing_authority="KMC",
        enactment_date="2009-04-01",
        effective_date="2009-04-01",
        status="current",
        is_primary=True,
        source_uri="manual://kmc_act_2009",
        source_type="manual",
        document_db_id=None,
        provisions=["5", "6", "7", "11", "14", "40", "302"],
        relationships=[
            ("RELATED_TO", "FSS_ACT_2006", "Both apply to food business premises"),
        ],
    ),
    # PUBLIC_HEALTH — stub
    PilotInstrument(
        instrument_id="IPC_1860",
        title="Indian Penal Code, 1860",
        short_title="IPC, 1860",
        instrument_type="act",
        legal_domain="PUBLIC_HEALTH",
        jurisdiction="INDIA",
        issuing_authority="COURTS",
        enactment_date="1860-10-06",
        effective_date="1860-10-06",
        status="current",
        is_primary=True,
        source_uri="manual://ipc_1860",
        source_type="manual",
        document_db_id=None,
        provisions=["269", "270", "271", "272", "273", "274", "275", "276", "277", "278"],
        relationships=[],
    ),
    # BUSINESS_CIVIL — stub
    PilotInstrument(
        instrument_id="CONTRACT_ACT_1872",
        title="Indian Contract Act, 1872",
        short_title="Contract Act, 1872",
        instrument_type="act",
        legal_domain="BUSINESS_CIVIL",
        jurisdiction="INDIA",
        issuing_authority="COURTS",
        enactment_date="1872-03-01",
        effective_date="1872-03-01",
        status="current",
        is_primary=True,
        source_uri="manual://contract_act_1872",
        source_type="manual",
        document_db_id=None,
        provisions=["2", "10", "12", "23", "25", "28", "37", "56", "73", "74"],
        relationships=[],
    ),
    # LAND_PREMISES — stub
    PilotInstrument(
        instrument_id="WB_LAND_REFORMS_ACT_1955",
        title="West Bengal Land Reforms and Tenancy (Amendment) Act, 1955",
        short_title="WB Land Reforms Act, 1955",
        instrument_type="act",
        legal_domain="LAND_PREMISES",
        jurisdiction="WEST_BENGAL",
        issuing_authority="WB_LAND_RECORDS",
        enactment_date="1955-01-01",
        effective_date="1955-01-01",
        status="current",
        is_primary=True,
        source_uri="manual://wb_land_reforms_1955",
        source_type="manual",
        document_db_id=None,
        provisions=["1", "2", "3", "4", "5"],
        relationships=[
            ("RELATED_TO", "FSS_ACT_2006", "Land use affects food business establishment"),
        ],
    ),
]

#: All instrument IDs referenced in relationships (even stubs not in pilot set)
ALL_INSTRUMENT_IDS: set[str] = {inst.instrument_id for inst in PILOT_INSTRUMENTS} | {
    "PFA_1954",
    "PCA_1960",
    "WATER_ACT_1974",
    "AIR_ACT_1981",
    "SWDM_RULES_2016",
    "PLASTIC_WASTE_RULES_2016",
    "KMC_SANITATION_BYLAW",
    "KMC_TRADE_LICENCE_BYLAW",
}


# --------------------------------------------------------------------------- #
# Provision stubs for manual (non-DB) instruments
# --------------------------------------------------------------------------- #

# Minimal provision text stubs for manual instruments — keeps the KG useful
# even without full corpus ingestion.  Each maps section_number -> (title, text).
PROVISION_STUBS: dict[str, dict[str, tuple[str, str]]] = {
    "ENV_PROTECTION_ACT_1986": {
        "2": ("Definitions", "In this Act, unless the context otherwise requires, ..."),
        "3": (
            "Powers of Centre to take action",
            "Where the Central Government is of opinion that a person has caused or is about to cause environment",
        ),
        "5": (
            "Restriction on certain operations",
            "No person shall carry on any operation which is likely to cause adverse environmental effect.",
        ),
        "6": ("Powers to give directions", "The Central Government may, in writing, direct any person..."),
        "7": ("Closure of units", "The Central Government may, in writing, direct the closure of a unit..."),
        "8": ("Penalty", "Whoever contravenes any direction given under sub-section (6) shall be punished..."),
    },
    "WB_ANIMAL_SLAUGHTER_RULE_2023": {
        "1": (
            "Short title and commencement",
            "These rules shall be called the West Bengal Animal Slaughter House Rules, 2023.",
        ),
        "2": ("Definitions", "In these rules, unless the context otherwise requires, ..."),
        "3": ("Licensing of slaughter houses", "No person shall carry on the slaughter of animals without a license."),
        "4": ("Conditions of license", "Every license shall be subject to such conditions as may be imposed..."),
        "5": ("Slaughter timings", "No slaughter shall take place except during prescribed hours."),
        "6": ("Sanitation and hygiene", "Every slaughter house shall maintain the highest standards of cleanliness."),
        "7": ("Waste disposal", "Animal waste and carcasses shall be disposed of in an environmentally sound manner."),
    },
    "KMC_ACT_2009": {
        "5": ("Powers to make by-laws", "The Corporation may make by-laws for the good government of the city."),
        "6": ("Trade licences", "No person shall carry on any trade without a licence."),
        "7": ("Maintenance of lanes and drains", "The Corporation shall maintain public lanes and drains."),
        "11": ("Nuisances", "The Corporation may remove or abate any nuisance."),
        "14": ("Dangerous or offensive trades", "No person shall carry on any dangerous or offensive trade..."),
        "40": ("Removal of encroachments", "The Corporation may remove any encroachment on public land."),
        "302": ("Offences", "Whoever contravenes any provision of this Act shall be guilty of an offence."),
    },
    "IPC_1860": {
        "269": (
            "Malignantact done by knowing it to be likely to spread infection of disease dangerous to life",
            "Whoever ... knowing it to be likely ...",
        ),
        "270": ("Malignantact likely to spread infection of disease dangerous to life", "Whoever ... ..."),
        "271": ("Disobedience to order duly promulgated by public servant", "Whoever ... disobeys ..."),
        "272": ("Obscene nonsense such as to cause obstruction, annoyance or injury", "Whoever ..."),
        "273": ("Obscene act to cause annoyance", "Whoever ..."),
        "274": ("Whoever, to the annoyance of others, does any act", "Whoever ..."),
        "275": ("False evidence", "Whoever ..."),
        "276": ("False evidence", "Whoever ..."),
        "277": ("Fraudulent claim to property", "Whoever ..."),
        "278": ("Fraudulent claim to property", "Whoever ..."),
    },
    "CONTRACT_ACT_1872": {
        "2": ("Interpretation clause", "In this Act the following expressions shall have the meanings ..."),
        "10": ("What agreements are contracts", "All agreements are contracts if ..."),
        "12": ("What considerations and objects are lawful", "Subject to the provisions ..."),
        "23": ("Consideration unlawful, if ...", "The consideration or object of an agreement ..."),
        "25": ("Agreement void, whereby parties prevented from contracting", "An agreement ..."),
        "28": ("Agreements in restraint of marriage", "Agreements ..."),
        "37": ("Agreements on subject which ...", "All agreements ..."),
        "56": ("Agreement of restraint of trade", "An agreement ..."),
        "73": ("Compensation for loss caused by breach of contract", "When a contract ..."),
        "74": ("Substitution of lawful consideration", "When a contract ..."),
    },
    "WB_LAND_REFORMS_ACT_1955": {
        "1": ("Short title and extent", "This Act may be called the West Bengal Land Reforms Act, 1955."),
        "2": ("Definitions", "In this Act, unless there is anything repugnant ..."),
        "3": ("Ceiling on holdings", "No person shall hold land in excess of the ceiling area."),
        "4": ("Persons disqualified", "No person shall acquire ..."),
        "5": ("Prohibition on transfer", "No transfer of interest in land ..."),
    },
    "PFA_1954": {
        "1": ("Short title", "This Act may be called the Prevention of Food Adulteration Act, 1954."),
        "7": ("Sampling", "For the purposes of this Act ..."),
    },
    "PCA_1960": {
        "1": ("Short title", "This Act may be called the Prevention of Cruelty to Animals Act, 1890."),
        "11": ("Slaughtering", "No person shall slaughter ... without permission."),
    },
    "WATER_ACT_1974": {
        "2": ("Definitions", "In this Act, unless there is anything repugnant ..."),
        "5": ("Constitution of Boards", "There shall be a Central Board ..."),
        "26": ("Powers of State Board", "Subject to the provisions of this Act ..."),
    },
    "AIR_ACT_1981": {
        "2": ("Definitions", "In this Act, unless there is anything repugnant ..."),
        "5": ("Constitution of Boards", "There shall be a Central Board ..."),
    },
    "SWDM_RULES_2016": {
        "3": ("Responsibility for segregation", "The occupier ... shall segregate ..."),
        "4": ("Collection of solid waste", "Every local authority ... shall collect ..."),
    },
    "CONSUMER_PROTECTION_ACT_2019": {
        "2": ("Definitions", "In this Act, unless the context otherwise requires, ..."),
        "21": ("District Commission", "The State Government ... shall establish ..."),
    },
}


# --------------------------------------------------------------------------- #
# Cross-domain relationship registry
# --------------------------------------------------------------------------- #

# Relationships between provisions across domains — SOURCE-SUPPORTED ONLY.
# These are the cross-domain connections that justify a food-business
# operator being subject to multiple laws simultaneously.
# Format: (source_provision_id, rel_type, target_provision_id, evidence_description)
CROSS_DOMAIN_RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    # FSS Act inspection powers relate to slaughterhouse licensing
    (
        "FSS_ACT_2006_SEC_32",
        "INTERACTS_WITH",
        "WB_ANIMAL_SLAUGHTER_RULE_2023_SEC_3",
        "FSS Act Section 32 inspection powers are exercised at slaughterhouses licensed under WB Animal Slaughter Rules Section 3",
    ),
    # Environmental consent applies to food businesses with wastewater
    (
        "ENV_PROTECTION_ACT_1986_SEC_5",
        "COMPLEMENTS",
        "FSS_ACT_2006_SEC_31",
        "Environment (Protection) Act Section 5 restrictions on operations complement FSS Act Section 31's hygiene requirements",
    ),
    # KMC trade licence applies to food businesses
    (
        "KMC_ACT_2009_SEC_6",
        "CROSS_REFERENCES",
        "FSS_ACT_2006_SEC_31",
        "KMC Act Section 6 (trade licences) cross-references FSS Act Section 31 (fbo licensing)",
    ),
    # Contract law applies to food business supply chains
    (
        "CONTRACT_ACT_1872_SEC_73",
        "COMPLEMENTS",
        "FSS_ACT_2006_SEC_32",
        "Contract Act Section 73 (damages) complements FSS Act Section 32 (inspection powers)",
    ),
]


# --------------------------------------------------------------------------- #
# Provision-to-concept mappings (deterministic, evidence-tagged)
# --------------------------------------------------------------------------- #

# Maps provision_id -> list of (concept_id, relationship_type, evidence)
# These represent the legal reasoning layer: what each provision actually
# imposes or creates.
# Only provisions in the pilot set + their stubs are listed here.
PROVISION_CONCEPT_MAP: dict[str, list[tuple[str, str, str]]] = {
    # WB Meat Order, 1966 — s.3 (real corpus provision; verified 2026-08-11)
    # "slaughter house" means any place used for the slaughter of any animal
    # for the purpose of selling the flesh thereof as meat. 3. Prohibition of
    # slaughter of animal, sale of meat, etc.
    "WB_MEAT_ORDER_1966_SEC_3": [
        (
            "Slaughterhouse",
            "APPLIES_TO",
            "WB Meat Order, 1966 s.3 prohibits slaughter of animals and regulates slaughter houses",
        ),
    ],
    # FSS Act — key sections
    "FSS_ACT_2006_SEC_31": [
        (
            "FoodBusinessOperator",
            "APPLIES_TO",
            "FSS Act Section 31 imposes obligations on every food business operator",
        ),
        ("Obligation", "IMPOSES_DUTY", "Section 31(1): 'every food business operator shall ...'"),
        ("FoodBusiness", "APPLIES_TO", "Section 31 applies to food businesses"),
        ("FSO", "GRANTS_POWER_TO", "Section 39 power to enter/ inspect/ seize"),
    ],
    "FSS_ACT_2006_SEC_32": [
        ("FSO", "GRANTS_POWER_TO", "Section 32 grants FSO powers to take samples, inspect"),
        ("FoodBusiness", "APPLIES_TO", "Section 32 applies to food businesses"),
        ("Inspection", "PRESCRIBES", "Section 32 prescribes inspection procedures"),
        ("Sampling", "PRESCRIBES", "Section 32 prescribes sampling procedures"),
    ],
    "FSS_ACT_2006_SEC_33": [
        ("FSO", "GRANTS_POWER_TO", "Section 33 grants FSO power to collect samples"),
        ("Sampling", "PRESCRIBES", "Section 33 prescribes sample collection"),
    ],
    "FSS_ACT_2006_SEC_39": [
        ("FSO", "GRANTS_POWER_TO", "Section 39 grants FSO entry/inspection/seizure powers"),
        ("Inspection", "PRESCRIBES", "Section 39 prescribes inspection procedures"),
        ("FoodBusiness", "APPLIES_TO", "Section 39 applies to food businesses"),
    ],
    "FSS_ACT_2006_SEC_51": [
        ("Offence", "CREATES_OFFENCE", "Section 51 creates offence for possession of adulterated food"),
        ("FoodAdulteration", "RELATES_TO", "Section 51 concerns adulterated food"),
    ],
    "FSS_ACT_2006_SEC_52": [
        ("Offence", "CREATES_OFFENCE", "Section 52 creates offence for substandard food"),
        ("Offence", "CREATES_OFFENCE", "Section 52 creates offence for misbranded food"),
    ],
    "FSS_ACT_2006_SEC_55": [
        ("Offence", "CREATES_OFFENCE", "Section 55 creates offence for contravention of Act/rules"),
        ("Penalty", "PRESCRIBES_PENALTY", "Section 55 prescribes penalty for contravention"),
        ("FoodBusiness", "APPLIES_TO", "Section 55 applies to food businesses"),
    ],
    "FSS_ACT_2006_SEC_56": [
        ("Offence", "CREATES_OFFENCE", "Section 56 creates offence for continuing contravention"),
        ("Penalty", "PRESCRIBES_PENALTY", "Section 56 prescribes penalty for continuing contravention"),
    ],
    "FSS_ACT_2006_SEC_58": [
        ("Penalty", "PRESCRIBES_PENALTY", "Section 58 prescribes penalty for obstructing FSO"),
        ("FSO", "GRANTS_POWER_TO", "Section 58 protects FSO while exercising powers"),
    ],
    "FSS_ACT_2006_SEC_63": [
        ("Procedure", "PRESCRIBES", "Section 63 prescribes adjudication procedure"),
        ("Offence", "CREATES_OFFENCE", "Section 63 creates offence for non-compliance"),
    ],
    "FSS_ACT_2006_SEC_64": [
        ("Penalty", "PRESCRIBES_PENALTY", "Section 64 prescribes penalty for offences"),
        ("Offence", "CREATES_OFFENCE", "Section 64 creates offence for contravention"),
    ],
    # Animal Slaughter Rules
    "WB_ANIMAL_SLAUGHTER_RULE_2023_SEC_3": [
        ("Slaughterhouse", "APPLIES_TO", "Rule 3 applies to slaughter houses"),
        ("Licence", "REQUIRES", "Rule 3 requires a licence for slaughter"),
        ("WB_FODDER_DEPT", "GRANTS_POWER_TO", "Rule 3 grants licensing power to WB Dept"),
    ],
    "WB_ANIMAL_SLAUGHTER_RULE_2023_SEC_5": [
        ("AnimalSlaughter", "RELATES_TO", "Rule 5 concerns slaughter timings"),
        ("Slaughterhouse", "APPLIES_TO", "Rule 5 applies to slaughter houses"),
    ],
    # Environment Protection Act
    "ENV_PROTECTION_ACT_1986_SEC_5": [
        ("ENV_POLLUTION", "RELATES_TO", "Section 5 concerns environmental pollution"),
        ("Wastewater", "RELATES_TO", "Section 5 relates to effluent/water pollution"),
        ("MOEFCC", "GRANTS_POWER_TO", "Section 5 grants power to Centre/MoEFCC"),
    ],
    "ENV_PROTECTION_ACT_1986_SEC_6": [
        ("Authority", "GRANTS_POWER_TO", "Section 6 grants power to issue directions"),
        ("ConsentToOperate", "REQUIRES", "Section 6 requires consent for certain operations"),
    ],
    "ENV_PROTECTION_ACT_1986_SEC_7": [
        ("ENV_POLLUTION", "RELATES_TO", "Section 7 concerns closure of polluting units"),
        ("Authority", "GRANTS_POWER_TO", "Section 7 grants closure power"),
    ],
    # KMC Act
    "KMC_ACT_2009_SEC_6": [
        ("TradeLicence", "REQUIRES", "Section 6 requires trade licences"),
        ("FoodBusiness", "APPLIES_TO", "Section 6 applies to food businesses operating as trades"),
        ("KMC", "GRANTS_POWER_TO", "Section 6 grants KMC licensing power"),
    ],
    "KMC_ACT_2009_SEC_11": [
        ("Nuisance", "RELATES_TO", "Section 11 concerns nuisances"),
        ("Sanitation", "RELATES_TO", "Section 11 relates to sanitation"),
        ("KMC", "ENFORCED_BY", "Section 11 enforced by KMC"),
    ],
    # Contract Act
    "CONTRACT_ACT_1872_SEC_73": [
        ("Obligation", "IMPOSES_DUTY", "Section 73 imposes duty to compensate for breach"),
        ("BusinessCivil", "APPLIES_TO", "Section 73 applies to business contracts"),
        ("Penalty", "PRESCRIBES_PENALTY", "Section 73 prescribes compensation for breach"),
    ],
    # IPC
    "IPC_1860_SEC_277": [
        ("Offence", "CREATES_OFFENCE", "Section 277 creates offence for public nuisance by waste"),
        ("ENV_POLLUTION", "RELATES_TO", "Section 277 relates to environmental waste as public nuisance"),
    ],
    # Land Reforms
    "WB_LAND_REFORMS_ACT_1955_SEC_3": [
        ("Premises", "RELATES_TO", "Section 3 concerns land holdings affecting premises"),
        ("LandPremises", "RELATES_TO", "Section 3 relates to premises occupancy"),
    ],
}
