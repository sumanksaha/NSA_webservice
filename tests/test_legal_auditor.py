"""Tests for Phase 1 (Experiment D) — deterministic legal auditor seam.

Seam S2: ``app.rag.agent.nodes.auditor.audit_argument`` — critiques a
``StructuredLegalArgument`` against the evidence texts and emits a
structured ``AuditResult`` (roadmap §32.2).  Fully deterministic, no LLM:
the harness (Condition C) decides the 1-cycle revision from the defects.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.agent.nodes.auditor import AuditResult, audit_argument
from app.rag.generation.structured_reasoner import StructuredLegalArgument

EVIDENCE = {
    "FSS_ACT::31": "Section 31 requires food businesses to obtain a licence. Provided that petty retailers are exempt.",
    "FSS_ACT::3-def": '"Food" means any article used as food for human consumption.',
}


def _argument(**overrides: object) -> StructuredLegalArgument:
    base: dict[str, object] = {
        "issue": "Is a licence needed?",
        "applicable_provisions": ["FSS_ACT::31"],
        "definitions_applied": {"food": "any article used as food"},
        "legal_rules": ["Licence required under Section 31."],
        "exceptions_considered": [{"rule": "Section 31", "exception": "petty-retailer exemption", "applies": False}],
        "condition_evaluations": [
            {
                "condition_id": "C1",
                "condition_text": "carries on food business",
                "fact_reference": "retail shop stated",
                "status": "satisfied",
                "explanation": "retail sale qualifies",
            }
        ],
        "cross_references_followed": [],
        "conflicts_or_hierarchy": [],
        "derived_conclusion": "A licence is required under Section 31.",
        "supporting_citations": ["FSS_ACT::31"],
        "uncertainties": [],
    }
    base.update(overrides)
    return StructuredLegalArgument.model_validate(base)


class TestAuditorPass:
    def test_sound_argument_passes(self):
        result = audit_argument(_argument(), EVIDENCE)
        assert isinstance(result, AuditResult)
        assert result.status == "PASS"
        assert result.defects == []


class TestAuditorDefects:
    def test_missed_exception_is_critical(self):
        arg = _argument(exceptions_considered=[])
        result = audit_argument(arg, EVIDENCE)
        assert result.status == "FAIL"
        assert [d.defect_type for d in result.defects] == ["missed_exception"]
        assert result.defects[0].severity == "critical"

    def test_no_exception_no_defect_without_markers(self):
        evidence = {"FSS_ACT::31": "Section 31 requires a licence. No provisos."}
        result = audit_argument(_argument(exceptions_considered=[]), evidence)
        assert result.status == "PASS"

    def test_invalid_citation(self):
        arg = _argument(supporting_citations=["FSS_ACT::99"])
        result = audit_argument(arg, EVIDENCE)
        assert result.status == "FAIL"
        assert any(d.defect_type == "invalid_citation" for d in result.defects)

    def test_satisfied_without_facts(self):
        arg = _argument(
            condition_evaluations=[
                {
                    "condition_id": "C1",
                    "condition_text": "carries on food business",
                    "fact_reference": "",
                    "status": "satisfied",
                    "explanation": "assumed",
                }
            ]
        )
        result = audit_argument(arg, EVIDENCE)
        assert any(d.defect_type == "unsupported_application" for d in result.defects)

    def test_unhandled_definition_term(self):
        arg = _argument(definitions_applied={})
        result = audit_argument(arg, EVIDENCE)
        assert any(d.defect_type == "definition_mismatch" for d in result.defects)

    def test_empty_provisions_is_incomplete_scope(self):
        arg = _argument(applicable_provisions=[], supporting_citations=[])
        result = audit_argument(arg, {})
        assert any(d.defect_type == "incomplete_scope" for d in result.defects)


class TestMarkerSyncWithTaxonomy:
    """Auditor marker regexes must stay a subset of the §22 taxonomy lists.

    The two modules intentionally don't import each other (app/ must never
    import evaluation/), so this test fails loudly on sync drift instead.
    """

    def test_auditor_markers_covered_by_taxonomy(self):
        from app.rag.agent.nodes.auditor import _DEFINITION_RE, _EXCEPTION_RE
        from evaluation.answer_error_taxonomy import (
            _DEFINITION_PATTERNS,
            _EXCEPTION_PATTERNS,
        )

        for alt in _EXCEPTION_RE.pattern.split("|"):
            assert alt in _EXCEPTION_PATTERNS, alt
        for alt in _DEFINITION_RE.pattern.split("|"):
            assert alt in _DEFINITION_PATTERNS, alt
