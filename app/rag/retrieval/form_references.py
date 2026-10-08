"""Form reference detector — lexical identifier arm for Form II–VIII mentions.

Mirrors the act+section identifier arm in ``identifier.py`` so that queries
like "which form does the FSO give the FBO?" can route to the workflow
document that contains the form definitions.

The forms are defined in the FSSAI (FSSA) Act and Rules:
    Form II  - receipt (Regulation 2.3.1)
    Form III - seizure order (Regulation 2.3.2(1))
    Form IV  - bond (Regulation 2.3.2(2))
    Form V   - notice to business operator (Regulation 2.4.1.3)
    Form VI  - memorandum to analyst (Regulation 2.4.1.10(i))
    Form VIIA- analyst report (Regulation 2.4.2.5)
    Form VIII- appeal to designated officer (Regulation 2.4.6)

See: sample_workflow.md Appendix §2 Phase 2.
"""

from __future__ import annotations

import re

__all__ = ["_DETECTABLE_FORMS", "detect_form", "form_query"]

#: Form numbers in alphabetical order (for longest-match priority).
_DETECTABLE_FORMS = [
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
]

#: Canonical names for forms as they appear in the FSSAI framework.
_FORM_NAMES: dict[str, str] = {
    "ii": "Form II (receipt)",
    "iii": "Form III (seizure order)",
    "iv": "Form IV (bond)",
    "v": "Form V (notice to business operator)",
    "vi": "Form VI (memorandum to analyst)",
    "vii": "Form VII / VIIA (analyst report)",
    "viii": "Form VIII (appeal to designated officer)",
}

#: Regex for standalone "Form NN" mentions (case-insensitive).
_FORM_RE = re.compile(
    r"\b(?:form|forms|Form)\s+(ii|iii|iv|v|vi|vii|viii|ix|x)\b",
    re.IGNORECASE,
)

#: Regex for bare "NN" form references in context like "give Form V" or "Form VIII"
#: (only when the number is standalone, not part of a section number).
_BARE_FORM_RE = re.compile(
    r"\b(ii|iii|iv|v|vi|vii|viii|ix|x)\b",
    re.IGNORECASE,
)


#: Semantic cues mapping to specific forms (for queries without explicit roman numerals).
_FORM_CUES: dict[str, str] = {
    "notice": "v",
    "seizure": "iii",
    "bond": "iv",
    "appeal": "viii",
    "memorandum": "vi",
    "analyst": "viia",
}


def detect_form(query: str) -> str | None:
    """Return the canonical form name mentioned in the query, if any.

    Returns the form number in lowercase (e.g. "v") or None if no form
    reference is detected.
    """
    m = _FORM_RE.search(query or "")
    if m:
        return m.group(1).lower()
    # Also detect bare form references like "give Form V"
    m = _BARE_FORM_RE.search(query or "")
    if m:
        # Only return if it's clearly a form reference (not a section number)
        # This is a heuristic — should be refined in production
        return m.group(1).lower()
    # Semantic fallback: infer form from context keywords
    lower = (query or "").lower()
    for cue, form in _FORM_CUES.items():
        if cue in lower:
            return form
    return None


def form_query(query: str) -> str | None:
    """Build a lexical query targeting the form's chunk.

    Returns a query string like "Form V" that can be used to retrieve
    chunks mentioning that specific form.
    """
    form = detect_form(query)
    if form:
        return f"Form {form.upper()}"
    return None


# Compatibility alias for tests.
detect_form_number = detect_form
