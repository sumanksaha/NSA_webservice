"""RCM case-field policy — one home for what Retailer-cum-Manufacturer changes.

A Retailer-cum-Manufacturer (loose food prepared and sold by the retailer)
has no separate manufacturer and carries no batch / mfg / expiry numbers.
Every consumer of that fact — the form validator, the three record
construction sites (create, update, import), and the JS visibility toggles
in both templates — reads this seam, so a missing carve-out fails in one
place instead of on a live form.

Blanking convention: :func:`stripped_for_save` blanks exempt fields to
``""`` in a form-data copy. Downstream ``parse_date("")`` yields ``None``
for the date fields, so string and date columns both land correctly
without per-site type juggling.

The JS toggles cannot import Python, so they mirror :data:`TOGGLE_FIELD_IDS`
literally; ``tests/test_rcm_policy.py::TestTemplateAgreement`` pins that
agreement on both rendered pages (a missing id fails there, not in a browser).
"""

from __future__ import annotations

#: Form fields that do not apply to RCM cases (skipped by required-field
#: and date validation, blanked on save, hidden by the JS toggle).
EXEMPT_FIELDS: frozenset[str] = frozenset({
    "manufacturer_fssai",
    "manufacturer_name",
    "manufacturer_fbo_name",
    "manufacturer_address",
    "manufacturer_report_receive_date",
    "batch_no",
    "mfg_date",
    "expiry_date",
})

#: The date-typed subset (skipped by date-format, future-date, and
#: ordering checks when RCM).
EXEMPT_DATE_FIELDS: frozenset[str] = frozenset({
    "mfg_date",
    "expiry_date",
    "manufacturer_report_receive_date",
})

#: Input ids owned by the JS visibility toggle (must equal EXEMPT_FIELDS;
#: pinned against both rendered templates by test).
TOGGLE_FIELD_IDS: tuple[str, ...] = tuple(sorted(EXEMPT_FIELDS))

_TRUTHY = ("on", "true", "1", "yes")


def is_rcm(form_data: dict) -> bool:
    """Is this form data a Retailer-cum-Manufacturer case?"""
    return str(form_data.get("retailer_cum_manufacturer", "")).strip().lower() in _TRUTHY


def stripped_for_save(form_data: dict) -> dict:
    """Return a copy of *form_data* with RCM-exempt fields blanked to ``""``.

    Non-RCM input returns an equal (but new) dict. The input is never mutated.
    """
    stripped = dict(form_data)
    if is_rcm(stripped):
        for field in EXEMPT_FIELDS:
            stripped[field] = ""
    return stripped
