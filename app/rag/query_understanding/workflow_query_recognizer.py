"""Workflow query recognizer — deterministic pattern matcher for seizure/sampling questions.

Recognises queries that belong to the FSSAI seizure & sampling workflow (Forms II–VIII,
Sections 2.3 and 2.4) so the RAG pipeline can treat them as *procedure* intent rather than
generic provision lookup.  Deterministic, rule-based: same query always returns the same
intent classification.  No LLM calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "WORKFLOW_INTENTS",
    "WorkflowRecognition",
    "extract_workflow_fields",
    "recognize_workflow_query",
]

# ---------------------------------------------------------------------------
# Intent vocabulary (must be a subset of the legal intent taxonomy)
# ---------------------------------------------------------------------------

WORKFLOW_INTENTS: frozenset[str] = frozenset({
    "seizure_form_lookup",  # "which form does the FSO give during seizure?"
    "seizure_process",  # "what happens during seizure?"
    "sampling_form_lookup",  # "which form does the analyst report go in?"
    "sampling_process",  # "what happens after the FSO takes a sample?"
    "document_order",  # "order of documents during sampling"
    "appeal_procedure",  # "how can FBO appeal after non-satisfactory result?"
    "penalty_procedure",  # "what happens if lab result is not OK?"
    "form_reference",  # mentions a specific form number
})

# ---------------------------------------------------------------------------
# Regex patterns — ordered by priority.  First match wins.
# ---------------------------------------------------------------------------

# Pattern: "which form", "what form", "form ...?"
_FORM_LOOKUP_RE = re.compile(
    r"\b(which|what|show|list|name|identify)\s+(?:is\s+)?(the\s+)?(form|forms?)\b.*",
    re.IGNORECASE,
)

# Pattern: form number explicit mention (e.g. "Form V")
_FORM_NUMBER_RE = re.compile(r"\bform\s+(ii|iii|iv|v|vi|vii|viii)\b", re.IGNORECASE)

# Pattern: seizure / sample / seize keyword
_SEARCH_ACTION_RE = re.compile(
    r"\b(seize|seizure|sample|sampling|seized|seizes|sample-taking)\b",
    re.IGNORECASE,
)

# Pattern: procedure / process / procedure question
_PROCEDURE_RE = re.compile(
    r"\b(procedure|process|steps?|how\s+does|what\s+is\s+the\s+order)\b",
    re.IGNORECASE,
)

# Pattern: appeal question
_APPEAL_RE = re.compile(r"\b(appeal|appeals|appealed|appealing)\b", re.IGNORECASE)

# Pattern: penalty question

# Form name aliases (keyword -> canonical form number)
# E.g. "notice" -> 'v' because Form V is the notice to the business operator.
_FORM_NAME_ALIASES = {
    "notice": "v",
    "analyst report": "vii",
    "appeal": "viii",
}

_PENALTY_RE = re.compile(
    r"\b(penalty|fine|punish|action.*(?:not OK|non-satisfactory|not satisfactory))\b", re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowRecognition:
    intent: str
    matched_patterns: list[str] = field(default_factory=list)
    form_mentioned: str | None = None
    action_keywords: list[str] = field(default_factory=list)
    procedure_keywords: list[str] = field(default_factory=list)
    is_workflow_query: bool = False

    @property
    def workflow_category(self) -> str | None:
        category_map = {
            "seizure_form_lookup": "form_lookup",
            "seizure_process": "procedure",
            "sampling_form_lookup": "form_lookup",
            "sampling_process": "procedure",
            "document_order": "procedure",
            "appeal_procedure": "procedure",
            "penalty_procedure": "procedure",
            "form_reference": "form_lookup",
        }
        return category_map.get(self.intent)


# ---------------------------------------------------------------------------
# Recogniser
# ---------------------------------------------------------------------------


def recognize_workflow_query(query: str) -> WorkflowRecognition:
    text = (query or "").strip()
    if not text:
        return WorkflowRecognition(intent="general", is_workflow_query=False)
    matched_patterns = []
    action_keywords = []
    procedure_keywords = []
    form_mentioned = None
    for keyword in ("seize", "sample"):
        if re.search(r"\b" + keyword + r"\b", text, re.IGNORECASE):
            action_keywords.append(keyword)
    m = _FORM_NUMBER_RE.search(text)
    if m:
        form_mentioned = m.group(1).lower()
        matched_patterns.append("form_number")
    else:
        # Also detect form by name alias (e.g. notice -> Form V)
        lower_text = text.lower()
        for alias, form_num in _FORM_NAME_ALIASES.items():
            if alias in lower_text:
                form_mentioned = form_num
                matched_patterns.append("form_name")
                break
    if _PROCEDURE_RE.search(text):
        procedure_keywords.append("procedure")
    if _FORM_LOOKUP_RE.search(text):
        matched_patterns.append("form_lookup")
        return WorkflowRecognition(
            intent="form_reference" if form_mentioned else "seizure_form_lookup",
            matched_patterns=matched_patterns,
            form_mentioned=form_mentioned,
            action_keywords=action_keywords,
            procedure_keywords=procedure_keywords,
            is_workflow_query=True,
        )
    if _SEARCH_ACTION_RE.search(text):
        if _APPEAL_RE.search(text):
            matched_patterns.append("appeal")
            return WorkflowRecognition(
                intent="appeal_procedure",
                matched_patterns=matched_patterns,
                action_keywords=action_keywords,
                procedure_keywords=procedure_keywords,
                is_workflow_query=True,
            )
        if _PENALTY_RE.search(text):
            matched_patterns.append("penalty")
            return WorkflowRecognition(
                intent="penalty_procedure",
                matched_patterns=matched_patterns,
                action_keywords=action_keywords,
                procedure_keywords=procedure_keywords,
                is_workflow_query=True,
            )
        matched_patterns.append("search_action")
        return WorkflowRecognition(
            intent="sampling_process" if "sample" in action_keywords else "seizure_process",
            matched_patterns=matched_patterns,
            action_keywords=action_keywords,
            procedure_keywords=procedure_keywords,
            is_workflow_query=True,
        )
    if _PROCEDURE_RE.search(text) and re.search(r"\b(order|sequence|order of|arrange)\b", text, re.IGNORECASE):
        matched_patterns.append("document_order")
        return WorkflowRecognition(
            intent="document_order",
            matched_patterns=matched_patterns,
            action_keywords=action_keywords,
            procedure_keywords=procedure_keywords,
            is_workflow_query=True,
        )
    return WorkflowRecognition(intent="general", is_workflow_query=False)


# ---------------------------------------------------------------------------
# Field extractor
# ---------------------------------------------------------------------------


def extract_workflow_fields(query: str) -> dict[str, Any]:
    recognition = recognize_workflow_query(query)
    return {
        "intent": recognition.intent,
        "is_workflow_query": recognition.is_workflow_query,
        "matched_patterns": list(recognition.matched_patterns),
        "form_mentioned": recognition.form_mentioned,
        "action_keywords": list(recognition.action_keywords),
        "procedure_keywords": list(recognition.procedure_keywords),
    }
