"""Tests for unified actions — violations + FBO issues, deduped + recommendations."""

from app.shared.actions_summary import _infer_field_from_text, build_unified_actions


class TestInferField:
    def test_clean_premise(self):
        assert _infer_field_from_text("Unclean Premises") == "clean_premise"

    def test_pest_report(self):
        assert _infer_field_from_text("Pest Control Report Missing") == "Pest_report"

    def test_water_report(self):
        assert _infer_field_from_text("Water Test Report Missing") == "Water_report"


class TestBuildUnifiedActions:
    def test_empty_input(self):
        assert build_unified_actions() == []

    def test_violations_preserved_and_deduped(self):
        violations = [{"title": "Unclean Premises", "field": "clean_premise", "observation": "dirty floor"}]
        result = build_unified_actions(violations)
        assert len(result) == 1
        assert result[0]["action"].startswith("Maintain the entire food premises")
        assert result[0]["source"] == "checklist"

    def test_fbo_issue_merged_and_recommended(self):
        violations = [{"title": "Pest Control Report Missing", "field": "Pest_report", "observation": "no record"}]
        issues = [{"title": "Unclean Premises", "source_type": "inspection", "state": "open"}]
        result = build_unified_actions(violations, issues)
        # Deduped: same field for "unclean premise" from checklist and FBO issue
        fields = [r["field"] for r in result]
        # Should have clean_premise and Pest_report (no duplicate for same field)
        # Since the violations list has Pest_report and the FBO issue has clean_premise (via title mapping),
        # we expect two distinct fields.
        assert "Pest_report" in fields
        assert "clean_premise" in fields
        # Every result should have a non-empty action
        for r in result:
            assert r.get("action")
