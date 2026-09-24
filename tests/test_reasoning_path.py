"""Tests for the shared reasoning-path module (arch review candidates 1+2).

Seam: ``app.rag.generation.reasoning_path`` owns the revision policy,
defect-note format, correction-context assembly, and structured
user-content assembly shared by the graph node, the audit router, and
the Experiment D harness. Deterministic, no LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.generation.reasoning_path import (
    audit_failed,
    defect_notes,
    reasoning_user_content,
    revision_context,
    should_revise,
)


def _fail_audit() -> dict:
    return {
        "status": "FAIL",
        "defects": [
            {
                "defect_type": "missed_exception",
                "explanation": "proviso ignored",
                "required_correction": "address the proviso",
            }
        ],
    }


class TestAuditFailed:
    def test_fail_dict(self):
        assert audit_failed(_fail_audit()) is True

    def test_pass_dict(self):
        assert audit_failed({"status": "PASS", "defects": []}) is False

    def test_none_and_empty(self):
        assert audit_failed(None) is False
        assert audit_failed({}) is False


class TestShouldRevise:
    def test_fail_with_budget(self):
        assert should_revise(_fail_audit(), 0, 1) is True

    def test_fail_budget_exhausted(self):
        assert should_revise(_fail_audit(), 1, 1) is False

    def test_pass_never_revises(self):
        assert should_revise({"status": "PASS"}, 0, 1) is False

    def test_missing_audit_never_revises(self):
        assert should_revise(None, 0, 1) is False


class TestDefectNotes:
    def test_canonical_format(self):
        notes = defect_notes(_fail_audit()["defects"])
        assert notes == "- missed_exception: proviso ignored => address the proviso"

    def test_empty_defects(self):
        assert defect_notes([]) == ""


class TestRevisionContext:
    def test_appends_correction_block(self):
        out = revision_context("CTX", _fail_audit()["defects"])
        assert out.startswith("CTX\n\nCorrection required:\n- missed_exception:")

    def test_empty_defects_unchanged(self):
        assert revision_context("CTX", []) == "CTX"


class TestReasoningUserContent:
    def test_with_context_carries_all_parts(self):
        out = reasoning_user_content("Q?", '{"issue": "x"}', "CTX")
        assert "Q?" in out
        assert '{"issue": "x"}' in out
        assert "CTX" in out
        assert "[n]" in out

    def test_without_context_omits_evidence_block(self):
        out = reasoning_user_content("Q?", '{"issue": "x"}')
        assert "Q?" in out and '{"issue": "x"}' in out
        assert "Evidence context" not in out


class TestAuditModelInput:
    def test_accepts_audit_result_model(self):
        from app.rag.agent.nodes.auditor import AuditResult

        assert should_revise(AuditResult(status="FAIL"), 0, 1) is True
        assert audit_failed(AuditResult(status="PASS")) is False


class TestDefaultCapMatchesProductionContract:
    def test_default_cap_is_one(self):
        # DEFAULT_MAX_REVISIONS = 1: production initial_state + Experiment D
        # share one budget; a missing override must not grant a second pass.
        from app.rag.generation.reasoning_path import DEFAULT_MAX_REVISIONS

        assert DEFAULT_MAX_REVISIONS == 1
        assert should_revise(_fail_audit(), 0) is True
        assert should_revise(_fail_audit(), 1) is False

    def test_invalid_cap_falls_back_to_default(self):
        assert should_revise(_fail_audit(), 0, "not-a-number") is True
        assert should_revise(_fail_audit(), 1, "not-a-number") is False
