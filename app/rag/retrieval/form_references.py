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

__all__ = ["FORM_NAMES", "_DETECTABLE_FORMS", "detect_form", "form_query"]

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
FORM_NAMES: dict[str, str] = {
    "ii": "Form II (receipt)",
    "iii": "Form III (seizure order)",
    "iv": "Form IV (bond)",
    "v": "Form V (notice to business operator)",
    "vi": "Form VI (memorandum to analyst)",
    "vii": "Form VII / VIIA (analyst report)",
    "viii": "Form VIII (appeal to designated officer)",
}

#: Pre-rename private alias. ``FORM_NAMES`` is the public name; this alias
#: keeps older callers (and test modules importing the private name) working.
_FORM_NAMES = FORM_NAMES


#: Regex for standalone "Form NN" mentions (case-insensitive).
#:
#: This is the only form matcher. The old ``_BARE_FORM_RE`` fallback
#: (``\b(ii|iii|iv|v|...)\b``, case-insensitive) fired on any standalone
#: numeral, so "What is Section X of the Act?" and "Explain clause ix" both
#: resolved to a form. It bought nothing: every case it was meant to catch --
#: "give Form V", "Forms V and VIII" -- already contains the word "Form" and
#: is matched above.
_FORM_RE = re.compile(
    r"\b(?:form|forms|Form)\s+(ii|iii|iv|v|vi|vii|viii|ix|x)\b",
    re.IGNORECASE,
)


def detect_form(query: str) -> str | None:
    """Return the canonical form name mentioned in the query, if any.

    Returns the form number in lowercase (e.g. "v") or None if no *explicit*
    form reference is detected.

    Only an explicit "Form NN" counts. The old keyword arm guessed a form from
    six common English words -- notice, seizure, bond, appeal, memorandum,
    analyst -- which carried no information about which form was meant, so
    every guess was wrong and some were common ("When must the FSO serve
    notice on the business operator?" -> Form V). Guessing is worse than
    abstaining here: the form arm then retrieves a confidently wrong document.
    """
    m = _FORM_RE.search(query or "")
    if m:
        return m.group(1).lower()
    return None


def form_query(query: str) -> str | None:
    """Build a lexical query targeting the form's chunk.

    Prefers the canonical description from :data:`FORM_NAMES`
    ("Form VIII (appeal to designated officer)") because that is what the
    corpus text actually contains -- the bare label "Form VIII" matches every
    form in the collection. Forms that are detectable but unnamed (``ix``,
    ``x``) fall back to the bare label so the arm still issues a query.
    """
    form = detect_form(query)
    if not form:
        return None
    return FORM_NAMES.get(form) or f"Form {form.upper()}"


# Compatibility alias for tests.
detect_form_number = detect_form
