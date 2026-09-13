"""Read-only case lookups for document modules (split from DocumentCaseManager).

Pure helpers over a SQLAlchemy model + ``case_type`` (``"case_file"`` or
``"adjudication"``): fetching, summaries, FK kwargs, officer scoping, and
the Sheets-sync column sets. No Flask request handling here.
"""

from __future__ import annotations

from typing import Any

from app.extensions import db


def get_case(model: type, case_id: int) -> Any | None:
    """Fetch one record by PK."""
    return db.session.get(model, case_id)


def get_case_by_number(model: type, case_number: str) -> Any | None:
    """Fetch one record by its human case number."""
    return model.query.filter_by(case_number=case_number).first()


def list_cases(model: type, case_type: str) -> list[dict]:
    """All cases, newest first, as summary dicts."""
    cases = model.query.order_by(model.created_at.desc()).all()
    return [case_summary(case_type, c) for c in cases]


def case_kwarg(case_type: str, case_id: int) -> dict:
    """Return the case-type-specific kwarg for CrossReference/Toc APIs."""
    if case_type == "case_file":
        return {"case_id": case_id, "adjudication_id": None}
    return {"adjudication_id": case_id, "case_id": None}


def officer_column(model: type, case_type: str):
    """The model attribute holding the responsible officer's name."""
    if case_type == "case_file":
        return model.food_safety_officer_name
    return model.food_safety_officer


def visible_to_current_user(model: type, case_type: str, case) -> bool:
    """Phase 18 record-level scope: officers see only their own cases."""
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is None:
        return True
    return getattr(case, officer_column(model, case_type).key, None) == scope


def case_summary(case_type: str, case) -> dict:
    """Compact dict for case lists."""
    if case_type == "case_file":
        return {
            "id": case.id,
            "case_number": case.case_number,
            "product_name": case.product_name,
            "manufacturer_name": case.manufacturer_name,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        }
    return {
        "id": case.id,
        "case_number": case.case_number,
        "fbo_name": case.fbo_name,
        "food_safety_officer": case.food_safety_officer,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


def get_case_number(case) -> str:
    """Human case number (both models share the attribute)."""
    return case.case_number


def get_fbo_name(case_type: str, case) -> str:
    """Display FBO name for reports."""
    if case_type == "case_file":
        return case.manufacturer_name
    return case.fbo_name


def get_fso(case_type: str, case):
    """Responsible officer name for reports (None for case files)."""
    if case_type == "case_file":
        return None
    return case.food_safety_officer


def sheets_columns(case_type: str) -> set[str]:
    """Column names eligible for Sheets sync, per case type."""
    if case_type == "case_file":
        return {
            "case_number",
            "food_safety_officer_name",
            "authorization_date",
            "inspection_date",
            "inspection_time",
            "sample_id",
            "manufacturer_fssai",
            "manufacturer_name",
            "manufacturer_fbo_name",
            "manufacturer_address",
            "retailer_fssai",
            "retailer_name",
            "retailer_fbo_name",
            "retailer_address",
            "product_name",
            "batch_no",
            "sample_quantity",
            "packet_count",
            "mfg_date",
            "expiry_date",
            "other_food_articles",
            "total_cost",
            "cost_in_words",
            "sample_code",
            "sample_submission_date",
            "Lab_Registration_No",
            "do_receipt_date",
            "is_misbranded",
            "is_substandard",
            "analyst_report_no",
            "analyst_report_date",
            "directive_letter_no",
            "directive_letter_date",
            "retailer_report_receive_date",
            "manufacturer_report_receive_date",
            "applicable_regulation",
            "applicable_clause",
            "sample_name",
            "applicable_sections",
        }
    return {
        "case_number",
        "food_safety_officer",
        "non_license",
        "pre_authorization",
        "complaint_lodged",
        "ce_license_no",
        "ce_trade_name",
        "ce_proprietor",
        "ce_address",
        "ce_status",
        "fbo_owner",
        "fbo_name",
        "fbo_address",
        "fssai_license",
        "concerned_food",
        "problem",
        "First_inspection_date",
        "compliance_deadline",
        "Complaint_date",
        "inspection_date",
        "authorization_date",
        "clean_premise",
        "refrigerator_clean",
        "proper_attire",
        "proper_covered_utensil",
        "date_tag",
        "veg_nonveg_separation",
        "food_segregation",
        "license_display",
        "artificial_colour",
        "Expired_item",
        "Pest_report",
        "Water_report",
        "section_55",
        "section_56",
        "section_58",
        "section_63",
        "section_64",
    }
