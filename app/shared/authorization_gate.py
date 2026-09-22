"""Shared authorization gate for petition generation.

Both the sample (case-file) and non-sample (adjudication) tracks follow the
same two-stage flow: first data entry has no authorization date — it is
issued by the Designated Officer when the permission file is submitted, and
the petition records it.  Petition files therefore cannot be generated until
the date exists on the record.
"""

from __future__ import annotations

from flask import jsonify

#: Shared denial text (also used by the generation-access seam so the two
#: never drift apart).
AUTHORIZATION_REQUIRED_ERROR = (
    "Petition cannot be generated until authorization is issued. "
    "Submit the permission file first, then record the authorization date on the case."
)


def authorization_gate_response(authorization_date):
    """Return a 403 JSON response when no authorization has been issued yet.

    Args:
        authorization_date: The record's authorization date (datetime, date,
            ISO string, or None).  Blank strings and unparseable values count
            as missing — call sites pass raw form strings, so normalization
            lives here rather than in each caller.

    Returns:
        ``(jsonify(...), 403)`` when blocked, else ``None``.
    """
    normalized = _normalize(authorization_date)
    if normalized:
        return None
    return (jsonify({"error": AUTHORIZATION_REQUIRED_ERROR}), 403)


def _normalize(value):
    """Coerce *value* to a datetime; return None when it means 'not issued'."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            from app.utils.filters import parse_date
        except Exception:
            return None  # fail closed: unverifiable string counts as missing
        return parse_date(value)
    return value
