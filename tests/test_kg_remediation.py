"""Tests for the KG semantic & structural remediation (P1-P4, 2026-08-11).

Covers the four fixes against mock Neo4j drivers (no network, no credentials):

- P3: scripts/remediate_kg_temporal.py — status propagation + effective_from
  backfill; dry-run rolls back (no writes); risk query must hit 0.
- P4: scripts/backfill_kg_provision_types.py — mechanical provision_type
  classification + Section-label wiring.
- P2: kg/concept_linking.py — synonym grounding, PREMATURE_TAXONOMY
  classification, plan/write separation.
- P1: kg/enrichment.py — extended rules (penalty-schedule rows, glued-text
  BNS artifacts, prohibition-of) + semantic_class tagging (not_applicable
  vs unclassified vs skipped_short_text).
"""

from __future__ import annotations

import pytest

from dotenv import load_dotenv

load_dotenv()


# --------------------------------------------------------------------------- #
# Shared fakes
# --------------------------------------------------------------------------- #


class FakeRecord(dict):
    """Dict-like neo4j record."""


class FakeResult(list):
    """Iterable result with ``single()`` and ``records`` (mirrors the driver)."""

    def single(self):
        return self[0] if self else None

    @property
    def records(self):
        return self


class FakeTx:
    def __init__(self, driver):
        self.driver = driver
        self.rolled_back = False
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return FakeResult([FakeRecord({"n": self.driver.ret_count(cypher)})])

    def rollback(self):
        self.rolled_back = True


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def run(self, cypher, **params):
        self.driver.session_calls.append((cypher, params))
        if self.driver.load_rows is not None and "RETURN p.provision_id AS provision_id" in cypher:
            return FakeResult([FakeRecord(r) for r in self.driver.load_rows])
        if self.driver.verify_rows is not None and "sum(CASE WHEN coalesce(p.provision_type" in cypher:
            return FakeResult([FakeRecord(r) for r in self.driver.verify_rows])
        if self.driver.label_count is not None and "MATCH (s:Section) RETURN count(*) AS n" in cypher:
            return FakeResult([FakeRecord({"n": self.driver.label_count})])
        return FakeResult([FakeRecord({"n": self.driver.ret_count(cypher)})])

    def begin_transaction(self):
        tx = FakeTx(self.driver)
        self.driver.txs.append(tx)
        return tx

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDriver:
    """Neo4j driver double: session.run + transaction path, count dispatch.

    ``counts`` maps a Cypher substring to an int OR a list of ints consumed
    in call order (for queries executed twice, e.g. the risk query before
    and after a fix).
    """

    def __init__(self, counts: dict | None = None, load_rows: list | None = None,
                 verify_rows: list | None = None, label_count: int | None = None):
        self.counts = counts or {}
        self.load_rows = load_rows
        self.verify_rows = verify_rows
        self.label_count = label_count
        self.session_calls: list[tuple[str, dict]] = []
        self.txs: list[FakeTx] = []
        self._seen = {k: 0 for k in self.counts}

    def ret_count(self, cypher: str) -> int:
        for key, val in self.counts.items():
            if key in cypher:
                if isinstance(val, list):
                    i = min(self._seen[key], len(val) - 1)
                    self._seen[key] += 1
                    return val[i]
                return val
        return 0

    def session(self, database=None):
        return FakeSession(self)


# --------------------------------------------------------------------------- #
# P3 — temporal remediation
# --------------------------------------------------------------------------- #


class TestTemporalRemediation:
    def test_risk_query_cypher_is_the_legal_risk_metric(self):
        from scripts.remediate_kg_temporal import _RISK_CYPHER

        assert "i.status <> 'current'" in _RISK_CYPHER
        assert "coalesce(p.status, 'current') = 'current'" in _RISK_CYPHER

    def test_live_remediate_propagates_and_backfills(self):
        from scripts.remediate_kg_temporal import remediate

        drv = FakeDriver(counts={
            # risk query runs twice: 5 before, 0 after
            "coalesce(p.status, 'current') = 'current'\nRETURN count(*) AS n": [5, 0],
            "i.effective_date = f.effective_date": 1,  # instrument fix
            "SET p.status = i.status": 5,  # propagation
            "p.effective_from = i.effective_date": 75,  # backfill
        })
        summary = remediate(drv, "neo4j", dry_run=False)
        assert summary["risk_before"] == 5
        assert summary["instrument_dates_fixed"] == 1
        assert summary["propagated"] == 5
        assert summary["effective_backfilled"] == 75
        assert summary["risk_after"] == 0

    def test_dry_run_writes_nothing_and_rolls_back(self):
        from scripts.remediate_kg_temporal import remediate

        drv = FakeDriver(counts={
            "coalesce(p.status, 'current') = 'current'\nRETURN count(*) AS n": 5,
            "i.effective_date = f.effective_date": 1,
            "SET p.status = i.status": 5,
            "p.effective_from = i.effective_date": 75,
        })
        summary = remediate(drv, "neo4j", dry_run=True)
        assert summary["dry_run"] is True
        # Every statement ran inside a transaction that was rolled back
        assert drv.txs, "dry-run must open a transaction"
        assert all(tx.rolled_back for tx in drv.txs)
        # No direct session-level writes (all writes went through the tx)
        assert not any("SET" in c or "RETURN count" not in c for c, _ in drv.session_calls)

    def test_repo_known_effective_date_for_fss_act(self):
        from scripts.remediate_kg_temporal import FIXED_INSTRUMENT_EFFECTIVE_DATES

        assert FIXED_INSTRUMENT_EFFECTIVE_DATES["FSS_ACT_2006"] == "2006-09-01"


# --------------------------------------------------------------------------- #
# P4 — provision-type backfill
# --------------------------------------------------------------------------- #


class TestProvisionTypeBackfill:
    @pytest.mark.parametrize(
        ("number", "expected"),
        [
            ("6", "Section"),
            ("31", "Section"),
            ("6(2)", "Subsection"),
            ("6(2)(a)", "Clause"),
            ("Schedule I", "Schedule"),
            ("", "Section"),
        ],
    )
    def test_mechanical_classification(self, number, expected):
        from scripts.backfill_kg_provision_types import classify_provision_type

        assert classify_provision_type(number) == expected

    def test_digits_only_corpus_maps_everything_to_section(self):
        from scripts.backfill_kg_provision_types import classify_provision_type

        kinds = {classify_provision_type(str(n)) for n in range(1, 1862)}
        assert kinds == {"Section"}

    def test_backfill_verification_reports_coverage(self):
        from scripts.backfill_kg_provision_types import backfill

        drv = FakeDriver(
            counts={"SET p.provision_type = r.provision_type, p:Section": 3},
            load_rows=[{"provision_id": f"X_SEC_{i}", "provision_number": str(i)} for i in (1, 2, 31)],
            verify_rows=[{"total": 3, "typed": 3, "sections": 3}],
            label_count=3,
        )
        summary = backfill(drv, "neo4j", dry_run=False)
        assert summary["updates_applied"] == 3
        assert summary["verify"]["coverage_pct"] == 100.0
        assert summary["verify"]["section_label_nodes"] == 3

    def test_dry_run_does_not_write(self):
        from scripts.backfill_kg_provision_types import backfill

        drv = FakeDriver(
            load_rows=[{"provision_id": "X_SEC_1", "provision_number": "1"}],
            verify_rows=[{"total": 1, "typed": 0, "sections": 0}],
            label_count=0,
        )
        summary = backfill(drv, "neo4j", dry_run=True)
        assert summary["dry_run"] is True
        assert summary["updates_applied"] == 0
        assert not any("SET p.provision_type" in c for c, _ in drv.session_calls)


# --------------------------------------------------------------------------- #
# P2 — concept linking
# --------------------------------------------------------------------------- #


class TestConceptLinking:
    def test_synonym_set_covers_all_live_concepts_or_explicitly_empty(self):
        from kg.concept_linking import CONCEPT_SYNONYMS

        # The 20 isolated concepts all have a (possibly empty) synonym set
        for cid in (
            "AnimalSlaughter", "AnimalWelfare", "BUSINESS_CIVIL", "BusinessCivil",
            "ConsentToOperate", "ConsumerProtection", "Contract", "Effluent",
            "Hygiene", "ImprovementNotice", "LandPremises", "Licence", "Meat",
            "Nuisance", "Premises", "Registration", "Sanitation", "SolidWaste",
            "TradeLicence", "Vehicles",
        ):
            assert cid in CONCEPT_SYNONYMS

    def test_find_grounding_hits_and_evidence(self):
        from kg.concept_linking import ConceptLinker

        hits = ConceptLinker.find_grounding(
            "No person shall engage in the slaughter of animals except at a slaughter house.",
            ("slaughter of animals", "slaughter house", "slaughter"),
        )
        assert hits
        assert hits[0]["confidence"] == 0.9  # canonical synonym (first hit) strongest
        assert "slaughter" in hits[0]["evidence"].lower()

    def test_find_grounding_empty_for_no_synonyms(self):
        from kg.concept_linking import ConceptLinker

        assert ConceptLinker.find_grounding("anything at all", ()) == []

    def test_plan_marks_domain_abstraction_premature(self):
        from kg.concept_linking import ConceptLinker

        class MiniDriver:
            def __init__(self):
                self.calls = []

            def execute_query(self, cypher, parameters_=None, database_=None):
                self.calls.append(cypher)
                from tests.test_kg_remediation import FakeResult, FakeRecord

                if "OPTIONAL MATCH (x)-[e]->(c) WHERE NOT (x:LegalConcept)" in cypher:
                    return FakeResult([FakeRecord({"concept_id": "BusinessCivil", "name": "Business Civil Law",
                                                   "domains": ["BUSINESS_CIVIL"], "inbound": 0})])
                if "collect(DISTINCT coalesce(c.chunk_text, ''))" in cypher:
                    return FakeResult()
                return FakeResult()

        plan = ConceptLinker(driver=MiniDriver(), database="neo4j").plan_links()
        assert plan["isolated_before"] == 1
        assert plan["linked"] == {}
        assert "BusinessCivil" in plan["premature"]
        assert plan["rows"] == []
        assert plan["edges_planned"] == 0

    def test_write_edges_batched_merge_shape(self):
        from kg.concept_linking import ConceptLinker

        class MiniDriver:
            def __init__(self):
                self.calls = []

            def execute_query(self, cypher, parameters_=None, database_=None):
                self.calls.append((cypher, parameters_ or {}))
                from tests.test_kg_remediation import FakeResult

                return FakeResult()

        drv = MiniDriver()
        linker = ConceptLinker(driver=drv, database="neo4j", batch_size=2)
        n = linker.write_edges(
            [
                {"provision_id": "P1", "concept_id": "Meat", "evidence": "e1", "confidence": 0.75},
                {"provision_id": "P2", "concept_id": "Meat", "evidence": "e2", "confidence": 0.75},
                {"provision_id": "P3", "concept_id": "Meat", "evidence": "e3", "confidence": 0.75},
            ]
        )
        assert n == 3
        writes = [c for c in drv.calls if "MERGE (p)-[rel:APPLIES_TO]->(c)" in c[0]]
        assert len(writes) == 2  # batched at 2
        assert all(0 < len(w["rows"]) <= 2 for _, w in writes)


# --------------------------------------------------------------------------- #
# P1 — extended enrichment rules + semantic_class
# --------------------------------------------------------------------------- #


class TestExtendedRules:
    def test_penalty_schedule_row_rupees(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "Section 496, subsection (1) of unwholesome wells, pools, etc. Failure to maintain land effectively One thousand One hundred rupees"
        )
        assert {t["rel_type"] for t in tags} == {"PRESCRIBES_PENALTY"}

    def test_penalty_schedule_row_slash(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "Section 498 Unlawful removal 500/- of earth, sand or other material from any land vested in the Corporation."
        )
        assert {t["rel_type"] for t in tags} == {"PRESCRIBES_PENALTY"}

    def test_glued_bns_penalty_text(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "Whoever,exceptinthecaseprovidedforbysub-section(2)ofsection122,voluntarilycausesgrievoushurt,shallbepunishedwithimprisonmentofeitherdescriptionforatermwhichmayextendtosevenyears,andshallalsobeliabletofine."
        )
        assert {t["rel_type"] for t in tags} == {"PRESCRIBES_PENALTY"}

    def test_glued_bns_offence_text(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "offencepunishableunder section64, section65,section66, section67,section68,"
        )
        assert "CREATES_OFFENCE" in {t["rel_type"] for t in tags}

    def test_prohibition_of_noun_form(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text("Section 89 Prohibition of advertisement without permission.")
        assert {t["rel_type"] for t in tags} == {"PROHIBITS"}

    def test_punished_with_matches(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text("Whoever commits this offence shall be punished with imprisonment for a term which may extend to seven years.")
        assert "PRESCRIBES_PENALTY" in {t["rel_type"] for t in tags}

    def test_penalty_rule_suppresses_fee_context(self):
        # Review-confirmed false positive (2026-08-11): a registration fee is
        # not a penalty.  "Rs. 1,000/-" sits inside a fee context and must not
        # fire the penalty-schedule rule.
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "s hereby pleased to levy a non-refundable processing fee of Rs. 1,000/- (Rupees one thousand only) for processing the documents for registratio"
        )
        assert not any(t["rel_type"] == "PRESCRIBES_PENALTY" for t in tags)

    def test_penalty_rule_suppresses_fee_context_after_amount(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text("An application fee of Rs. 500 shall accompany the form.")
        assert not any(t["rel_type"] == "PRESCRIBES_PENALTY" for t in tags)

    def test_penalty_rule_suppresses_financial_format_header(self):
        # Review-confirmed false positive (2026-08-11): a Schedule III
        # balance-sheet format row ("Rupees in ..... Particulars") is table
        # boilerplate, not a penalty.
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "349 PART II – STATEMENT OF PROFIT AND LOSS Name of the Non-Banking Financial Company. "
            "Statement of Profit and Loss for the period ended ........ (Rupees in ........) "
            "Particulars Note No. Figures for the current reporting period Revenue from operations"
        )
        assert not any(t["rel_type"] == "PRESCRIBES_PENALTY" for t in tags)

    def test_penalty_rule_keeps_per_day_fine(self):
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "the company shall be liable to a penalty of one hundred rupees for every day during which the default continues"
        )
        assert "PRESCRIBES_PENALTY" in {t["rel_type"] for t in tags}

    def test_penalty_rule_keeps_fine_row_with_deposit_verb(self):
        # "deposit of any matter" is a verb — it must NOT suppress the genuine
        # 500/- fine cell (deposit is deliberately absent from the fee list).
        from kg.enrichment import LegalSemanticEnricher

        tags = LegalSemanticEnricher.tag_text(
            "Section 498 Unlawful removal 500/- of earth, sand or other material or deposit of any matter or making of any encroachment from any land vested in the Corporation."
        )
        assert {t["rel_type"] for t in tags} == {"PRESCRIBES_PENALTY"}


class TestNotApplicableClassifier:
    @pytest.mark.parametrize(
        ("text", "expected_reason"),
        [
            ("(l) \"Food Analyst\" means an analyst appointed under section 45;", "definition"),
            ("(H) In section 104, for the words and figures", "cross_reference_fragment"),
            ("[Section 218 omitted, by section 23 of the Calcutta Municipal Corporation (Second Amendment) Act, 1984]", "amendment_machinery"),
            ("PART III PUBLISHED BY AUTHORITY THE GAZETTE OF INDIA EXTRAORDINARY", "gazette_machinery"),
            ("of the membership and other matters of or relating to a company", "cross_reference_fragment"),
            ("349 PART II – STATEMENT OF PROFIT AND LOSS ... (Rupees in ........) Particulars Note No. Figures for the current reporting period Revenue from operations", "financial_format_row"),
        ],
    )
    def test_reasons(self, text, expected_reason):
        from kg.enrichment import _not_applicable_reason

        assert _not_applicable_reason(text) == expected_reason

    def test_substantive_text_is_not_applicable_none(self):
        from kg.enrichment import _not_applicable_reason

        assert _not_applicable_reason("No person shall commence or carry on any food business except under a licence.") is None


class TestEnrichClassTagging:
    def _make_driver(self, provisions: list[dict]):
        class MiniDriver:
            def __init__(self):
                self.calls = []

            def execute_query(self, cypher, parameters_=None, database_=None):
                self.calls.append((cypher, parameters_ or {}))
                if "MATCH (p:LegalProvision)" in cypher and "UNWIND" not in cypher:
                    return FakeResult([FakeRecord(p) for p in provisions])
                return FakeResult()

        return MiniDriver()

    def test_enrich_writes_semantic_classes(self):
        from kg.enrichment import LegalSemanticEnricher

        drv = self._make_driver(
            [
                {"provision_id": "P1", "provision_number": "1", "title": "",
                 "provision_text": "No person shall commence or carry on any food business except under a licence.",
                 "legal_domain": "FOOD_SAFETY", "instrument_id": "I"},
                {"provision_id": "P2", "provision_number": "2", "title": "",
                 "provision_text": "(l) \"Food Analyst\" means an analyst appointed under section 45;",
                 "legal_domain": "FOOD_SAFETY", "instrument_id": "I"},
                {"provision_id": "P3", "provision_number": "3", "title": "",
                 "provision_text": "short",
                 "legal_domain": "FOOD_SAFETY", "instrument_id": "I"},
            ]
        )
        summary = LegalSemanticEnricher(driver=drv, database="neo4j").enrich(dry_run=False)
        assert summary["provisions_loaded"] == 3
        assert summary["skipped_short_text"] == 1
        assert summary["not_applicable"] == 1
        assert summary["unclassified"] == 0
        assert summary["classes_written"] == 3
        # Class write went through UNWIND with semantic_class values
        class_writes = [c for c in drv.calls if "SET p.semantic_class = r.semantic_class" in c[0]]
        assert class_writes
        classes = [r["semantic_class"] for r in class_writes[0][1]["rows"]]
        assert "tagged" in classes and "not_applicable:definition" in classes and "skipped_short_text" in classes

    def test_dry_run_counts_classes_without_writes(self):
        from kg.enrichment import LegalSemanticEnricher

        drv = self._make_driver(
            [
                {"provision_id": "P1", "provision_number": "1", "title": "",
                 "provision_text": "No person shall commence or carry on any food business except under a licence.",
                 "legal_domain": "FOOD_SAFETY", "instrument_id": "I"},
                {"provision_id": "P2", "provision_number": "2", "title": "",
                 "provision_text": "short",
                 "legal_domain": "FOOD_SAFETY", "instrument_id": "I"},
            ]
        )
        summary = LegalSemanticEnricher(driver=drv, database="neo4j").enrich(dry_run=True)
        assert summary["dry_run"] is True
        assert summary["edges_written"] == 0
        assert summary["classes_written"] == 0
        assert summary["class_breakdown"]["tagged"] == 1
        assert summary["class_breakdown"]["skipped_short_text"] == 1
