"""
suggester.py

LangChain-based suggester that recommends applicable FSS Act sections
(55, 56, 58, 63, 64) based on inspection checklist results and case facts.
Used by the /suggest_sections route as the "AI-suggest" half of the
hybrid officer-review workflow: this module only proposes section IDs and
one-line reasoning; it never writes to the case pack or submits anything.
The officer retains final authority via the editable checkboxes in the UI.
"""

import json
import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from sections_data import SECTIONS, VALID_SECTION_IDS

logger = logging.getLogger(__name__)

# Fields that are case metadata, not checklist items — excluded when
# building the "checklist" payload sent to the model.
_NON_CHECKLIST_FIELDS = {
    "food_safety_officer",
    "case_number",
    "fbo_owner",
    "fbo_name",
    "fbo_address",
    "fssai_license",
    "concerned_food",
    "complaint_lodged",
    "problem",
    "non_license",
    "pre_authorization",
    "First_inspection_date",
    "compliance_deadline",
    "Complaint_date",
    "inspection_date",
    "authorization_date",
}

# Checklist items that directly indicate unhygienic/unsanitary conditions.
_HYGIENE_CHECKLIST_ITEMS = {
    "clean_premise": "Premises found inadequately maintained and unhygienic.",
    "refrigerator_clean": "Refrigeration facilities found unclean.",
    "proper_attire": "Food handlers lacked prescribed protective attire.",
    "proper_covered_utensil": "Food and utensils were left uncovered.",
    "food_segregation": "Improper food segregation — risk of cross-contamination.",
    "veg_nonveg_separation": "Veg/non-veg segregation not maintained.",
}

# Sections the officer must tick manually — never returned by the suggester.
_MANUAL_ONLY_SECTIONS = {"58"}

SYSTEM_PROMPT = """You are assisting a Food Safety Officer under the FSS Act, 2006.
Given inspection checklist results and case facts, suggest whether Section 56
and/or Section 64 are applicable. Only choose from: 56, 64.

Section reference text:
{section_text}

Rules:
- Sec 56 applies if unhygienic/unsanitary manufacturing/processing was observed
  OR can reasonably be inferred from the checklist and case facts.
- Sec 64 applies ONLY if this is an explicitly flagged repeat/subsequent offence.
- Do NOT suggest Sections 55, 58, or 63 — those are handled separately.
- Base your answer only on the facts given. Do not assume facts not present.
- If uncertain about a section, omit it rather than guessing.
- Return STRICT JSON only, no markdown fencing, no commentary outside the JSON:
  {{"sections": ["56"], "reasoning": {{"56": "one line"}}}}
"""

USER_PROMPT = """Case facts:
{case_facts}

Inspection checklist (yes = non-compliant unless noted otherwise):
{checklist}

Non-license flag: {non_license}
Pre-authorization case: {pre_authorization}
Complaint lodged: {complaint_lodged}
Complaint details: {problem}
"""

_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("user", USER_PROMPT),
    ]
)

_llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
_parser = JsonOutputParser()

_chain = _prompt | _llm | _parser


def _is_non_license(form_data: dict) -> bool:
    return str(form_data.get("non_license", "no")).strip().lower() == "yes"


def _detect_section_56_from_checklist(form_data: dict) -> tuple[bool, str]:
    """
    Returns (applies, one-line reasoning) when checklist items directly
    indicate unhygienic/unsanitary manufacturing or processing.
    """
    violations = [
        desc
        for field, desc in _HYGIENE_CHECKLIST_ITEMS.items()
        if form_data.get(field) == "no"
    ]
    if not violations:
        return False, ""

    summary = "; ".join(violations[:2])
    if len(violations) > 2:
        summary += f"; and {len(violations) - 2} more hygiene issue(s)"
    return True, f"Checklist shows unhygienic/unsanitary conditions: {summary}."


def _invoke_llm_suggestions(form_data: dict, section_text: str) -> dict:
    checklist_items = {
        k: v for k, v in form_data.items() if k not in _NON_CHECKLIST_FIELDS
    }
    case_facts = (
        f"FBO name: {form_data.get('fbo_name', '')}\n"
        f"FBO owner: {form_data.get('fbo_owner', '')}\n"
        f"FSSAI licence: {form_data.get('fssai_license', '')}\n"
        f"Concerned food: {form_data.get('concerned_food', '')}\n"
        f"Case number: {form_data.get('case_number', '')}"
    )

    try:
        raw_result = _chain.invoke(
            {
                "section_text": section_text,
                "case_facts": case_facts,
                "checklist": json.dumps(checklist_items),
                "non_license": form_data.get("non_license", "no"),
                "pre_authorization": form_data.get("pre_authorization", "no"),
                "complaint_lodged": form_data.get("complaint_lodged", "no"),
                "problem": form_data.get("problem", ""),
            }
        )
    except Exception:
        logger.exception("suggest_sections: LangChain invocation failed")
        return {"sections": [], "reasoning": {}}

    if not isinstance(raw_result, dict):
        logger.warning(
            "suggest_sections: unexpected result type %r", type(raw_result)
        )
        return {"sections": [], "reasoning": {}}

    sections = [
        s
        for s in raw_result.get("sections", [])
        if s in VALID_SECTION_IDS and s not in _MANUAL_ONLY_SECTIONS
    ]
    reasoning = {
        k: v
        for k, v in raw_result.get("reasoning", {}).items()
        if k in VALID_SECTION_IDS and k not in _MANUAL_ONLY_SECTIONS
    }
    return {"sections": sections, "reasoning": reasoning}


def _build_section_reference_text() -> str:
    """Concatenates the reference text for the five allowed sections only."""
    return "\n\n".join(
        SECTIONS[s] for s in sorted(VALID_SECTION_IDS) if s in SECTIONS
    )


def suggest_sections(form_data: dict) -> dict:
    """
    Given the raw form submission (as a dict, e.g. from request.form.to_dict()),
    returns {"sections": [...], "reasoning": {...}}.

    Section selection rules:
    1. Non-licensed FBO (non_license=yes) -> only Section 63.
    2. Licensed FBO -> Section 55 is always applied.
    3. Section 58 is never auto-suggested; the officer ticks it manually.
    4. Section 56 is applied when hygiene violations are observed in the
       checklist or inferred by the model from the input data.
    5. Section 64 may be suggested by the model for repeat offences.

    Never raises on LLM/parsing failure — deterministic sections (55/63/56
    from checklist) are still returned so the officer can review and override.
    """
    section_text = _build_section_reference_text()

    # Rule 1: non-licensed cases — Section 63 only.
    if _is_non_license(form_data):
        return {
            "sections": ["63"],
            "reasoning": {
                "63": (
                    "FBO is non-licensed/unregistered — "
                    "Section 63 applies exclusively."
                )
            },
        }

    # Rule 2: licensed cases — Section 55 is mandatory.
    sections: list[str] = ["55"]
    reasoning: dict[str, str] = {
        "55": (
            "Licensed FBO follow-up inspection — mandatory application of "
            "Section 55 for failure to comply with prior FSO directions."
        )
    }

    # Rule 4a: checklist-based Section 56 detection.
    hygiene_applies, hygiene_reason = _detect_section_56_from_checklist(form_data)
    if hygiene_applies:
        sections.append("56")
        reasoning["56"] = hygiene_reason

    # Rule 4b / 5: model may infer Section 56 and suggest Section 64.
    llm_result = _invoke_llm_suggestions(form_data, section_text)
    for section_id in llm_result.get("sections", []):
        if section_id not in sections:
            sections.append(section_id)
        if section_id in llm_result.get("reasoning", {}):
            reasoning[section_id] = llm_result["reasoning"][section_id]

    # Rule 3: Section 58 is manual-only — never included in suggestions.
    sections = [s for s in sections if s not in _MANUAL_ONLY_SECTIONS]

    return {"sections": sections, "reasoning": reasoning}
