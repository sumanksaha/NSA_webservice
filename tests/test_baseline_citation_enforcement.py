"""SPEC-1 citation enforcement: measured, REVERTED, pinned against regression.

SPEC-1 proposed hardening the grounded-QA prompt into a hard "quote at least
one verbatim passage, or abstain" gate.  Measured on the baseline-gold slice
(55 paired qids, ``evaluation/ab_baseline_citation.py``), that change was a
net regression and has been reverted:

    metric              shipped    SPEC-1     delta
    citation_recall      0.659     0.466     -0.192
    false_abstention     0.073     0.436     +0.364  (6x)
    hallucination        0.018     0.218     +0.200  (12x)

SPEC-1's premise ("baseline citation_recall = 0.00") was an artifact of the
recovered-only Stage-2 population, whose baseline windows contain no gold chunk
at all; on a population where the metric is defined the shipped prompt already
exceeds SPEC-1's own >= 0.50 target.

These tests pin the *shipped* contract and assert the reverted gate stays out.
Do not re-add a hard quote gate without re-running the A/B.
"""
from __future__ import annotations

from app.rag.generation.prompt_template import (
    DOMAIN_SYSTEM_PROMPTS,
    GROUND_QA_SYSTEM_PROMPT,
    GROUND_QA_USER_TEMPLATE,
    PromptTemplate,
)


class TestShippedContract:
    """The shipped prompt keeps the [n] citation contract for every domain."""

    def test_every_domain_prompt_contains_citation_markers(self):
        for domain, prompt in DOMAIN_SYSTEM_PROMPTS.items():
            assert "[n]" in prompt, f"domain={domain} missing [n] citation marker"

    def test_every_domain_prompt_forbids_fabrication(self):
        for domain, prompt in DOMAIN_SYSTEM_PROMPTS.items():
            assert "never fabricate" in prompt.lower(), (
                f"domain={domain} missing no-fabrication rule"
            )

    def test_fssai_prompt_is_the_default(self):
        assert DOMAIN_SYSTEM_PROMPTS["fssai"] == GROUND_QA_SYSTEM_PROMPT

    def test_all_expected_domains_present(self):
        expected = {"fssai", "env", "commercial", "animal", "wb_state", "criminal", "general"}
        assert expected == set(DOMAIN_SYSTEM_PROMPTS.keys())

    def test_user_template_asks_to_quote_then_cite(self):
        assert "quote" in GROUND_QA_USER_TEMPLATE.lower()
        assert "[n]" in GROUND_QA_USER_TEMPLATE


class TestRevertedGateStaysOut:
    """Guard against re-introducing the regression measured above."""

    def test_no_hard_at_least_one_verbatim_gate(self):
        # "at least one verbatim passage ... you MUST" drove the abstention blowup.
        lowered = GROUND_QA_USER_TEMPLATE.lower()
        assert "at least one verbatim" not in lowered
        assert "you must follow these steps" not in lowered

    def test_no_zero_quote_abstention_clause(self):
        # The "only when you produced zero quotes" clause gated abstention on quoting.
        assert "zero relevant passages" not in GROUND_QA_USER_TEMPLATE.lower()
        assert "produced zero quotes" not in GROUND_QA_USER_TEMPLATE.lower()

    def test_no_shared_domain_contract_injection(self):
        # SPEC-1 also pushed the two-step contract into every domain prompt via a
        # shared suffix.  Reverted with the rest; the module must not re-graft it.
        import app.rag.generation.prompt_template as pt

        assert not hasattr(pt, "_DOMAIN_CONTRACT")


class TestShippedScanInstruction:
    """The shipped SPEC-1 fix, and the evidence it is allowed to rest on.

    The "go through every numbered source" line is what lifted citation recall.
    It is pinned here so it is not quietly dropped, alongside the guard tests
    that keep the reverted abstention gate out.
    """

    def test_shipped_template_keeps_the_scan_instruction(self):
        lowered = GROUND_QA_USER_TEMPLATE.lower()
        assert "go through every numbered source" in lowered, "SPEC-1 scan instruction was dropped"
        assert "bear on the question" in lowered

    def test_scan_instruction_names_the_provision_classes_it_targets(self):
        # These are the clause kinds that made a single passage look sufficient.
        lowered = GROUND_QA_USER_TEMPLATE.lower()
        for token in ("condition", "exception", "threshold", "definition"):
            assert token in lowered, f"scan instruction no longer mentions {token}"

    def test_shipped_template_still_quotes_then_answers(self):
        assert "First quote the passages that bear on the question" in GROUND_QA_USER_TEMPLATE
        assert "[n] markers" in GROUND_QA_USER_TEMPLATE

    def test_shipped_template_is_the_winning_candidate_verbatim(self):
        """The shipped wording must equal the measured candidate exactly.

        Guards against drifting away from the configuration the A/B validated.
        """
        from evaluation.ab_baseline_citation import _CANDIDATES

        assert _CANDIDATES["cand_scan"] == GROUND_QA_USER_TEMPLATE


class TestSpec1Candidates:
    """Candidate prompts must add citation pressure WITHOUT re-adding the gate.

    The reverted SPEC-1 failed because it coupled citation to a hard quote
    precondition plus an abstention escape clause. Every candidate is additive
    citation wording only, so these tests pin that separation.
    """

    def _candidates(self) -> dict[str, str]:
        from evaluation.ab_baseline_citation import VARIANTS

        return {k: v[1] for k, v in VARIANTS.items() if k.startswith("cand_")}

    def test_there_are_candidates(self):
        assert self._candidates(), "no SPEC-1 candidates registered"

    def test_candidates_keep_the_quote_then_answer_line(self):
        for name, tpl in self._candidates().items():
            assert "First quote the passages that bear on the question" in tpl, (
                f"{name} dropped the legacy quote-then-answer instruction"
            )

    def test_candidates_have_no_abstention_gate(self):
        """The exact clauses that caused the 0.073 -> 0.436 false-abstention blowup."""
        banned = (
            "at least one verbatim",
            "you must follow these steps",
            "zero relevant passages",
            "produced zero quotes",
            "cannot find the answer",
        )
        for name, tpl in self._candidates().items():
            lowered = tpl.lower()
            for phrase in banned:
                assert phrase not in lowered, f"{name} re-introduces banned gate {phrase!r}"

    def test_candidates_add_citation_pressure(self):
        """A candidate must add wording the legacy template does not have."""
        from evaluation.ab_baseline_citation import LEGACY_USER_TEMPLATE

        legacy_lines = set(LEGACY_USER_TEMPLATE.splitlines())
        for name, tpl in self._candidates().items():
            lowered = tpl.lower()
            # "cit" covers cite/citing/citation without over-fitting wording.
            assert "cit" in lowered, f"{name} adds no citation instruction"
            assert "[n]" in tpl or "[source" in lowered, f"{name} names no citation form"
            added = [ln for ln in tpl.splitlines() if ln.strip() and ln not in legacy_lines]
            assert added, f"{name} is byte-identical to legacy; no pressure added"

    def test_candidates_are_format_safe(self):
        from evaluation.ab_baseline_citation import VARIANTS

        for name, (system, tpl) in VARIANTS.items():
            assert "{context}" in tpl, f"{name} missing {{context}}"
            assert "{query}" in tpl, f"{name} missing {{query}}"
            assert "<legal_context>" in tpl and "</legal_context>" in tpl, f"{name} malformed context block"
            rendered = tpl.format(context="CTX", query="Q?")
            assert "CTX" in rendered and "Q?" in rendered, name
            assert system, name

    def test_every_variant_has_a_distinct_template(self):
        from evaluation.ab_baseline_citation import VARIANTS

        templates = [v[1] for v in VARIANTS.values()]
        assert len(set(templates)) == len(templates), "two variants share a template"

    def test_shipped_template_has_no_candidate_gate(self):
        lowered = GROUND_QA_USER_TEMPLATE.lower()
        for phrase in ("at least one verbatim", "you must follow these steps", "produced zero quotes"):
            assert phrase not in lowered, f"shipped template re-gained {phrase!r}"


class TestRenderedPrompt:
    """End-to-end: rendered prompts for every domain carry the [n] contract."""

    def test_rendered_prompt_for_every_domain_cites_sources(self):
        pt = PromptTemplate()
        for domain in DOMAIN_SYSTEM_PROMPTS:
            sys_p, user_p = pt.render_default(
                "what is the punishment under Section 31?",
                "[1] Section 31 of the FSS Act says X. [2] Section 32 says Y.",
                domain=domain,
            )
            assert "[n]" in sys_p, f"domain={domain} system prompt missing [n]"
            assert "[n]" in user_p, f"domain={domain} user prompt missing [n]"

    def test_unknown_domain_falls_back_to_fssai(self):
        pt = PromptTemplate()
        sys_p, _ = pt.render_default("question", "[1] context", domain="nonexistent_domain")
        assert sys_p == GROUND_QA_SYSTEM_PROMPT

    def test_rendered_prompt_has_well_formed_context(self):
        pt = PromptTemplate()
        _, user_p = pt.render_default("test question", "[1] test context", domain="fssai")
        assert "<legal_context>" in user_p
        assert "</legal_context>" in user_p
        assert "Question: test question" in user_p
