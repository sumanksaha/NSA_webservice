"""Tests for the prompt template registry (app/rag/generation/prompt_template.py).

Two invariants matter here:

* the **shipped** template must stay byte-identical when the answer-shape
  arms are present -- the repo's convention is that a candidate prompt never
  moves the deployed one until it has been measured (the same rule
  ``evaluation/ab_baseline_citation.py`` follows for its candidates);
* the answer-shape arms must actually differ from the shipped template in
  the way they claim to.  They exist to change the *shape* of the answer, so
  a variant that silently lost its shape directive would be a no-op arm --
  which is exactly what happened to ``step2_qtype`` in the brevity
  experiment (its system prompt was byte-identical to another variant's).
"""

from __future__ import annotations

from app.rag.generation import prompt_template as pt


class TestShippedTemplateIsUnchanged:
    def test_shipped_variant_is_the_shipped_template(self):
        assert pt.TEMPLATE_VARIANTS["shipped"] == (
            pt.GROUND_QA_SYSTEM_PROMPT,
            pt.GROUND_QA_USER_TEMPLATE,
        )

    def test_shipped_user_template_keeps_the_measured_directives(self):
        tpl = pt.GROUND_QA_USER_TEMPLATE
        # The "scan every source" cue -- measured +0.076 citation recall.
        assert "go through every numbered source" in tpl
        # The citation contract.
        assert "each with its own [n] citations" in tpl
        # The ADR-0010 guardrails: no blanket refusal, no abstention escape.
        assert "DO NOT give a blanket refusal" in tpl
        assert "cannot be determined" in tpl
        # The two-step heading contract.
        assert "Step 1 — Quotes" in pt.GROUND_QA_SYSTEM_PROMPT

    def test_shipped_template_does_not_carry_the_answer_shape_directives(self):
        tpl = pt.GROUND_QA_USER_TEMPLATE
        assert "the operative words that state the rule" not in tpl
        assert "write ONE clause" not in tpl


class TestAnswerShapeVariants:
    def test_all_variants_render(self):
        for name in pt.TEMPLATE_VARIANTS:
            system, template = pt.TEMPLATE_VARIANTS[name]
            assert "{query}" in template
            assert "{context}" in template
            assert "Question: {query}" in template

    def test_each_variant_differs_from_the_shipped_template(self):
        shipped = pt.TEMPLATE_VARIANTS["shipped"]
        for name, variant in pt.TEMPLATE_VARIANTS.items():
            if name == "shipped":
                continue
            assert variant != shipped

    def test_variants_carry_the_shape_directive(self):
        for name in ("armshape", "armshape_v2"):
            system, template = pt.TEMPLATE_VARIANTS[name]
            # one short operative line per bearing source, not a block quote
            assert "at most about twenty words" in template
            # Step 2 must reuse the provision's own words, one clause per part
            assert "write ONE clause" in template
            assert "paraphrase" in template
            # and must not repeat the quotes as a block
            assert "do not repeat" in template
            assert "Step 1" in template

    def test_variants_keep_the_adversarial_guardrails(self):
        for name in ("armshape", "armshape_v2"):
            _, template = pt.TEMPLATE_VARIANTS[name]
            # the citation contract is not weakened
            assert "each with its own [n] citations" in template
            # ADR-0010: no new refusal lexicon beyond the shipped one
            assert "DO NOT give a blanket refusal" in template
            assert "say which part is uncovered" in template
            # the penalty-structure cue is untouched (measured flat, kept)
            assert "state its full structure" in template

    def test_v2_forbids_narration(self):
        # The first shape arm degenerated into a reasoning trace that ran to
        # 30,633 characters: its Step 1 instruction was meta-language, which
        # the model narrated instead of following.  v2 forbids that outright.
        _, template = pt.TEMPLATE_VARIANTS["armshape_v2"]
        assert "Do not describe your plan, your reasoning or these instructions" in template
        assert "do not write anything before the first heading" in template

    def test_v2_puts_completeness_before_the_one_clause_directive(self):
        # The single-step variant put the scope the other way round and the
        # model answered one clause where the question named three -- legally
        # correct but incomplete (coverage 0.4286 on Q007).
        _, template = pt.TEMPLATE_VARIANTS["armshape_v2"]
        assert template.index("Answer every part the question names") < template.index("write ONE clause")
