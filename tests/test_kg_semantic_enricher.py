"""Tests for kg/enrichment.py — LegalSemanticEnricher (2026-08-11).

Unit tests run against a mock Neo4j driver — no network, no credentials.

Key behaviours under test:
- rule precedence (PROHIBITS beats IMPOSES_DUTY, penalty beats offence)
- evidence fragments + confidence capture
- short-text skip (OCR noise guard)
- concept-target validity (edges only land on the controlled vocabulary)
- dry-run performs no writes
- batched UNWIND write shape (no APOC)
"""

from __future__ import annotations

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
    """Records every Cypher call; returns configurable results per query."""

    def __init__(self, provisions: list[dict] | None = None):
        self.calls: list[dict] = []
        self.provisions = provisions or []

    def execute_query(self, cypher, parameters_=None, database_=None):
        self.calls.append({"cypher": cypher, "params": parameters_ or {}, "database": database_})
        if "MATCH (p:LegalProvision)" in cypher and "UNWIND" not in cypher:
            return FakeResult([FakeRecord(p) for p in self.provisions])
        return FakeResult()


@pytest.fixture
def provisions() -> list[dict]:
    return [
        {
            "provision_id": "FSS_ACT_2006_SEC_31",
            "provision_number": "31",
            "title": "Licensing of food business",
            "provision_text": (
                "No person shall commence or carry on any food business except under a licence. "
                "Every food business operator shall obtain a licence from the Authority. "
                "Whoever contravenes this section shall be punishable with imprisonment for a "
                "term which may extend to six months, and also with fine which may extend to "
                "five lakh rupees."
            ),
            "legal_domain": "FOOD_SAFETY",
            "instrument_id": "FSS_ACT_2006",
        },
        {
            "provision_id": "FSS_ACT_2006_SEC_32",
            "provision_number": "32",
            "title": "Power of Food Safety Officer",
            "provision_text": (
                "The Food Safety Officer may take samples of any article of food, inspect any "
                "food business premises, and seize articles in the prescribed manner."
            ),
            "legal_domain": "FOOD_SAFETY",
            "instrument_id": "FSS_ACT_2006",
        },
        {
            "provision_id": "SHORT_1",
            "provision_number": "1",
            "title": "Short",
            "provision_text": "Very short OCR fragment.",  # < MIN_TEXT_CHARS -> skipped
            "legal_domain": "FOOD_SAFETY",
            "instrument_id": "X",
        },
    ]


# --------------------------------------------------------------------------- #
# Pure tagging
# --------------------------------------------------------------------------- #


class TestTagText:
    def test_prohibition_beats_duty(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "No person shall commence or carry on any food business except under a licence."
        )
        rels = {t["rel_type"] for t in tags}
        assert "PROHIBITS" in rels
        assert "IMPOSES_DUTY" not in rels  # "shall" present but prohibition wins category dedupe

    def test_penalty_beats_bare_offence(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "Whoever contravenes this section shall be punishable with imprisonment for six months "
            "and also with fine."
        )
        rels = {t["rel_type"] for t in tags}
        assert "PRESCRIBES_PENALTY" in rels
        # Only one Penalty edge (highest confidence), never a bare CREATES_OFFENCE
        penalties = [t for t in tags if t["rel_type"] == "PRESCRIBES_PENALTY"]
        assert len(penalties) == 1
        assert penalties[0]["confidence"] == 0.95

    def test_offence_tagged(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text("Any person who commits an offence under this Act is liable to punishment.")
        assert {t["rel_type"] for t in tags} == {"CREATES_OFFENCE"}

    def test_power_tagged(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "The Food Safety Officer is empowered to take samples and is authorized to inspect any premises."
        )
        rels = {t["rel_type"] for t in tags}
        assert "GRANTS_POWER_TO" in rels
        # "empowered"/"authorized to" are 0.8-confidence; bare "may" (0.6)
        # is gated by MIN_CONFIDENCE and never written alone
        may_power = [t for t in tags if t["rel_type"] == "GRANTS_POWER_TO"]
        assert all(t["confidence"] >= 0.7 for t in may_power)
        assert len(may_power) == 1  # deduped to the strongest match

    def test_evidence_is_sentence_fragment(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "No person shall commence or carry on any food business except under a licence."
        )
        prohibition = next(t for t in tags if t["rel_type"] == "PROHIBITS")
        assert "shall" in prohibition["evidence"].lower()

    def test_short_text_returns_empty(self):
        from kg.enrichment import LegalSemanticEnricher

        assert LegalSemanticEnricher.tag_text("short") == []
        assert LegalSemanticEnricher.tag_text("") == []

    def test_all_concepts_in_vocabulary(self):
        from kg.enrichment import SEMANTIC_RULES

        # Every concept_id in the rule set must exist in the graph vocabulary
        from kg.domain_manifest import CONCEPTS

        for _, concept_id, _, _ in SEMANTIC_RULES:
            assert concept_id in CONCEPTS, f"{concept_id} missing from domain_manifest.CONCEPTS"


# --------------------------------------------------------------------------- #
# Orchestration (mock driver)
# --------------------------------------------------------------------------- #


class TestEnrich:
    def test_dry_run_writes_nothing(self, provisions):
        from kg.enrichment import LegalSemanticEnricher

        driver = FakeDriver(provisions=provisions)
        enricher = LegalSemanticEnricher(driver=driver, database="neo4j")
        summary = enricher.enrich(dry_run=True)
        assert summary["dry_run"] is True
        assert summary["provisions_loaded"] == 3
        assert summary["skipped_short_text"] == 1
        assert summary["edges_planned"] > 0
        # No UNWIND edge-write call
        assert not any("UNWIND $rows" in c["cypher"] for c in driver.calls)

    def test_enrich_writes_batched_edges(self, provisions):
        from kg.enrichment import LegalSemanticEnricher

        driver = FakeDriver(provisions=provisions)
        enricher = LegalSemanticEnricher(driver=driver, database="neo4j", batch_size=500)
        summary = enricher.enrich(dry_run=False)
        assert summary["edges_written"] == summary["edges_planned"]
        # Edge-write batches carry the MERGE shape; the separate semantic-
        # class batch (SET p.semantic_class) is excluded and asserted below.
        writes = [
            c for c in driver.calls
            if "UNWIND $rows" in c["cypher"] and "SET p.semantic_class" not in c["cypher"]
        ]
        assert writes
        for w in writes:
            assert "MERGE (p)-[rel:" in w["cypher"]
            for row in w["params"]["rows"]:
                assert row["provision_id"] in {"FSS_ACT_2006_SEC_31", "FSS_ACT_2006_SEC_32"}
                assert row["rel_type"] in {
                    "PROHIBITS", "IMPOSES_DUTY", "CREATES_OFFENCE", "PRESCRIBES_PENALTY",
                    "GRANTS_POWER_TO", "GRANTS_PERMISSION", "PRESCRIBES",
                }
                assert row["evidence"]
                assert 0.0 <= row["confidence"] <= 1.0

    def test_domain_filter(self, provisions):
        from kg.enrichment import LegalSemanticEnricher

        driver = FakeDriver(provisions=provisions)
        enricher = LegalSemanticEnricher(driver=driver, database="neo4j")
        enricher.enrich(domain="ENVIRONMENT_POLLUTION", dry_run=True)
        load_calls = [c for c in driver.calls if "LegalProvision" in c["cypher"] and "UNWIND" not in c["cypher"]]
        assert load_calls
        assert load_calls[0]["params"]["domain"] == "ENVIRONMENT_POLLUTION"

    def test_no_apoc_used(self, provisions):
        from kg.enrichment import LegalSemanticEnricher

        driver = FakeDriver(provisions=provisions)
        LegalSemanticEnricher(driver=driver, database="neo4j").enrich(dry_run=False)
        assert not any("apoc." in c["cypher"].lower() for c in driver.calls)
