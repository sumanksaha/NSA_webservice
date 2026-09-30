"""Intent-conditioned food-standard answer generation (task spec §13, §15, §16).

Two responsibilities:

1. **Anti-definition-anchoring system prompt** — when the query requests a
   standard/limit, the definition of the food is NOT an answer.  The
   definition may appear only as supplementary context (spec §13: "If the
   user asks for a food standard, a definition of the food is not an answer
   to the question.").
2. **Structured answer + completeness** — the final answer follows the
   Food / Applicable standard / Requirements / Source / Interpretation /
   Caveat layout (spec §15), built from the evidence bundle rather than
   isolated chunks, with explicit insufficiency when the standard cannot be
   established (never fabricate values).

The generation service of record stays ``GroundedGenerationService`` — this
module renders the *prompt inputs* (system prompt + user prompt from the
evidence bundle) and post-processes completeness into the response debug
payload.  Deterministic; no extra LLM calls.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "FOOD_STANDARD_SYSTEM_PROMPT",
    "PARAMETER_SYSTEM_PROMPT",
    "render_food_user_prompt",
    "build_food_system_prompt",
    "check_answer_completeness",
    "parameter_rows_from_evidence",
]


#: §13 — the anti-definition-anchoring instruction, verbatim intent.
FOOD_STANDARD_SYSTEM_PROMPT = (
    "You are a legal assistant specialised in FSSAI food-safety regulations. "
    "The user is asking for a REGULATORY STANDARD, not a description of the food. "
    "If the user asks for a food standard, a definition of the food is not an answer "
    "to the question. Use the regulatory standard/provision: the 'shall conform' "
    "provision, the parameter/requirement table rows, and the limits stated there. "
    "The definition may be included only as supplementary context when useful. "
    "Answer using ONLY the <legal_context> sources. Cite with [n] markers matching "
    "the numbered sources. Never invent a parameter value, limit, or source: if the "
    "evidence does not establish the standard, say exactly that the retrieved "
    "evidence is insufficient."
)

#: Parameter-specific variant (§14) — the named parameter's limit is the answer.
PARAMETER_SYSTEM_PROMPT = (
    "You are a legal assistant specialised in FSSAI food-safety regulations. "
    "The user asks for a SPECIFIC PARAMETER requirement (e.g. moisture, extraneous "
    "matter, ash) for a named food commodity. Answer with that parameter's "
    "prescribed value/limit from the standard's requirement table for THAT "
    "commodity only — ignore limits belonging to other commodities. A definition "
    "of the food is not an answer. Answer using ONLY the <legal_context> sources, "
    "cite with [n] markers, and never invent a value: if the parameter's limit is "
    "not in the evidence for that commodity, state that the retrieved evidence is "
    "insufficient."
)

#: Definition intent keeps the base grounded prompt (regression safety).
_DEFINITION_SYSTEM_PROMPT = (
    "You are a legal assistant specialised in FSSAI food-safety regulations. "
    "The user asks for the definition/description of a food commodity. Prefer the "
    "statutory definition ('X means …') from the regulation. Answer using ONLY the "
    "<legal_context> sources, cite with [n] markers, and never fabricate."
)


def build_food_system_prompt(intent: str) -> str:
    """Intent-conditioned system prompt."""
    if intent in ("food_standard", "requirement", "compliance"):
        return FOOD_STANDARD_SYSTEM_PROMPT
    if intent == "parameter_specific_standard":
        return PARAMETER_SYSTEM_PROMPT
    if intent == "definition":
        return _DEFINITION_SYSTEM_PROMPT
    return (
        "You are a legal assistant specialised in FSSAI food-safety regulations. "
        "Answer using ONLY the <legal_context> sources, cite with [n] markers, and "
        "never fabricate."
    )


def _bundle_texts(bundle: dict[str, Any]) -> list[tuple[str, str]]:
    """(role, text) pairs from the bundle in presentation order."""
    out: list[tuple[str, str]] = []
    for pc in bundle.get("parent_context") or []:
        out.append(("parent", pc if isinstance(pc, str) else str(pc.text or "")))
    for pe in bundle.get("primary_evidence") or []:
        out.append(("primary", str(pe.text or "")))
    for re_ in bundle.get("related_evidence") or []:
        out.append(("related", str(re_.text or "")))
    return out


def render_food_user_prompt(query: str, bundle: dict[str, Any]) -> str:
    """Render the user prompt from the §12 evidence bundle.

    Evidence blocks are numbered [1..n] in presentation order (parent context
    first so the generator reads the clause heading before the rows).
    """
    lines = ["Relevant legal context:", "<legal_context>"]
    n = 0
    for role, text in _bundle_texts(bundle):
        if not text.strip():
            continue
        n += 1
        label = {"parent": "STANDARD CONTEXT", "primary": "PRIMARY EVIDENCE", "related": "RELATED EVIDENCE"}[role]
        lines.append(f"[{n}] ({label})\n{text.strip()}")
    lines.append("</legal_context>")
    source = bundle.get("legal_source") or {}
    if source:
        lines.append(
            "Legal location (verified from evidence metadata): "
            + ", ".join(f"{k}={v}" for k, v in source.items())
        )
    lines.append("")
    lines.append(
        "First quote the passages that bear on the question, then answer using those "
        "quotes, citing specific sources with [n] markers."
    )
    lines.append("")
    lines.append(f"Question: {query}")
    lines.append("Answer:")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §16 — answer-level completeness + parameter extraction
# ---------------------------------------------------------------------------

_ROW_RE = re.compile(
    r"\((?P<mark>[ivx]+|\d+)\)\s+(?P<name>[A-Z][^:;(){]{2,60}?)\s+(?P<value>"
    r"Not\s+more\s+than[^();]{1,40}|Not\s+less\s+than[^();]{1,40}|"
    r"[\d.]+\s*(?:per\s+cent|percent)[^();]{0,30})",
    re.IGNORECASE,
)

_ANSWER_VALUE_RE = re.compile(
    r"\b(?:not\s+more\s+than|not\s+less\s+than|maximum|minimum|up\s+to|at\s+least)?\s*"
    r"\d+(?:\.\d+)?\s*(?:per\s+cent|percent|%|mg/kg|ppm|mg)\b",
    re.IGNORECASE,
)


def parameter_rows_from_evidence(bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Deterministic (parameter, value) rows extracted from bundle evidence.

    Only rows with an explicit measurement value are returned — nothing is
    inferred or normalised beyond casing/whitespace.
    """
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for _role, text in _bundle_texts(bundle):
        for m in _ROW_RE.finditer(text or ""):
            name = re.sub(r"\s+", " ", m.group("name")).strip().rstrip(",;")
            value = re.sub(r"\s+", " ", m.group("value")).strip().rstrip(",;")
            key = (name.lower(), value.lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append({"parameter": name, "value": value})
    return rows


def check_answer_completeness(
    query: str,
    answer: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """§16 answer-level completeness verdict (deterministic, no LLM).

    Combines the bundle's evidence flags with an answer-level read:
    numeric-value presence for standard/parameter intents, and a
    definition-leak detector (did the answer cite the definition sentence
    when a standard was requested?).
    """
    from app.rag.retrieval.food_query_understanding import FoodQueryUnderstanding

    fq = FoodQueryUnderstanding.from_query(query)
    evidence_flags = dict(bundle.get("completeness") or {})
    low_answer = (answer or "").lower()

    numeric_present = bool(_ANSWER_VALUE_RE.search(answer or ""))
    definition_leak = False
    if fq.wants_standard:
        # The definition's signature phrase — "means the dried mature fruits",
        # i.e. a "X means" sentence quoted as the answer's substance.
        definition_leak = bool(re.search(r"\bmeans\s+the\b", low_answer)) and not numeric_present

    complete = all(
        evidence_flags.get(k, False)
        for k in ("entity_found", "intent_satisfied", "standard_found", "source_found")
    )
    if fq.parameters:
        complete = complete and bool(evidence_flags.get("parameter_complete", False)) and numeric_present

    return {
        **evidence_flags,
        "numeric_value_present": numeric_present,
        "definition_leak": definition_leak,
        "answer_complete": complete and not definition_leak,
    }
