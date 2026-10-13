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
    "condition, exception, threshold or definition the answer depends on.\n"
    # Conditioning block v1 (2026-10-10). The scan line addressed *citation
    # breadth*; the block below addresses the residual *answer-quality*
    # failures. On the gold-in-prompt slice (60 qids where the provider admits
    # gold to the window and the audit still scored the answer wrong —
    # `python -m evaluation.rerun_gold_in_prompt --tag baseline`), the shipped
    # scan-only template graded 57/60 still wrong, decomposed by the human
    # audit's own model_action: abstention_gate 24 (premature or partial
    # refusal when the reference IS in the context), provision_check 20
    # (wrong section / wrong penalty figure provision identified),
    # needs_deeper_reasoning 16 (multi-part question answered partially,
    # truncated source not flagged, completeness claimed). Each directive is
    # additive wording: no hard precondition, no gating clause, no prescribed
    # closing format — the shapes that regressed abstention in ADR-0010
    # (§7.1 quote gate 0.073 -> 0.436 false abstention; §7.3 enumerate
    # candidate p=0.004). Success is measured on the same slice per directive
    # iteration; `abstained` is the binding risk to watch.
    "Answer every part the question names, each with its own [n] citations. "
    "If one part is not covered by the context, answer the other parts and "
    "say which part is uncovered rather than declining the whole question; "
    "before stating that the context does not address something, point to "
    "the numbered sources you checked for it.\n"
    "For each material legal proposition, name the exact provision you rely "
    "on (Act and section) beside its [n] citation. If more than one section "
    "could apply, say which one governs and why. If the context holds more "
    "than one version of a provision, use the version the question asks "
    "about and say which that is.\n"
    "When a penalty is at issue, state its full structure — base fine and/or "
    "imprisonment, any continuing daily fine, any escalation on repeat "
    "conviction — with exact figures as printed in the source, never a "
    "rounded summary.\n"
    "If a source is cut off mid-clause, say which point is missing instead "
    "of reconstructing it, and do not call a list complete unless the "
    "context establishes it.\n\n"
    "Question: {query}\n"
    "Answer:"
)

#: Two-pass structured answering (W3).  When ``ENABLE_STRUCTURED_REASONER``
#: is on, ``StructuredReasoner`` first builds the IRAC-shaped IR
#: (``structured_reasoner.py``: issue -> provisions -> definitions -> rules ->
#: exceptions/conditions -> conclusion) in its own LLM call, and the final
#: answer is rendered *from* that IR.  The answer prompt below therefore
#: carries the reasoning as content instead of prescribing an emitted
#: scratchpad: the out-of-band pass removes the 1024-token budget conflict
#: that an in-answer IRAC scratchpad would create (§1.3 of
#: docs/PROMPT_CONDITIONING_IRAC_RESEARCH.md), at the cost of one extra LLM
#: call per query.
#:
#: Abstention safety: the memo layout reorders content, it does not gate on
#: quoting and it prescribes no closing format — the two shapes that drove
#: the reverted SPEC-1 (0.073 -> 0.436 false abstention) and the rejected
#: enumerate candidate (p=0.004). The qualification clause is carried over
#: verbatim in substance from the FSSAI two-step contract. Flag-gated; the
#: shipped ``grounded_qa`` template is untouched.
GROUNDED_QA_STRUCTURED_SYSTEM_PROMPT = (
    "You are a senior legal counsel answering from statute. Answer using ONLY "
    "the <legal_context> sources and the structured reasoning provided. Work "
    "in two steps. Step 1 — check the structured reasoning against the "
    "sources, especially every exception or proviso it lists. Step 2 — write "
    "the answer as a legal memo, in this order: Issue (the precise legal "
    "question), Rule (the governing provision with its Act and section, every "
    "qualifying proviso or exception, and the full penalty structure where a "
    "penalty is at issue), Application (the rule applied to the facts the "
    "question states), Conclusion (the determination). "
    "Citation contract: (1) every material legal claim carries at least one "
    "[n] citation; (2) each [n] maps to a source shown in the context "
    "(e.g. [1], [2]) — never cite a source that is not shown; "
    "(3) never fabricate facts or figures. Where the structured reasoning "
    "marks a condition unknown, state so clearly and qualify what is unknown "
    "instead of guessing."
)

GROUNDED_QA_STRUCTURED_USER_TEMPLATE = (
    "Relevant legal context:\n"
    "<legal_context>\n"
    "{context}\n"
    "</legal_context>\n\n"
    "Structured legal reasoning (Issue, provisions, rules, exceptions and "
    "conditions, already read from the sources):\n"
    "{reasoning}\n\n"
    "Write the final answer as a legal memo — Issue, Rule (with every proviso "
    "and the full penalty structure where applicable), Application, "
    "Conclusion — with an [n] citation on every material legal proposition "
    "and exact figures as printed in the sources. Qualify anything the "
    "reasoning marks unknown instead of guessing.\n\n"
    "Question: {query}\n"
    "Answer:"
)

#: W4 — query-type conditioned directives. The classification already reaches
#: generation (``generate(query_type=...)``) but the rendered prompt was
#: query-type-blind: identical wording for a penalty question and an exemption
#: question. Each entry is one additive sentence aimed at the audit failure
#: mode that dominates its type, appended before ``Question:``.
#:
#: Gated by ``RAG_QUERY_TYPE_DIRECTIVES`` (default off) and measured through
#: ``evaluation/ab_baseline_citation.py --qtype <type>`` before the flag is
#: flipped; `abstained` is the binding risk on every type.
QUERY_TYPE_DIRECTIVES: dict[str, str] = {
    "penalty": (
        "For this penalty question, state the full structure of the sanction — "
        "base fine and/or imprisonment, any continuing daily fine, and any "
        "escalation on repeat conviction — with exact figures as printed in "
        "the source, never a rounded summary."
    ),
    "exception": (
        "For this exemption question, quote the exception, proviso or "
        "non-applicability clause itself, with its own [n] citation, before "
        "stating what the general rule provides."
    ),
    "definition": (
        "For this definition question, answer from the operative provision "
        "that governs, not from a general definition: name the operative "
        "clause and say how the definition bears on it."
    ),
    "prohibition": (
        "For this prohibition question, restate every element the source "
        "joins with 'and' — dropping one changes the offence — and do not "
        "soften the prohibition into a permission."
    ),
    "obligation": (
        "For this obligation question, restate the duty as written, with "
        "every cumulative requirement, and identify who must discharge it."
    ),
    "offence": (
        "For this offence question, identify the actus reus elements the "
        "source states, all of them, and the authority that takes cognizance."
    ),
    "procedure": (
        "For this procedure question, state the designated authority, any "
        "statutory deadline in days, and the statutory Form by name."
    ),
    "temporal": (
        "For this temporal question, state which version of the provision the "
        "question asks about and how the versions differ."
    ),
    "enforcement": (
        "For this enforcement question, name the enforcing authority and the "
        "specific power it exercises under the cited provision."
    ),
}

_TEMPLATES: dict[str, tuple[str, str]] = {
    "grounded_qa": (GROUND_QA_SYSTEM_PROMPT, GROUND_QA_USER_TEMPLATE),
    # W3: two-pass structured answering. Selected by GroundedGenerationService
    # when cfg.structured_reasoner is on and the reasoner IR is available.
    "grounded_qa_structured": (
        GROUNDED_QA_STRUCTURED_SYSTEM_PROMPT,
        GROUNDED_QA_STRUCTURED_USER_TEMPLATE,
    ),
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
        query_type: str = "",
        **extra_vars: Any,
    ) -> tuple[str, str]:
        """Convenience: render the ``grounded_qa`` template.

        Args:
            query: The user's question.
            context: Retrieved legal context.
            domain: Optional domain key (``fssai`` / ``env`` / ``commercial``
                / ``animal`` / ``wb_state`` / ``general``); ``None`` uses the
                FSSAI default (backward compatible).
            query_type: Optional classified query type.  When it matches an
                entry in :data:`QUERY_TYPE_DIRECTIVES`, that directive is
                appended before the ``Question:`` line.  Empty (the default)
                leaves the rendered prompt byte-identical to before.

        """
        vars_dict = dict(extra_vars)
        if domain:
            vars_dict["domain"] = domain
        system_prompt, user_prompt = self.render(
            "grounded_qa",
            query=query,
            context=context,
            extra_vars=vars_dict or None,
        )
        # W4: the directive is inserted before the question so it still reads
        # as an instruction, not as trailing content after "Answer:".
        directive = QUERY_TYPE_DIRECTIVES.get((query_type or "").strip().lower())
        if directive:
            marker = "\nQuestion: "
            if marker in user_prompt:
                user_prompt = user_prompt.replace(marker, f"\n{directive}{marker}", 1)
        return system_prompt, user_prompt
