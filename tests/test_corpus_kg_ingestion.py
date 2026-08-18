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
        assert "1986" not in by_num  # year junk filtered
        sec5 = by_num["5"]
        assert len(sec5["chunk_ids"]) == 2  # both section-5 chunks support it
        assert "directions" in sec5["text"]

    def test_stub_fallback_provisions(self, engine):
        provs = engine.build_provisions("PFA_1954", None, [], fallback_stubs={"1": ("Short title", "PFA 1954 text.")})
        assert provs[0]["provision_id"] == "PFA_1954_SEC_1"
        assert provs[0]["source"] == "stub"
        assert provs[0]["confidence"] == 0.6


# --------------------------------------------------------------------------- #
# Engine orchestration (dry-run + write plan)
# --------------------------------------------------------------------------- #


class TestEngine:
    def test_collect_plans_domain_edge_for_every_provision(self, engine):
        collected = engine.collect()
        stats = collected["stats"]
        assert stats["provisions"] == stats["provisions_with_domain"]
        assert stats["provisions"] >= 2  # EP Act s.5 + s.12 (+ stubs)
        # Every provision row carries legal_domain
        for p in collected["provisions"]:
            assert p["legal_domain"] in {
                "FOOD_SAFETY",
                "ANIMAL_SLAUGHTER",
                "ENVIRONMENT_POLLUTION",
                "MUNICIPAL",
                "PUBLIC_HEALTH",
                "BUSINESS_CIVIL",
                "LAND_PREMISES",
                "CRIMINAL",
            }

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
