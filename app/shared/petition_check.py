"""Petition completeness checks for document downloads.

Before a petition PDF is served, two things are verified so that no field
goes missing in the produced document:

1. Every required context value is present and non-blank
   (:func:`missing_required_fields`).
2. The rendered HTML contains no unresolved Jinja placeholders
   (:func:`has_unresolved_jinja`).
"""

from __future__ import annotations

import re

# An unresolved placeholder looks like ``{{ name }}`` / ``{% tag`` /
# ``{# comment`` — an opener followed by identifier text. A bare ``{{``
# inside user-supplied prose (no identifier after it) is left alone.
_UNRESOLVED_JINJA_RE = re.compile(r"{{\s*[a-zA-Z_]|{%\s*\w|{#\s*\w")


def _is_blank(value) -> bool:
    """Return True when *value* would render as an empty field."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return not value.strip()
    return not value


def missing_required_fields(required_labels: dict[str, str], context: dict) -> dict[str, str]:
    """Return ``{field: label}`` for required fields that are blank.

    Args:
        required_labels: Mapping of context key → human-readable label.
        context: Template render context.
    """
    missing: dict[str, str] = {}
    for field, label in required_labels.items():
        if _is_blank(context.get(field, "")):
            missing[field] = label
    return missing


def has_unresolved_jinja(html: str) -> bool:
    """Return True when rendered *html* still contains Jinja syntax."""
    return bool(_UNRESOLVED_JINJA_RE.search(html))
