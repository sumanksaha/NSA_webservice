"""Tests for kg/hybrid.py — KGContextExpander (2026-08-11).

Unit tests run against a mock Neo4j driver — no network, no credentials.

Key behaviours under test:
- chunk IDs -> provisions via either ID space (chunk_id / qdrant_point_id)
- structured expansion (instrument, domain, status, authorities, provenance)
- related cross-reference expansion
- graceful degradation: no Neo4j configured / empty input / query error
- never raises
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dotenv import load_dotenv

load_dotenv()


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeRecord(dict):
    """Dict-like record (mirrors the neo4j driver's mapping Record)."""


class FakeResult:
    def __init__(self, records: list | None = None):
        self._records = records or []

    @property
    def records(self):
        return self._records


class FakeDriver:
    """Scripted driver: returns different records per query phase."""

    def __init__(self, chunk_rows=None, related_rows=None):
        self.calls: list[dict] = []
        self.chunk_rows = chunk_rows or []
        self.related_rows = related_rows or []

    def execute_query(self, cypher, parameters_=None, database_=None):
        self.calls.append({"cypher": cypher, "params": parameters_ or {}, "database": database_})
        if "CROSS_REFERENCES" in cypher:
            return FakeResult([FakeRecord(r) for r in self.related_rows])
        if "SUPPORTED_BY" in cypher:
            return FakeResult([FakeRecord(r) for r in self.chunk_rows])
        return FakeResult()


@pytest.fixture
def chunk_rows() -> list[dict]:
    return [
        {
            "chunk_id": "fss-chunk-1",
            "chunk_node_id": "fss-chunk-1",
            "qdrant_point_id": None,
            "provision_id": "FSS_ACT_2006_SEC_31",
            "provision_number": "31",
            "title": "Licensing of food business",
            "text": "No person shall commence or carry on any food business except under a licence...",
            "status": "current",
            "instrument_id": "FSS_ACT_2006",
            "instrument_title": "Food Safety and Standards Act, 2006",
            "legal_domain": "FOOD_SAFETY",
            "document_id": "fss_act_2006",
            "document_uri": "https://fssai.gov.in/act",
            "authority_name": "FSSAI",
        },
        {
            "chunk_id": "fss-chunk-2",
            "chunk_node_id": "fss-chunk-2",
            "qdrant_point_id": None,
            "provision_id": "FSS_ACT_2006_SEC_31",
            "provision_number": "31",
            "title": "Licensing of food business",
            "text": "Body text under section 31.",
            "status": "current",
            "instrument_id": "FSS_ACT_2006",
            "instrument_title": "Food Safety and Standards Act, 2006",
            "legal_domain": "FOOD_SAFETY",
            "document_id": "fss_act_2006",
            "document_uri": "https://fssai.gov.in/act",
            "authority_name": "FSSAI",
        },
        {
            "chunk_id": "env-pt-1",
            "chunk_node_id": "env-pt-1",
            "qdrant_point_id": None,
            "provision_id": "ENV_PROTECTION_ACT_1986_SEC_5",
            "provision_number": "5",
            "title": "Power to give directions",
            "text": "The Central Government may issue directions...",
            "status": "current",
            "instrument_id": "ENV_PROTECTION_ACT_1986",
            "instrument_title": "Environment (Protection) Act, 1986",
            "legal_domain": "ENVIRONMENT_POLLUTION",
            "document_id": "environment_protection_act_1986",
            "document_uri": "https://moef.gov.in/ep-act",
            "authority_name": "Ministry of Environment, Forest and Climate Change",
        },
    ]


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #


class TestExpandChunks:
    def test_expands_both_id_spaces(self, chunk_rows):
        from kg.hybrid import KGContextExpander

        driver = FakeDriver(chunk_rows=chunk_rows)
        expander = KGContextExpander(driver=driver, database="neo4j")
        result = expander.expand_chunks(["fss-chunk-1", "env-pt-1"])
        assert result["enabled"] is True
        assert result["error"] is None
        assert result["matched_chunks"] == 3  # fss-chunk-1 + fss-chunk-2 + env-pt-1
        provs = {p["provision_id"]: p for p in result["provisions"]}
        assert set(provs) == {"FSS_ACT_2006_SEC_31", "ENV_PROTECTION_ACT_1986_SEC_5"}
        fss = provs["FSS_ACT_2006_SEC_31"]
        assert fss["legal_domain"] == "FOOD_SAFETY"
        assert fss["status"] == "current"
        assert fss["instrument_title"] == "Food Safety and Standards Act, 2006"
        assert fss["document_uri"] == "https://fssai.gov.in/act"
        assert fss["authorities"] == ["FSSAI"]
        assert set(result["domains"]) == {"FOOD_SAFETY", "ENVIRONMENT_POLLUTION"}
        # Provision deduped despite two supporting chunks
        assert len([p for p in result["provisions"] if p["provision_id"] == "FSS_ACT_2006_SEC_31"]) == 1

    def test_deduplicates_input_ids(self, chunk_rows):
        from kg.hybrid import KGContextExpander

        driver = FakeDriver(chunk_rows=chunk_rows)
        expander = KGContextExpander(driver=driver, database="neo4j")
        result = expander.expand_chunks(["env-pt-1", "env-pt-1", "env-pt-1"])
        assert result["chunk_ids_input"] == 1
        assert len(driver.calls[0]["params"]["chunk_ids"]) == 1

    def test_related_provisions_attached(self, chunk_rows):
        from kg.hybrid import KGContextExpander

        related = [
            {
                "source_id": "FSS_ACT_2006_SEC_31",
                "related_id": "ENV_PROTECTION_ACT_1986_SEC_5",
                "related_number": "5",
                "related_title": "Power to give directions",
                "rel_type": "COMPLEMENTS",
                "evidence": "Cross-references environmental directions.",
                "related_domain": "ENVIRONMENT_POLLUTION",
            }
        ]
        driver = FakeDriver(chunk_rows=chunk_rows, related_rows=related)
        expander = KGContextExpander(driver=driver, database="neo4j")
        result = expander.expand_chunks(["fss-chunk-1"])
        fss = next(p for p in result["provisions"] if p["provision_id"] == "FSS_ACT_2006_SEC_31")
        assert fss["related"][0]["rel_type"] == "COMPLEMENTS"
        assert fss["related"][0]["related_id"] == "ENV_PROTECTION_ACT_1986_SEC_5"

    def test_empty_input(self):
        from kg.hybrid import KGContextExpander

        expander = KGContextExpander(driver=FakeDriver(), database="neo4j")
        result = expander.expand_chunks([])
        assert result["matched_chunks"] == 0
        assert result["provisions"] == []

    def test_no_neo4j_configured_degrades(self, monkeypatch):
        from kg.hybrid import KGContextExpander

        monkeypatch.delenv("NEO4J_URI", raising=False)
        monkeypatch.delenv("NEO4J_USERNAME", raising=False)
        monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
        # No injected driver either -> disabled, not an exception
        expander = KGContextExpander(driver=None, database="neo4j")
        result = expander.expand_chunks(["x"])
        assert result["enabled"] is False
        assert result["error"] == "Neo4j not configured"

    def test_query_error_degrades_to_empty(self, chunk_rows):
        from kg.hybrid import KGContextExpander

        driver = FakeDriver(chunk_rows=chunk_rows)
        expander = KGContextExpander(driver=driver, database="neo4j")
        # Force an exception mid-query
        expander._execute = MagicMock(side_effect=RuntimeError("boom"))
        result = expander.expand_chunks(["fss-chunk-1"])
        assert result["enabled"] is True  # configured but failed
        assert result["error"] == "boom"
        assert result["provisions"] == []
