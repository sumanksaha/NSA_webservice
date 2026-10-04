"""Tests for the corpus-driven legal KG rebuild (Option B — 2026-08-11).

Unit tests run against a mock Neo4j driver, a fake Qdrant client, a fake
manifest, and a stubbed FSS DB loader — no network, no credentials.

Key behaviours under test:
- manifest -> instrument mapping (ID map, wb_state per-doc domains, CRIMINAL)
- authority resolution (aliases + on-demand creation)
- section validation (year-like junk filtered, act-range enforcement)
- provision building from Qdrant payloads
- domain edges are planned for EVERY provision (the audit's D1 fix)
- FSS Document node + HAS_CHUNK provenance (the audit's D4 fix)
- cross-domain edges: corpus-truthful edges written, endpoints-missing skipped
- batched UNWIND writes, dry-run performs no writes
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

load_dotenv()


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class FakeResult:
    def __init__(self, records: list | None = None):
        self._records = records or []

    @property
    def records(self):
        return self._records


class FakeDriver:
    """Records every Cypher call; returns empty results."""

    def __init__(self):
        self.calls: list[dict] = []

    def execute_query(self, cypher, parameters_=None, database_=None):
        self.calls.append({"cypher": cypher, "params": parameters_ or {}, "database": database_})
        return FakeResult()


class FakeQdrant:
    """Minimal Qdrant double: a couple of payload points per collection."""

    def __init__(self):
        self.collections = [
            type("C", (), {"name": "env_legal_768"}),
            type("C", (), {"name": "commercial_legal_768"}),
        ]

    def get_collections(self):
        return type("R", (), {"collections": self.collections})()

    def scroll(
        self, collection_name=None, limit=None, with_payload=None, with_vectors=None, offset=None, scroll_filter=None
    ):
        points = {
            "env_legal_768": [
                {
                    "id": "env-pt-1",
                    "payload": {
                        "chunk_id": "env-pt-1",
                        "document_id": "environment_protection_act_1986",
                        "chunk_index": 0,
                        "chunk_text": "Section 5: Power to give directions — the Central Government may issue directions.",
                        "section_number": "5",
                        "section_title": "Power to give directions",
                        "document_type": "act",
                        "act_name": "Environment (Protection) Act, 1986",
                        "is_current": True,
                    },
                },
                {
                    "id": "env-pt-2",
                    "payload": {
                        "chunk_id": "env-pt-2",
                        "document_id": "environment_protection_act_1986",
                        "chunk_index": 1,
                        "chunk_text": "Body text under section 5.",
                        "section_number": "5",
                        "section_title": None,
                    },
                },
                {
                    "id": "env-pt-3",
                    "payload": {
                        "chunk_id": "env-pt-3",
                        "document_id": "environment_protection_act_1986",
                        "chunk_index": 2,
                        "chunk_text": "Cross-reference to the Act of 1986 elsewhere.",
                        "section_number": "1986",  # year-like junk -> filtered
                    },
                },
                {
                    "id": "env-pt-4",
                    "payload": {
                        "chunk_id": "env-pt-4",
                        "document_id": "environment_protection_act_1986",
                        "chunk_index": 3,
                        "chunk_text": "Section 12: miscellaneous.",
                        "section_number": "12",  # outside EP Act 1..26? no — 12 is inside
                    },
                },
            ],
            "commercial_legal_768": [],
        }
        page = points.get(collection_name, [])
        return page, None


@pytest.fixture
def fake_manifest(tmp_path: Path) -> Path:
    manifest = {
        "documents": [
            {
                "file": "ep_act_1986.pdf",
                "document_id": "environment_protection_act_1986",
                "title": "The Environment (Protection) Act, 1986",
                "document_type": "act",
                "authority": "Parliament of India",
                "jurisdiction": "India",
                "state": "",
                "domain": "env",
                "act_name": "Environment (Protection) Act, 1986",
                "enactment_date": "1986-05-23",
                "is_current": True,
            },
            {
                "file": "Kolkata_Municipal_Corporation_Act_1980.PDF",
                "document_id": "kmc_act_1980",
                "title": "The Kolkata Municipal Corporation Act, 1980",
                "document_type": "act",
                "authority": "West Bengal Legislature",
                "jurisdiction": "India",
                "state": "West Bengal",
                "domain": "wb_state",
                "act_name": "Kolkata Municipal Corporation Act, 1980",
                "enactment_date": "1980",
                "is_current": True,
            },
            {
                "file": "Bharatiya_Nyaya_Sanhita_2023.pdf",
                "document_id": "bharatiya_nyaya_sanhita_2023",
                "title": "The Bharatiya Nyaya Sanhita, 2023",
                "document_type": "act",
                "authority": "Parliament of India",
                "jurisdiction": "India",
                "state": "",
                "domain": "criminal",
                "act_name": "Bharatiya Nyaya Sanhita, 2023",
                "enactment_date": "2023-12-25",
                "effective_date": "2024-07-01",
                "is_current": True,
            },
            {
                "file": "draft-pwmrules-2022.pdf",
                "document_id": "pwm_draft_rules_2022",
                "title": "Draft Plastic Waste Management Rules, 2022",
                "document_type": "rule",
                "authority": "Ministry of Environment, Forest and Climate Change",
                "jurisdiction": "India",
                "state": "",
                "domain": "env",
                "act_name": "Environment (Protection) Act, 1986",
                "is_current": False,
                "notes": "DRAFT — not current law",
            },
        ]
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


@pytest.fixture
def engine(fake_manifest: Path) -> MagicMock:
    from kg.corpus_ingestion import KGCorpusIngestionEngine

    e = KGCorpusIngestionEngine(
        driver=FakeDriver(),
        database="neo4j",
        manifest_path=fake_manifest,
        qdrant_client=FakeQdrant(),
    )
    # Stub the FSS DB loaders so unit tests need no app/DB
    e.load_fss_documents = MagicMock(return_value=[])
    e.load_all_fss_chunks = MagicMock(return_value={})
    return e


class FakeFssQdrant:
    """Qdrant double holding only the FSSAI collection, FSS payload shape."""

    POINTS: ClassVar[list[dict]] = [
        {
            "id": "f1",
            "payload": {
                "chunk_id": "f1",
                "document_id": "11f9c5e8765e4c678c6b271d20ed426b",
                "document_title": "Food Additives Regulations-4",
                "document_uri": "FSSAI_rules documents\\Food_Additives_Regulations-4.pdf",
                "document_type": "regulation",
                "authority": "MINISTRY OF HEALTH AND FAMILY WELFARE",
                "act_name": "Food Safety and Standards Act, 2006",
                "instrument_id": "FSS_FOOD_ADDITIVES_REGULATIONS_4_11f9c5e8",
                "legal_domain": "FOOD_SAFETY",
                "is_current": True,
                "chunk_index": 900,
                "chunk_text": "2.9.8 Cumin (K Jeera) whole means the dried ...",
                "clause_number": "2.9.8",
                "section_number": None,
                "provision_ids": ["fssai:s2.9.8"],
                "provision_confidence": 0.88,
                "provision_modality": "obligation",
            },
        },
        {
            "id": "f2",
            "payload": {
                "chunk_id": "f2",
                "document_id": "11f9c5e8765e4c678c6b271d20ed426b",
                "document_title": "Food Additives Regulations-4",
                "document_uri": "FSSAI_rules documents\\Food_Additives_Regulations-4.pdf",
                "document_type": "regulation",
                "authority": "MINISTRY OF HEALTH AND FAMILY WELFARE",
                "act_name": "Food Safety and Standards Act, 2006",
                "instrument_id": "FSS_FOOD_ADDITIVES_REGULATIONS_4_11f9c5e8",
                "legal_domain": "FOOD_SAFETY",
                "is_current": True,
                "chunk_index": 901,
                "chunk_text": "(x) Insect damaged matter Not more than 1.0 percent",
                "clause_number": "2.9.8",
                "section_number": None,
                "provision_ids": ["fssai:s2.9.8"],
            },
        },
        {
            "id": "f3",
            "payload": {
                "chunk_id": "f3",
                "document_id": "cf9fdf64c87e48429c6ba51c2c5cf356",
                "document_title": "L-and-R oper content merged",
                "document_uri": "FSSAI_rules documents\\L-and-R.pdf",
                "document_type": "regulation",
                "authority": "Food Safety and Standards Authority of India",
                "act_name": "Food Safety and Standards Act, 2006",
                "instrument_id": "FSS_L_AND_R_OPER_CONTENT_MERGED_cf9fdf64",
                "legal_domain": "FOOD_SAFETY",
                "is_current": True,
                "chunk_index": 10,
                "chunk_text": "2.9.8 Food vans of caterers must be covered",
                "clause_number": "2.9.8",
                "section_number": None,
                "provision_ids": ["fssai:s2.9.8"],
            },
        },
    ]

    def get_collections(self):
        return type("R", (), {"collections": [type("C", (), {"name": "fssai_legal_768"})]})()

    def scroll(
        self, collection_name=None, limit=None, with_payload=None, with_vectors=None, offset=None, scroll_filter=None
    ):
        if collection_name != "fssai_legal_768":
            return [], None
        return list(self.POINTS), None


# --------------------------------------------------------------------------- #
# Mapping tests
# --------------------------------------------------------------------------- #


class TestMappings:
    def test_domain_mapping(self, engine):
        assert engine.resolve_domain({"domain": "env"}) == "ENVIRONMENT_POLLUTION"
        assert engine.resolve_domain({"domain": "commercial"}) == "BUSINESS_CIVIL"
        assert engine.resolve_domain({"domain": "animal"}) == "ANIMAL_SLAUGHTER"
        assert engine.resolve_domain({"domain": "criminal"}) == "CRIMINAL"
        assert engine.resolve_domain({"domain": "fssai"}) == "FOOD_SAFETY"
        # wb_state resolves per document
        assert engine.resolve_domain({"domain": "wb_state", "document_id": "kmc_act_1980"}) == "MUNICIPAL"
        assert (
            engine.resolve_domain({"domain": "wb_state", "document_id": "wb_premises_tenancy_act_1997"})
            == "LAND_PREMISES"
        )

    def test_fire_services_acts_map_to_fire_safety(self, engine):
        """Both Fire Services Acts must not fall through to the LAND_PREMISES default.

        ``resolve_domain`` silently defaults unmapped ``wb_state`` documents to
        ``LAND_PREMISES``, which misfiled fire-services law as land/premises
        law with no error. Regression guard for the 2026-10-03 OCR ingest.
        """
        for doc_id in ("wb_fire_services_act_1950", "wb_fire_services_amendment_act_2022"):
            assert engine.resolve_domain({"domain": "wb_state", "document_id": doc_id}) == "FIRE_SAFETY"

    def test_every_mapped_domain_is_registered(self, engine):
        """A domain name absent from ``kg.domain_manifest.DOMAINS`` yields NO
        domain edge: ``load_vocabularies`` MERGEs only the registry, and the
        write step ``MATCH``es a ``LegalDomain`` by that name. An unregistered
        name therefore fails silently, so assert the registry covers every
        mapping this module can produce."""
        from kg.corpus_ingestion import MANIFEST_DOMAIN_TO_KG, WB_STATE_DOMAIN_MAP
        from kg.domain_manifest import DOMAINS

        mapped = set(WB_STATE_DOMAIN_MAP.values()) | {d for d in MANIFEST_DOMAIN_TO_KG.values() if d}
        unregistered = sorted(mapped - set(DOMAINS))
        assert unregistered == [], f"domains used but never registered (would get no domain edge): {unregistered}"

    def test_jurisdiction_mapping(self, engine):
        assert engine.resolve_jurisdiction({"jurisdiction": "India", "state": ""}) == "INDIA"
        assert engine.resolve_jurisdiction({"jurisdiction": "India", "state": "West Bengal"}) == "WEST_BENGAL"

    def test_authority_alias(self, engine):
        assert engine.resolve_authority("Ministry of Environment, Forest and Climate Change", "env") == "MOEFCC"
        assert engine.resolve_authority("Parliament of India", "criminal") == "PARLIAMENT_OF_INDIA"
        assert engine.resolve_authority("fssai", "fssai") == "FSSAI"
        assert engine.resolve_authority("West Bengal Legislature", "wb_state") == "WB_LEGISLATURE"

    def test_authority_unknown_creates_deterministic_id(self, engine):
        aid = engine.resolve_authority("Some Totally New Board", "env")
        assert aid.startswith("AUTH_")
        # Same name -> same id (deterministic)
        assert engine.resolve_authority("Some Totally New Board", "env") == aid

    def test_instrument_id_map(self, engine):
        assert (
            engine.resolve_instrument_id({"document_id": "environment_protection_act_1986"})
            == "ENV_PROTECTION_ACT_1986"
        )
        assert engine.resolve_instrument_id({"document_id": "bharatiya_nyaya_sanhita_2023"}) == "BNS_2023"
        # Unknown docs must slug from the UNIQUE document_id — act_name is
        # shared by many documents of the same Act and would collide.
        other = engine.resolve_instrument_id({"document_id": "some_new_doc", "act_name": "Some New Act, 2026"})
        assert other == "SOME_NEW_DOC"

    def test_status_from_is_current(self, engine):
        assert engine.instrument_status({"is_current": True}) == "current"
        assert engine.instrument_status({"is_current": False, "notes": "DRAFT gazette"}) == "draft"
        assert engine.instrument_status({"is_current": False, "notes": "superseded by 2022 amendments"}) == "superseded"


# --------------------------------------------------------------------------- #
# Section validation
# --------------------------------------------------------------------------- #


class TestSectionValidation:
    def test_year_like_junk_filtered(self):
        from kg.corpus_ingestion import _clean_section, _valid_section

        assert _valid_section("5", None) is True
        assert _valid_section("1960", None) is False  # year
        assert _valid_section("2022", None) is False  # year
        assert _valid_section("0", None) is False
        assert _valid_section("158", None) is True
        assert _valid_section("", None) is False
        assert _clean_section("26(2)(ii)") == "26"
        assert _clean_section(None) is None

    def test_act_range_enforced(self):
        from app.rag.legal_sections import sections_for_act
        from kg.corpus_ingestion import _valid_section

        known = sections_for_act("Environment (Protection) Act, 1986")
        assert _valid_section("5", known) is True
        assert _valid_section("27", known) is False  # EP Act has 26 sections
        assert _valid_section("12", known) is True


# --------------------------------------------------------------------------- #
# Provision building
# --------------------------------------------------------------------------- #


class TestProvisionBuilding:
    def test_provisions_from_qdrant_sections(self, engine):
        chunks = [
            {
                "chunk_id": "a",
                "chunk_text": "Section 5: Power to give directions.",
                "section_number": "5",
                "section_title": "Power to give directions",
            },
            {"chunk_id": "b", "chunk_text": "Body.", "section_number": "5", "section_title": None},
            {"chunk_id": "c", "chunk_text": "Cross-ref 1986.", "section_number": "1986", "section_title": None},
            {"chunk_id": "d", "chunk_text": "Section 12: Misc.", "section_number": "12", "section_title": None},
        ]
        provs = engine.build_provisions("ENV_PROTECTION_ACT_1986", "Environment (Protection) Act, 1986", chunks)
        ids = {p["provision_id"] for p in provs}
        assert ids == {"ENV_PROTECTION_ACT_1986_SEC_5", "ENV_PROTECTION_ACT_1986_SEC_12"}
        by_num = {p["provision_number"]: p for p in provs}
        assert "1986" not in by_num  # year junk never becomes a provision
        sec5 = by_num["5"]
        # "a" and "b" declare s5; "c" declares the junk year 1986 and so
        # continues the section it sits in.
        assert len(sec5["chunk_ids"]) == 3
        assert "directions" in sec5["text"]
        assert "Body." in sec5["text"]

    def test_stub_fallback_provisions(self, engine):
        provs = engine.build_provisions("PFA_1954", None, [], fallback_stubs={"1": ("Short title", "PFA 1954 text.")})
        assert provs[0]["provision_id"] == "PFA_1954_SEC_1"
        assert provs[0]["source"] == "stub"
        assert provs[0]["confidence"] == 0.6

    def test_provision_text_accumulates_across_chunks(self, engine):
        """Regression: the header chunk of a section carries only the section
        number, so taking text from the first chunk left 476 provisions with a
        one-character body (``'9'``) and a ``skipped_short_text`` class."""
        chunks = [
            {
                "chunk_id": "h",
                "chunk_index": 0,
                "chunk_text": "9",
                "section_number": "9",
                "section_title": "Ascertainment of price",
            },
            {
                "chunk_id": "b1",
                "chunk_index": 1,
                "chunk_text": "The price may be fixed by the contract.",
                "section_number": None,
            },
            {
                "chunk_id": "b2",
                "chunk_index": 2,
                "chunk_text": "It may be left to be fixed in manner agreed.",
                "section_number": None,
            },
        ]
        provs = engine.build_provisions("SALE_OF_GOODS_ACT_1930", "Sale of Goods Act, 1930", chunks)
        text = provs[0]["text"]
        # The bare-number stub is dropped, both bodies survive.
        assert "fixed by the contract" in text
        assert "manner agreed" in text
        assert not text.startswith("9\n")

    def test_bare_section_stub_alone_gives_no_body(self, engine):
        """A section with nothing but its header stub still yields a provision,
        but an empty body rather than a misleading one-character body."""
        chunks = [{"chunk_id": "h", "chunk_index": 0, "chunk_text": "9", "section_number": "9"}]
        provs = engine.build_provisions("AIR_ACT_1981", "Air (Prevention and Control of Pollution) Act, 1981", chunks)
        assert [p["provision_id"] for p in provs] == ["AIR_ACT_1981_SEC_9"]
        assert provs[0]["text"] == ""

    def test_declared_section_propagates_to_following_chunks(self, engine):
        """Regression: the chunker writes ``section_number`` only on a
        section's header chunk, leaving 10,505 continuation chunks unlinked.
        A continuation chunk belongs to the section declared before it."""
        chunks = [
            {"chunk_id": "h9", "chunk_index": 0, "chunk_text": "9", "section_number": "9"},
            {"chunk_id": "b1", "chunk_index": 1, "chunk_text": "The price may be fixed.", "section_number": None},
            {"chunk_id": "b2", "chunk_index": 2, "chunk_text": "It may be left to be fixed.", "section_number": None},
            {"chunk_id": "h10", "chunk_index": 3, "chunk_text": "10", "section_number": "10"},
            {"chunk_id": "b3", "chunk_index": 4, "chunk_text": "Stipulations.", "section_number": None},
        ]
        mapping = engine.map_chunks_to_provisions("SALE_OF_GOODS_ACT_1930", None, chunks)
        assert mapping == {
            "h9": "SALE_OF_GOODS_ACT_1930_SEC_9",
            "b1": "SALE_OF_GOODS_ACT_1930_SEC_9",
            "b2": "SALE_OF_GOODS_ACT_1930_SEC_9",
            "h10": "SALE_OF_GOODS_ACT_1930_SEC_10",
            "b3": "SALE_OF_GOODS_ACT_1930_SEC_10",
        }
        provs = {p["provision_number"]: p for p in engine.build_provisions("SALE_OF_GOODS_ACT_1930", None, chunks)}
        assert "fixed by" in provs["9"]["text"] or "may be fixed" in provs["9"]["text"]
        assert "Stipulations" in provs["10"]["text"]

    def test_chunks_before_any_declaration_stay_unlinked(self, engine):
        """No declaration, no evidence — guessing is what made the registry-ref
        fallback unsafe, so a leading run of undeclared chunks stays out."""
        chunks = [
            {"chunk_id": "a", "chunk_index": 0, "chunk_text": "Preamble.", "section_number": None},
            {"chunk_id": "b", "chunk_index": 1, "chunk_text": "More preamble.", "section_number": None},
            {"chunk_id": "h", "chunk_index": 2, "chunk_text": "5", "section_number": "5"},
        ]
        mapping = engine.map_chunks_to_provisions("X_ACT", None, chunks)
        assert mapping == {"h": "X_ACT_SEC_5"}

    def test_registry_provision_refs_are_never_used_for_sections(self, engine):
        """Regression: ``provision_ids`` / ``provision_spans`` are frequently a
        single degenerate constant repeated over hundreds of chunks (``sog:s66``
        on 130 of 141 Sale of Goods chunks; ``epa:s26.5`` on 1,625 chunks).
        Resolving sections from them fabricated mega-provisions."""
        chunks = [
            {"chunk_id": "h", "chunk_index": 0, "chunk_text": "5", "section_number": "5"},
            {
                "chunk_id": "a",
                "chunk_index": 1,
                "chunk_text": "Body A.",
                "section_number": None,
                "provision_ids": ["sog:s66"],
                "provision_spans": [{"section": "66"}],
            },
            {
                "chunk_id": "b",
                "chunk_index": 2,
                "chunk_text": "Body B.",
                "section_number": None,
                "sections_covered": ["66"],
            },
        ]
        assert engine.map_chunks_to_provisions("SOG", None, chunks) == {
            "h": "SOG_SEC_5",
            "a": "SOG_SEC_5",
            "b": "SOG_SEC_5",
        }

    def test_subsection_qualified_keys_respect_act_range(self, engine):
        """A qualified key is only accepted when its head section is in range."""
        chunks = [
            {"chunk_id": "ok", "chunk_index": 0, "chunk_text": "Body.", "section_number": "3(ii)"},
            {"chunk_id": "year", "chunk_index": 1, "chunk_text": "Body.", "section_number": "1986(ii)"},
            {"chunk_id": "junk", "chunk_index": 2, "chunk_text": "Body.", "section_number": "Section 9"},
        ]
        provs = engine.build_provisions("COMPANIES_ACT_2013", "The Companies Act, 2013", chunks)
        assert [p["provision_number"] for p in provs] == ["3(ii)"]

    def test_map_chunks_agrees_with_build_provisions(self, engine):
        """The mapping and the provision builder must never disagree — that
        disagreement is what produced chunks pointing at nothing."""
        chunks = [
            {"chunk_id": "a", "chunk_index": 0, "chunk_text": "Body A.", "section_number": "12"},
            {"chunk_id": "b", "chunk_index": 1, "chunk_text": "Body B.", "section_number": None},
            {"chunk_id": "c", "chunk_index": 2, "chunk_text": "Body C.", "section_number": "1999"},
        ]
        provs = engine.build_provisions("BNS_2023", None, chunks)
        mapping = engine.map_chunks_to_provisions("BNS_2023", None, chunks)
        known = {p["provision_id"] for p in provs}
        assert set(mapping.values()) <= known
        # "b" continues s12; "c" declares the junk year 1999, so it never
        # becomes a provision and continues s12 instead.
        assert mapping == {
            "a": "BNS_2023_SEC_12",
            "b": "BNS_2023_SEC_12",
            "c": "BNS_2023_SEC_12",
        }
        assert {p["provision_number"] for p in provs} == {"12"}


# --------------------------------------------------------------------------- #
# FSSAI corpus (clause-keyed, Qdrant fallback when the local DB is empty)
# --------------------------------------------------------------------------- #


class TestFssProvisions:
    def _chunks(self):
        return [
            {
                "chunk_id": "c1",
                "chunk_text": "2.9.8 Cumin (K Jeera) whole means ...",
                "clause_number": "2.9.8",
                "provision_ids": ["fssai:s2.9.8"],
                "provision_confidence": 0.88,
                "provision_modality": "obligation",
            },
            {
                "chunk_id": "c2",
                "chunk_text": "(x) Insect damaged matter Not more than 1.0 percent",
                "clause_number": "2.9.8",
                "provision_ids": ["fssai:s2.9.8"],
            },
            {
                "chunk_id": "c3",
                "chunk_text": "2.9.7 Coriander whole means ...",
                "clause_number": "2.9.7",
                "provision_ids": ["fssai:s2.9.7"],
            },
        ]

    def test_clauses_group_and_keep_registry_ref(self, engine):
        provs = engine.build_fss_provisions("FSS_FAR4", self._chunks())
        assert [p["provision_id"] for p in provs] == ["FSS_FAR4_CLAUSE_2.9.7", "FSS_FAR4_CLAUSE_2.9.8"]
        p298 = next(p for p in provs if p["provision_number"] == "2.9.8")
        assert p298["provision_ref"] == "fssai:s2.9.8"
        assert p298["chunk_ids"] == ["c1", "c2"]
        assert p298["confidence"] == 0.88
        assert p298["modality"] == "obligation"

    def test_node_id_scoped_by_instrument(self, engine):
        # Clause 4 exists in 14 documents of the real corpus — the registry id
        # alone is not unique, so the node id must be instrument-scoped.
        chunks = [{"chunk_id": "a", "chunk_text": "t", "clause_number": "4", "provision_ids": ["fssai:s4"]}]
        one = engine.build_fss_provisions("INSTR_A", chunks)[0]
        two = engine.build_fss_provisions("INSTR_B", chunks)[0]
        assert one["provision_id"] != two["provision_id"]
        assert one["provision_ref"] == two["provision_ref"] == "fssai:s4"

    def test_chunk_without_provision_id_falls_back_to_clause(self, engine):
        provs = engine.build_fss_provisions(
            "FSS_FAR4", [{"chunk_id": "x", "chunk_text": "t", "clause_number": "2.9.8"}]
        )
        assert provs[0]["provision_id"] == "FSS_FAR4_CLAUSE_2.9.8"
        assert provs[0]["provision_ref"] == "fssai:s2.9.8"

    def test_chunks_without_clause_or_provision_dropped(self, engine):
        assert engine.build_fss_provisions("FSS_FAR4", [{"chunk_id": "x", "chunk_text": "t"}]) == []


class TestFssQdrantFallback:
    """The local LegalDocument/LegalChunk tables can be empty on a fresh DB."""

    def _engine(self, fake_manifest):
        from kg.corpus_ingestion import KGCorpusIngestionEngine

        e = KGCorpusIngestionEngine(
            driver=FakeDriver(),
            database="neo4j",
            manifest_path=fake_manifest,
            qdrant_client=FakeFssQdrant(),
        )
        e._fss_corpus = MagicMock(return_value=([], {}))  # empty local DB
        return e

    def test_documents_derived_from_payloads(self, fake_manifest):
        docs = self._engine(fake_manifest).load_fss_documents()
        assert len(docs) == 2
        far4 = next(d for d in docs if d["title"] == "Food Additives Regulations-4")
        assert far4["chunk_count"] == 2
        assert far4["qdrant_collection"] == "fssai_legal_768"
        assert far4["instrument_id"] == "FSS_FOOD_ADDITIVES_REGULATIONS_4_11f9c5e8"

    def test_chunks_grouped_by_document(self, fake_manifest):
        chunks = self._engine(fake_manifest).load_all_fss_chunks()
        assert sum(len(v) for v in chunks.values()) == 3
        far4 = chunks["11f9c5e8765e4c678c6b271d20ed426b"]
        assert {c["chunk_id"] for c in far4} == {"f1", "f2"}
        assert far4[0]["clause_number"] == "2.9.8"

    def test_payload_instrument_id_wins_over_derived_slug(self, fake_manifest):
        from kg.corpus_ingestion import KGCorpusIngestionEngine

        e = KGCorpusIngestionEngine(
            driver=FakeDriver(),
            database="neo4j",
            manifest_path=fake_manifest,
            qdrant_client=FakeFssQdrant(),
        )
        e._fss_corpus = MagicMock(return_value=([], {}))
        rows, _ = e._build_instrument_rows()
        fss = [r for r in rows if r["source_type"] == "existing_db"]
        assert fss
        assert all(r["instrument_id"].startswith("FSS_") for r in fss)
        assert "FSS_FOOD_ADDITIVES_REGULATIONS_4_11f9c5e8" in {r["instrument_id"] for r in fss}


# --------------------------------------------------------------------------- #
# Engine orchestration (dry-run + write plan)
# --------------------------------------------------------------------------- #


class TestEngine:
    def test_collect_plans_domain_edge_for_every_provision(self, engine):
        from kg.domain_manifest import DOMAINS

        collected = engine.collect()
        stats = collected["stats"]
        assert stats["provisions"] == stats["provisions_with_domain"]
        assert stats["provisions"] >= 2  # EP Act s.5 + s.12 (+ stubs)
        # Every provision row carries a REGISTERED legal_domain (the registry is
        # the source of truth — deriving from DOMAINS keeps this from drifting
        # when a new domain such as FIRE_SAFETY is added).
        for p in collected["provisions"]:
            assert p["legal_domain"] in DOMAINS

    def test_collect_documents_and_instruments(self, engine):
        collected = engine.collect()
        # 4 manifest docs + 3 structural stubs (PFA/IPC/PCA)
        assert len(collected["instruments"]) >= 7
        assert len(collected["documents"]) == len(collected["instruments"])
        iids = {i["instrument_id"] for i in collected["instruments"]}
        assert "ENV_PROTECTION_ACT_1986" in iids
        assert "KMC_ACT_1980" in iids
        assert "BNS_2023" in iids
        assert "PFA_1954" in iids  # repealed stub for the FSS Act repeal chain

    def test_draft_instrument_status(self, engine):
        collected = engine.collect()
        draft = next(i for i in collected["instruments"] if i["instrument_id"] == "PWM_DRAFT_RULES_2022")
        assert draft["status"] == "draft"

    def test_cross_domain_edges_only_for_existing_endpoints(self, engine):
        collected = engine.collect()
        written, skipped = [], []
        for src, rel, tgt, _ev in __import__(
            "kg.corpus_ingestion", fromlist=["CORPUS_CROSS_DOMAIN_EDGES"]
        ).CORPUS_CROSS_DOMAIN_EDGES:
            if src in collected["provision_ids"] and tgt in collected["provision_ids"]:
                written.append((src, rel, tgt))
            else:
                skipped.append(src)
        # EP s.5 exists in the fake corpus -> edge kept
        assert (
            "ENV_PROTECTION_ACT_1986_SEC_5",
            "COMPLEMENTS",
            "FSS_ACT_2006_SEC_31",
        ) not in written  # FSS not in fake DB
        # FSS_ACT_2006_SEC_31 does not exist in unit tests (FSS loader stubbed) -> edge skipped

    def test_dry_run_writes_nothing(self, engine):
        engine._driver.calls.clear()
        summary = engine.run_rebuild(clear=True, dry_run=True)
        assert summary["dry_run"] is True
        assert engine._driver.calls == []  # zero Cypher executed

    def test_run_rebuild_issues_batched_writes(self, engine):
        engine.run_rebuild(clear=False, dry_run=False)
        calls = engine._driver.calls
        assert any("UNWIND $rows" in c["cypher"] for c in calls)
        # every provision gets a BELONGS_TO_DOMAIN row batch
        domain_batch = next(
            (c for c in calls if "BELONGS_TO_DOMAIN" in c["cypher"] and "LegalProvision" in c["cypher"]), None
        )
        assert domain_batch is not None
        assert all("legal_domain" in r for r in domain_batch["params"]["rows"])
        # FSS document node + HAS_CHUNK edge batch present (D4 fix)
        assert any("HAS_CHUNK" in c["cypher"] for c in calls)
        # stub instruments written
        assert any("PFA_1954" in json.dumps(c["params"]) for c in calls)

    def test_concept_edges_target_concepts_and_authorities(self, engine):
        # write_concept_edges Cypher must match on concept_id OR authority_id

        prov_ids = {"FSS_ACT_2006_SEC_31"}
        # FSS provisions don't exist in the fake DB -> everything skipped
        result = engine.write_concept_edges(prov_ids)
        assert result["skipped"]  # the map's FSS ids are not in the fake graph
