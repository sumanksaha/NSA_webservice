"""Tests for entity-enriched planner subqueries (research rec B).

Seam: ``_question_for_requirement`` threads the already-extracted
entities (instrument/section), jurisdiction and temporal scope into the
retrieval subquery text. Same signature, deterministic, no LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.planning.query_planner import (
    EvidenceRequirement,
    QueryPlanner,
    Requirement,
    _question_for_requirement,
)


def _req(**overrides: object) -> Requirement:
    base: dict[str, object] = {
        "requirement_id": "r1",
        "evidence_type": EvidenceRequirement.PROVISION,
        "subject": "food business licence",
    }
    base.update(overrides)
    return Requirement(**base)  # type: ignore[arg-type]


class TestEntityEnrichedSubqueries:
    def test_section_and_instrument_qualifiers(self):
        req = _req(entities=["Food Safety and Standards Act, 2006", "31"])
        q = _question_for_requirement(req, "Is a licence needed under Section 31?")
        assert "Section 31" in q
        assert "Food Safety and Standards Act, 2006" in q

    def test_jurisdiction_and_temporal_stay_out(self):
        # Research B scope: section + Act only — jurisdiction/temporal
        # qualifiers were unrequested creep and are reverted.
        req = _req(jurisdiction="West Bengal", temporal_scope="before 2020")
        q = _question_for_requirement(req, "Is a licence needed in West Bengal?")
        assert q == "Which provision governs food business licence?"

    def test_non_act_entity_never_becomes_instrument(self):
        # The instrument slot takes only shared-detector Act names.
        req = _req(subject="penalty", entities=["late filing"])
        q = _question_for_requirement(req, "What penalty applies to late filing?")
        assert q == "Which provision governs penalty?"

    def test_no_entities_keeps_base_template(self):
        q = _question_for_requirement(_req(), "Is a licence needed?")
        assert q == "Which provision governs food business licence?"

    def test_negation_clause_preserved(self):
        req = _req(negation=True, entities=["31"])
        q = _question_for_requirement(req, "What applies?")
        assert "Section 31" in q
        assert "negation" in q

    def test_planner_end_to_end_carries_section(self):
        plan = QueryPlanner().plan("What penalty applies under Section 51 of the FSS Act?")
        questions = " ".join(t.question for t in plan.tasks)
        assert "51" in questions
