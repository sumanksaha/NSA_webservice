"""Future-date guard for the sample case-file form.

Event dates record things that already happened (sample draw, receipts,
reports, authorization) and must not lie in the future. Expiry is
excluded: it is normally a future date (already constrained to be after
mfg_date). These are pure-function tests on ``validate_case_file_form``
(the seam both create and edit paths validate through).
"""

from __future__ import annotations

from app.case_file_generator.routes import validate_case_file_form

PAST = "2026-01-10"
FUTURE = "2099-01-01"

BASE_FORM = {
    "case_number": "2026/FSS/910",
    "food_safety_officer_name": "FSO Dates",
    "authorization_date": PAST,
    "inspection_date": PAST,
    "inspection_time": "10:30",
    "manufacturer_fssai": "10012043001234",
    "manufacturer_name": "Mfr",
    "manufacturer_fbo_name": "Mfr FBO",
    "manufacturer_address": "Mfr Addr",
    "retailer_fssai": "10012043005678",
    "retailer_name": "Ret",
    "retailer_fbo_name": "Ret FBO",
    "retailer_address": "Ret Addr",
    "product_name": "Mustard Oil",
    "batch_no": "B1",
    "sample_quantity": "1 L",
    "packet_count": "4",
    "mfg_date": "2025-12-01",
    "expiry_date": "2026-12-01",
    "sample_code": "SC-1",
    "lab_registration_no": "LAB-1",
    "do_receipt_date": PAST,
    "analyst_report_no": "AR-1",
    "analyst_report_date": PAST,
    "directive_letter_no": "DL-1",
    "directive_letter_date": PAST,
    "retailer_report_receive_date": PAST,
    "manufacturer_report_receive_date": PAST,
}


def test_base_form_is_valid():
    assert validate_case_file_form(dict(BASE_FORM)) == {}


def test_future_inspection_date_rejected():
    errors = validate_case_file_form(dict(BASE_FORM, inspection_date=FUTURE))
    assert "inspection_date" in errors


def test_future_mfg_date_rejected():
    errors = validate_case_file_form(dict(BASE_FORM, mfg_date=FUTURE))
    assert "mfg_date" in errors


def test_future_report_and_authorization_dates_rejected():
    form = dict(
        BASE_FORM,
        authorization_date=FUTURE,
        do_receipt_date=FUTURE,
        analyst_report_date=FUTURE,
        directive_letter_date=FUTURE,
        retailer_report_receive_date=FUTURE,
        manufacturer_report_receive_date=FUTURE,
    )
    errors = validate_case_file_form(form)
    for field in (
        "authorization_date",
        "do_receipt_date",
        "analyst_report_date",
        "directive_letter_date",
        "retailer_report_receive_date",
        "manufacturer_report_receive_date",
    ):
        assert field in errors, f"expected future-date error for {field}"


def test_future_expiry_date_allowed():
    # Expiry is normally in the future; only mfg < expiry ordering applies.
    errors = validate_case_file_form(dict(BASE_FORM, expiry_date=FUTURE))
    assert "expiry_date" not in errors


def test_rcm_ignores_stale_hidden_dates():
    # Switching to RCM hides manufacturer/batch/mfg/expiry inputs (the edit
    # page deliberately does not clear them); the server blanks them on
    # save, so stale values — even future or misordered ones — must not
    # fail validation.
    form = dict(
        BASE_FORM,
        retailer_cum_manufacturer="on",
        mfg_date=FUTURE,
        expiry_date="2025-01-01",
        manufacturer_report_receive_date=FUTURE,
        batch_no="STALE",
        manufacturer_fssai="",
        manufacturer_name="",
    )
    errors = validate_case_file_form(form)
    assert errors == {}, errors
