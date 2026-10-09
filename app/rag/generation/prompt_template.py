"""Prompt templates for grounded RAG generation.

Provides a template registry and rendering for the grounded-QA prompt
used by the LLM client to produce citable legal answers.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

GROUND_QA_SYSTEM_PROMPT = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer using ONLY the <legal_context> sources below. "
    "Work in two steps. Step 1 — quote: first extract the short passages "
    "that bear on the question, quoting them verbatim. Step 2 — answer: "
    "give a concise, legally precise answer derived strictly from those quotes. "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts. If the answer is not in the context, state "
    "so clearly and qualify what is unknown instead of guessing."
)

#: Domain-parameterized system prompts (Phase 1 — de-FSSAI).  The FSSAI
#: domain is the default; ``render(..., extra_vars={"domain": ...})`` or
#: ``render_default(..., domain=...)`` selects one of these.  Unknown or
#: missing domains fall back to the FSSAI prompt (backward compatible).
DOMAIN_SYSTEM_PROMPTS: dict[str, str] = {
    "fssai": GROUND_QA_SYSTEM_PROMPT,
    "env": (
        "You are a legal assistant specialised in Indian environmental law "
        "(Environment (Protection) Act, 1986; Water and Air pollution control "
        "Acts; plastic waste and solid waste management rules). Answer "
        "questions using ONLY the provided context. Cite sources using [n] "
        "markers (e.g. [1], [2]). If the answer is not in the context, state "
        "so clearly. Never fabricate facts or cite sources not in the context. "
        "Keep answers concise and legally precise."
    ),
    "commercial": (
        "You are a legal assistant specialised in Indian commercial and "
        "corporate law (Companies Act, 2013; Contract Act, 1872; Partnership "
        "and LLP Acts; Sale of Goods, Limitation, Specific Relief and Consumer "
        "Protection Acts). Answer questions using ONLY the provided context. "
        "Cite sources using [n] markers (e.g. [1], [2]). If the answer is not "
        "in the context, state so clearly. Never fabricate facts or cite "
        "sources not in the context. Keep answers concise and legally precise."
    ),
    "animal": (
        "You are a legal assistant specialised in Indian animal welfare and "
        "livestock law (Prevention of Cruelty to Animals Act/Rules; Bengal "
        "diseases-of-animals and livestock quarantine instruments). Answer "
        "questions using ONLY the provided context. Cite sources using [n] "
        "markers (e.g. [1], [2]). If the answer is not in the context, state "
        "so clearly. Never fabricate facts or cite sources not in the context. "
        "Keep answers concise and legally precise."
    ),
    "wb_state": (
        "You are a legal assistant specialised in West Bengal state law "
        "(Kolkata Municipal Corporation Act, 1980; West Bengal Premises "
        "Tenancy Act, 1997). Answer questions using ONLY the provided context. "
        "Cite sources using [n] markers (e.g. [1], [2]). If the answer is not "
        "in the context, state so clearly. Never fabricate facts or cite "
        "sources not in the context. Keep answers concise and legally precise."
    ),
    "criminal": (
        "You are a legal assistant specialised in Indian criminal law "
        "(Bharatiya Nyaya Sanhita, 2023 — the successor to the Indian Penal "
        "Code, 1860). Answer questions using ONLY the provided context. Cite "
        "sources using [n] markers (e.g. [1], [2]). If the answer is not in "
        "the context, state so clearly. Never fabricate facts or cite sources "
        "not in the context. Keep answers concise and legally precise."
    ),
    "general": (
        "You are a legal assistant specialised in Indian law. Answer questions "
        "using ONLY the provided context. Cite sources using [n] markers where "
        "n is the source number shown in the context (e.g. [1], [2]). If the "
        "answer is not in the context, state so clearly. Never fabricate facts "
        "or cite sources not in the context. Keep answers concise and legally "
        "precise."
    ),
}

GROUND_QA_USER_TEMPLATE = (
    "Relevant legal context:\n"
    "<legal_context>\n"
    "{context}\n"
    "</legal_context>\n\n"
    # The "scan every source" line is the SPEC-1 fix that actually worked.
    # Measured on the baseline-gold slice, 3 pooled candidate replicates vs 4
    # legacy replicates: citation_recall 0.6388 -> 0.7147, delta +0.076,
    # t=4.37, p<0.0001, with COMPLETE separation between the two replicate sets
    # (worst candidate 0.695 > best legacy 0.661). Paired within-qid (n=55):
    # +0.082, p=0.026. citation_precision is flat (-0.009, p=0.78).
    # It works because under-citing was a *scan* problem: the model answered
    # from the most obvious passage and never reconsidered the rest of the
    # window, so n_citations sat at 3.75 against an average of 4.54 gold chunks
    # present in the window.
    #
    # Do NOT re-add a hard quote gate or an abstention escape clause. Measured
    # and reverted: a "quote at least one verbatim passage or abstain" variant
    # drove false abstention from 0.07 to 0.44 and citation_recall DOWN 0.19,
    # because fewer answers means fewer citations. See ADR-0010 §7.1.
    #
    # Measured but NOT established: abstention. Two replicates suggested a large
    # drop; a third came in at 0.130 against a legacy range of 0.071-0.143, and
    # the pooled test gives p=0.27. Treat abstention as unchanged.
    #
    # Known trade to watch: breadth pushes is_padded (answers citing >= 9 of 10
    # sources) from 0.075 to 0.175.
    "First quote the passages that bear on the question, then answer "
    "the question using those quotes, citing specific sources with "
    "[n] markers.\n"
    "Before answering, go through every numbered source in the context and "
    "note the ones that bear on the question, including any that add a "
    "condition, exception, threshold or definition the answer depends on.\n\n"
    "Question: {query}\n"
    "Answer:"
)

_TEMPLATES: dict[str, tuple[str, str]] = {
    "grounded_qa": (GROUND_QA_SYSTEM_PROMPT, GROUND_QA_USER_TEMPLATE),
}


class PromptTemplate:
    """Render grounded-QA prompts from a template registry."""

    def __init__(self, templates: dict[str, tuple[str, str]] | None = None) -> None:
        self._templates: dict[str, tuple[str, str]] = dict(templates) if templates else dict(_TEMPLATES)

    @property
    def available_actions(self) -> list[str]:
        return list(self._templates.keys())

    def render(
        self,
        action: str,
        *,
        query: str,
        context: str,
        extra_vars: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Render a named prompt template.

        Returns:
            A ``(system_prompt, user_prompt)`` tuple.

        """
        if action not in self._templates:
            raise ValueError(f"Unknown prompt action: {action!r}. Available: {list(self._templates.keys())}")
        system_prompt, user_template = self._templates[action]
        vars_dict: dict[str, Any] = {"query": query, "context": context}
        if extra_vars:
            vars_dict.update(extra_vars)
            domain = str(extra_vars.get("domain") or "").strip().lower()
            if domain in DOMAIN_SYSTEM_PROMPTS:
                system_prompt = DOMAIN_SYSTEM_PROMPTS[domain]
        user_prompt = user_template.format(**vars_dict)
        return system_prompt, user_prompt

    def render_default(
        self,
        query: str,
        context: str,
        domain: str | None = None,
        **extra_vars: Any,
    ) -> tuple[str, str]:
        """Convenience: render the ``grounded_qa`` template.

        Args:
            query: User question.
            context: Retrieved legal context.
            domain: Optional domain key (``fssai`` / ``env`` / ``commercial``
                / ``animal`` / ``wb_state`` / ``general``); ``None`` uses the
                FSSAI default (backward compatible).

        """
        vars_dict = dict(extra_vars)
        if domain:
            vars_dict["domain"] = domain
        return self.render(
            "grounded_qa",
            query=query,
            context=context,
            extra_vars=vars_dict or None,
        )
