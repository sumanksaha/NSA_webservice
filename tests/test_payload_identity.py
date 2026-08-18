"""Tests for kg/payload_identity.py — QdrantPayloadStamper (2026-08-11).

Unit tests run against a fake Qdrant client + fake manifest + stubbed FSS
loaders — no network, no credentials.

Key behaviours under test:
- provision_id/instrument_id/legal_domain/status derived from the manifest
- section validation (year-like junk rejected; act-range enforced)
- unknown documents get collection-domain only (no guessed IDs)
- dry-run performs NO writes
- stamp() is idempotent (missing/different fields only; re-run no-op)
- payload index creation is best-effort
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


class FakeCollection:
    def __init__(self, name: str):
        self.name = name


class FakeClient:
    """Qdrant client double: scroll pages + records set_payload / indexes."""

    def __init__(self, points_by_collection: dict[str, list[dict]]):
        self.collections = [FakeCollection(name) for name in points_by_collection]
        self.points_by_collection = points_by_collection
        self.set_payload_calls: list[dict] = []
        self.index_calls: list[tuple[str, str]] = []

    def get_collections(self):
        return type("R", (), {"collections": self.collections})()

    def scroll(self, collection_name=None, limit=None, with_payload=None, with_vectors=None, offset=None):
        page = self.points_by_collection.get(collection_name, [])
        if offset is not None:
            return [], None
        return page[:limit], None

    def set_payload(self, collection_name=None, payload=None, points=None):
        self.set_payload_calls.append({"collection": collection_name, "payload": payload, "points": list(points or [])})
        # Apply the payload to the stored points so a second run sees them.
        for pt in self.points_by_collection.get(collection_name, []):
            if pt["id"] in (points or []):
                pt["payload"].update(payload or {})

    def create_payload_index(self, collection_name=None, field_name=None, field_schema=None):
        self.index_calls.append((collection_name, field_name))


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
            {
                "file": "kmc_act_1980.pdf",
                "document_id": "kmc_act_1980",
                "title": "The Kolkata Municipal Corporation Act, 1980",
                "document_type": "act",
                "authority": "West Bengal Legislature",
                "jurisdiction": "India",
                "state": "West Bengal",
                "domain": "wb_state",
                "act_name": "Kolkata Municipal Corporation Act, 1980",
                "is_current": True,
            },
        ]
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def make_points() -> dict[str, list[dict]]:
    return {
        "env_legal_768": [
            {
                "id": "env-pt-1",
                "payload": {
                    "chunk_id": "env-pt-1",
                    "document_id": "environment_protection_act_1986",
                    "section_number": "5",
                    "act_name": "Environment (Protection) Act, 1986",
                },
            },
            {
                "id": "env-pt-2",
                "payload": {
                    "chunk_id": "env-pt-2",
                    "document_id": "environment_protection_act_1986",
                    "section_number": "27",  # outside EP Act 1..26 -> invalid
                },
            },
            {
                "id": "env-pt-3",
                "payload": {
                    "chunk_id": "env-pt-3",
                    "document_id": "environment_protection_act_1986",
                    "section_number": "1986",  # year-like -> invalid
                },
            },
            {
                "id": "env-pt-4",
                "payload": {
                    "chunk_id": "env-pt-4",
                    "document_id": "pwm_draft_rules_2022",
                    "section_number": "10",
                },
            },
            {
                "id": "env-pt-5",
                "payload": {
                    "chunk_id": "env-pt-5",
                    "document_id": "unknown_doc_123",  # no manifest row
                    "section_number": "7",
                },
            },
        ],
        "wb_state_legal_768": [
            {
                "id": "kmc-pt-1",
                "payload": {
                    "chunk_id": "kmc-pt-1",
                    "document_id": "kmc_act_1980",
                    "section_number": "6",
                },
            }
        ],
    }


@pytest.fixture
def stamper(fake_manifest: Path):
    from kg.corpus_ingestion import KGCorpusIngestionEngine
    from kg.payload_identity import QdrantPayloadStamper

    engine = KGCorpusIngestionEngine(
        driver=MagicMock(),
        database="neo4j",
        manifest_path=fake_manifest,
        qdrant_client=FakeClient(make_points()),
    )
    # Stub the FSS DB loaders so unit tests need no app/DB
    engine.load_fss_documents = MagicMock(return_value=[])
    engine.load_all_fss_chunks = MagicMock(return_value={})
    return QdrantPayloadStamper(engine=engine)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class TestDocIdentity:
    def test_manifest_docs_resolved(self, stamper):
        identity = stamper.doc_identity_map()
        ep = identity["environment_protection_act_1986"]
        assert ep["instrument_id"] == "ENV_PROTECTION_ACT_1986"
        assert ep["legal_domain"] == "ENVIRONMENT_POLLUTION"
        assert ep["status"] == "current"
        assert ep["act_name"] == "Environment (Protection) Act, 1986"

    def test_draft_status(self, stamper):
        identity = stamper.doc_identity_map()
        assert identity["pwm_draft_rules_2022"]["status"] == "draft"

    def test_wb_state_domain_per_document(self, stamper):
        identity = stamper.doc_identity_map()
        assert identity["kmc_act_1980"]["legal_domain"] == "MUNICIPAL"


# --------------------------------------------------------------------------- #
# Point resolution
# --------------------------------------------------------------------------- #


class TestFieldsForPoint:
    def test_full_stamp_for_manifest_doc(self, stamper):
        fields = stamper._fields_for_point(
            "env_legal_768", {"document_id": "environment_protection_act_1986", "section_number": "5"}
        )
        assert fields == {
            "provision_id": "ENV_PROTECTION_ACT_1986_SEC_5",
            "instrument_id": "ENV_PROTECTION_ACT_1986",
            "legal_domain": "ENVIRONMENT_POLLUTION",
            "status": "current",
        }

    def test_section_outside_act_range_no_provision(self, stamper):
        fields = stamper._fields_for_point(
            "env_legal_768", {"document_id": "environment_protection_act_1986", "section_number": "27"}
        )
        assert "provision_id" not in fields
        assert fields["instrument_id"] == "ENV_PROTECTION_ACT_1986"

    def test_year_like_section_rejected(self, stamper):
        fields = stamper._fields_for_point(
            "env_legal_768", {"document_id": "environment_protection_act_1986", "section_number": "1986"}
        )
        assert "provision_id" not in fields

    def test_draft_status_flows(self, stamper):
        fields = stamper._fields_for_point(
            "env_legal_768", {"document_id": "pwm_draft_rules_2022", "section_number": "10"}
        )
        assert fields["status"] == "draft"

    def test_unknown_document_collection_domain_only(self, stamper):
        fields = stamper._fields_for_point("env_legal_768", {"document_id": "unknown_doc_123", "section_number": "7"})
        assert fields == {"legal_domain": "ENVIRONMENT_POLLUTION"}  # no guessed IDs

    def test_subsection_cleaned(self, stamper):
        fields = stamper._fields_for_point(
            "env_legal_768", {"document_id": "environment_protection_act_1986", "section_number": "5(2)(ii)"}
        )
        assert fields["provision_id"] == "ENV_PROTECTION_ACT_1986_SEC_5"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class TestPlanAndStamp:
    def test_plan_reports_and_does_not_write(self, stamper):
        client = stamper._engine._get_qdrant_client()
        plan = stamper.plan()
        assert plan["total_points"] == 6
        assert plan["points_to_update"] == 6
        # env-pt-1 gets all four fields; env-pt-5 only domain
        assert "ENV_PROTECTION_ACT_1986_SEC_5" in str(plan)
        assert client.set_payload_calls == []  # zero writes in dry-run
        assert client.index_calls == []

    def test_stamp_writes_grouped_payloads(self, stamper):
        client = stamper._engine._get_qdrant_client()
        summary = stamper.stamp(create_indexes=False)
        assert summary["points_updated"] == 6
        assert len(client.set_payload_calls) >= 2  # grouped by field-set
        # every call carries only the four identity fields
        for call in client.set_payload_calls:
            assert set(call["payload"].keys()) <= {"provision_id", "instrument_id", "legal_domain", "status"}
        assert client.index_calls == []  # create_indexes=False

    def test_stamp_creates_indexes(self, stamper):
        client = stamper._engine._get_qdrant_client()
        stamper.stamp(create_indexes=True)
        assert len(client.index_calls) >= 2  # 4 fields x 2 collections (best-effort)
        fields = {f for _, f in client.index_calls}
        assert fields >= {"provision_id", "instrument_id", "legal_domain", "status"}

    def test_stamp_idempotent_second_run_noop(self, stamper):
        client = stamper._engine._get_qdrant_client()
        first = stamper.stamp(create_indexes=False)
        assert first["points_updated"] == 6
        calls_after_first = len(client.set_payload_calls)
        second = stamper.stamp(create_indexes=False)
        assert second["points_updated"] == 0
        assert len(client.set_payload_calls) == calls_after_first  # nothing more written

    def test_collection_filter(self, stamper):
        client = stamper._engine._get_qdrant_client()
        summary = stamper.stamp(collections=["env_legal_768"], create_indexes=False)
        assert summary["points_updated"] == 5
        assert client.set_payload_calls[0]["collection"] == "env_legal_768"
