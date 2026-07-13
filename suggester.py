"""
suggester.py

LangChain-based suggester that recommends applicable FSS Act sections
(55, 56, 58, 63, 64) based on inspection checklist results and case facts.
Used by the /suggest_sections Flask route as the "AI-suggest" half of the
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

SYSTEM_PROMPT = """You are assisting a Food Safety Officer under the FSS Act, 2006.
Given inspection checklist results and case facts, suggest which of the following
sections are applicable. Only choose from: 55, 56, 58, 63, 64.

Section reference text:
{section_text}

Rules:
- Sec 63 applies ONLY if the FBO has no valid FSSAI licence (non-license flag is yes).
- Sec 56 applies if unhygienic/unsanitary manufacturing/processing was observed.
- Sec 55 applies if the FBO failed to comply with a prior FSO directive/instruction sheet.
- Sec 58 applies as a catch-all only if a real contravention exists with no specific
  section fitting (55/56/63).
- Sec 64 applies ONLY if this is an explicitly flagged repeat/subsequent offence.
- Base your answer only on the facts given. Do not assume facts not present.
- If uncertain about a section, omit it rather than guessing.
- Return STRICT JSON only, no markdown fencing, no commentary outside the JSON:
  {{"sections": ["55","58"], "reasoning": {{"55": "one line", "58": "one line"}}}}
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


def _build_section_reference_text() -> str:
    """Concatenates the reference text for the five allowed sections only."""
    return "\n\n".join(
        SECTIONS[s] for s in sorted(VALID_SECTION_IDS) if s in SECTIONS
    )


def suggest_sections(form_data: dict) -> dict:
    """
    Given the raw form submission (as a dict, e.g. from request.form.to_dict()),
    returns {"sections": [...], "reasoning": {...}}.

    Never raises on LLM/parsing failure — returns an empty suggestion set so
    the caller can fall back to manual checkbox entry without breaking the form.
    All returned section IDs are validated against VALID_SECTION_IDS before
    being handed back, regardless of what the model outputs.
    """
    section_text = _build_section_reference_text()

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
        logger.warning("suggest_sections: unexpected result type %r", type(raw_result))
        return {"sections": [], "reasoning": {}}

    # Server-side validation — never trust raw model output directly.
    sections = [
        s for s in raw_result.get("sections", []) if s in VALID_SECTION_IDS
    ]
    reasoning = {
        k: v
        for k, v in raw_result.get("reasoning", {}).items()
        if k in VALID_SECTION_IDS
    }

    return {"sections": sections, "reasoning": reasoning}
