"""Tests for CaseQueryService — extracted from DocumentCaseManager (D5 deepening).

Covers:
- case_summary field selection for case_file vs adjudication
- CaseQueryService used standalone (without DocumentCaseManager constructor)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.shared.case_query_service import CaseQueryService


class TestCaseSummaryCaseFile:
    """case_summary for case_file returns case-file fields."""

    def test_summary_has_case_file_fields(self):
        svc = CaseQueryService(model=MagicMock(), case_type="case_file")
        case = MagicMock()
        case.id = 1
        case.case_number = "CF-001"
        case.product_name = "Biscuits"
        case.manufacturer_name = "ABC Foods"
        case.created_at = None

        s = svc.case_summary(case)
        assert s["id"] == 1
        assert s["case_number"] == "CF-001"
        assert s["product_name"] == "Biscuits"
        assert s["manufacturer_name"] == "ABC Foods"
        assert "fbo_name" not in s  # adjudication-only field

    def test_summary_case_file_excludes_adjudication_fields(self):
        svc = CaseQueryService(model=MagicMock(), case_type="case_file")
        case = MagicMock()
        case.id = 2
        case.case_number = "CF-002"
        case.product_name = "Chips"
        case.manufacturer_name = "XYZ"
        case.created_at = None

        s = svc.case_summary(case)
        assert "fbo_name" not in s
        assert "food_safety_officer" not in s


class TestCaseSummaryAdjudication:
    """case_summary for adjudication returns adjudication fields."""

    def test_summary_has_adjudication_fields(self):
        svc = CaseQueryService(model=MagicMock(), case_type="adjudication")
        case = MagicMock()
        case.id = 3
        case.case_number = "ADJ-001"
        case.fbo_name = "Test FBO"
        case.food_safety_officer = "Officer A"
        case.created_at = None

        s = svc.case_summary(case)
        assert s["id"] == 3
        assert s["case_number"] == "ADJ-001"
        assert s["fbo_name"] == "Test FBO"
        assert s["food_safety_officer"] == "Officer A"
        assert "product_name" not in s  # case_file-only field

    def test_summary_adjudication_excludes_case_file_fields(self):
        svc = CaseQueryService(model=MagicMock(), case_type="adjudication")
        case = MagicMock()
        case.id = 4
        case.case_number = "ADJ-002"
        case.fbo_name = "FBO B"
        case.food_safety_officer = "Officer B"
        case.created_at = None

        s = svc.case_summary(case)
        assert "product_name" not in s
        assert "manufacturer_name" not in s


class TestCaseQueryServiceConstruction:
    """CaseQueryService can be constructed standalone."""

    def test_standalone_no_callbacks_needed(self):
        """The key deepening win: CaseQueryService needs only model + case_type,
        not the 5 callbacks that DocumentCaseManager requires."""
        from app.models import CaseFile, Adjudication

        svc_cf = CaseQueryService(CaseFile, case_type="case_file")
        assert svc_cf.model is CaseFile
        assert svc_cf.case_type == "case_file"

        svc_adj = CaseQueryService(Adjudication, case_type="adjudication")
        assert svc_adj.model is Adjudication
        assert svc_adj.case_type == "adjudication"
