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

import json
from pathlib import Path

import pytest

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
            assert "never fabricate" in prompt.lower(), f"domain={domain} missing no-fabrication rule"

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

        Pinned to ``cand_scan_cond`` since the conditioning block landed
        (2026-10-10): the pin moved deliberately, after the scan line was
        kept verbatim inside it, so the A/B control (``cand_scan``) stays
        registered and unchanged. A future move must repeat this: keep
        cand_scan, register the new candidate, re-point this pin.
        """
        from evaluation.ab_baseline_citation import _CANDIDATES

        assert _CANDIDATES["cand_scan_cond"] == GROUND_QA_USER_TEMPLATE
        assert "cand_scan" in _CANDIDATES, "A/B control candidate must stay registered"


class TestScanLineSurvivesConditioning:
    """The conditioning block is additive: the SPEC-1 scan line survives verbatim."""

    def test_conditioned_template_contains_the_scan_line(self):
        assert "Before answering, go through every numbered source in the context" in GROUND_QA_USER_TEMPLATE
        assert "condition, exception, threshold or definition the answer depends on" in GROUND_QA_USER_TEMPLATE
        assert "First quote the passages that bear on the question" in GROUND_QA_USER_TEMPLATE

    def test_conditioned_block_targets_the_audited_causes(self):
        lowered = GROUND_QA_USER_TEMPLATE.lower()
        # abstention_gate slice: answer every part, check before refusing.
        assert "answer every part the question names" in lowered
        assert "point to the numbered sources you checked" in lowered
        # provision_check slice: provision-by-claim identification.
        assert "name the exact provision you rely on" in lowered
        # penalty incompleteness: full penalty structure, exact figures.
        assert "continuing daily fine" in lowered
        assert "exact figures as printed" in lowered
        # needs_deeper_reasoning slice: truncation honesty, no false completeness.
        assert "cut off mid-clause" in lowered
        assert "do not call a list complete" in lowered

    def test_conditioning_block_readds_no_gate(self):
        banned = (
            "at least one verbatim",
            "you must follow these steps",
            "zero relevant passages",
            "produced zero quotes",
            "cannot find the answer",
        )
        lowered = GROUND_QA_USER_TEMPLATE.lower()
        for phrase in banned:
            assert phrase not in lowered, f"conditioning block re-introduced banned gate {phrase!r}"


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


class TestShippedVariantRender:
    """``--variant shipped`` must be exactly what production ships.

    W4 (query-type directives) is measured through this harness, so the
    shipped render has to pass ``query_type`` through: with
    ``RAG_QUERY_TYPE_DIRECTIVES`` on, every qid gets the directive its own
    classified type maps to, and qids of un-directived types stay byte-identical
    (their own parallel control).  Frozen candidates must ignore ``query_type``
    — their wording is the thing under test, so it cannot be edited by a flag.
    """

    @staticmethod
    def _svc_and_context():
        from app.rag.generation.context_builder import BuiltContext
        from app.rag.generation.grounded_service import GroundedGenerationService

        return GroundedGenerationService(), BuiltContext(
            context="[1] Section 31 of the FSS Act says X.",
            citations=[],
            chunk_count=1,
        )

    def test_shipped_renders_the_type_directive_when_flagged(self, monkeypatch):
        from app.rag.generation.prompt_template import QUERY_TYPE_DIRECTIVES
        from evaluation.ab_baseline_citation import render_prompt_for

        monkeypatch.setenv("RAG_QUERY_TYPE_DIRECTIVES", "true")
        svc, built = self._svc_and_context()
        _, user = render_prompt_for(svc, None, "what is the penalty?", built, "penalty")

        directive = QUERY_TYPE_DIRECTIVES["penalty"]
        assert directive in user, "shipped variant dropped the W4 directive"
        # Inserted as an instruction, not as trailing content after "Answer:".
        assert user.index(directive) < user.index("Question:")

    def test_shipped_is_byte_identical_when_the_flag_is_off(self, monkeypatch):
        from evaluation.ab_baseline_citation import render_prompt_for

        monkeypatch.delenv("RAG_QUERY_TYPE_DIRECTIVES", raising=False)
        svc, built = self._svc_and_context()

        _, default_render = render_prompt_for(svc, None, "q", built, "")
        _, with_type = render_prompt_for(svc, None, "q", built, "penalty")

        assert with_type == default_render, "query_type changed the prompt while the flag is off"

    def test_shipped_leaves_un_directived_types_alone(self, monkeypatch):
        from app.rag.generation.prompt_template import QUERY_TYPE_DIRECTIVES
        from evaluation.ab_baseline_citation import render_prompt_for

        monkeypatch.setenv("RAG_QUERY_TYPE_DIRECTIVES", "true")
        svc, built = self._svc_and_context()

        _, un_directived = render_prompt_for(svc, None, "q", built, "authority")
        for text in QUERY_TYPE_DIRECTIVES.values():
            assert text not in un_directived

    def test_frozen_candidate_ignores_query_type(self, monkeypatch):
        from evaluation.ab_baseline_citation import LEGACY_SYSTEM_PROMPT, VARIANTS, render_prompt_for

        monkeypatch.setenv("RAG_QUERY_TYPE_DIRECTIVES", "true")
        svc, built = self._svc_and_context()
        system, user = render_prompt_for(svc, VARIANTS["cand_cond_penalty"][1], "q", built, "obligation")

        assert system == LEGACY_SYSTEM_PROMPT
        assert user == VARIANTS["cand_cond_penalty"][1].format(context=built.context, query="q")


class TestResume:
    """``--tag`` doubles as a resume token: a 429 mid-run must not cost the arm.

    The free tier fails in bursts, so re-invoking the identical command has to
    top up only the missing qids. The guard matters more than the cache: a tag
    written under a different flag set is one arm mixing with another, so it
    must refuse rather than resume.
    """

    @staticmethod
    def _write(tmp_path, payload: dict) -> Path:
        out = Path(tmp_path) / "baseline_citation_shipped_w4_treat.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        return out

    @staticmethod
    def _flags(**over):
        from evaluation.ab_baseline_citation import _run_flags

        flags = _run_flags()
        flags.update(over)
        return flags

    def test_missing_file_resumes_nothing(self, tmp_path):
        from evaluation.ab_baseline_citation import load_prior

        out = Path(tmp_path) / "nope.json"
        assert load_prior(out, self._flags()) == {}

    def test_ok_rows_are_reused_by_qid(self, tmp_path):
        from evaluation.ab_baseline_citation import load_prior

        flags = self._flags()
        out = self._write(
            tmp_path,
            {
                "flags": flags,
                "records": [
                    {"qid": "Q007", "answer": "a", "m": {"citation_recall": 1.0}},
                    {"qid": "Q040", "answer": "", "m": {"citation_recall": 0.0}},
                ],
            },
        )
        prior = load_prior(out, flags)

        # A blank answer is a failed generation: it must be re-run, not reused.
        assert set(prior) == {"Q007"}
        assert prior["Q007"]["m"]["citation_recall"] == 1.0

    def test_rows_without_records_key_yield_nothing(self, tmp_path):
        from evaluation.ab_baseline_citation import load_prior

        flags = self._flags()
        out = self._write(tmp_path, {"variant": "shipped", "per_question": {"Q007": {}}})
        assert load_prior(out, flags) == {}

    def test_flag_mismatch_refuses_to_mix_arms(self, tmp_path):
        from evaluation.ab_baseline_citation import load_prior

        out = self._write(tmp_path, {"flags": self._flags(RAG_QUERY_TYPE_DIRECTIVES="true")})
        with pytest.raises(SystemExit, match="fresh --tag"):
            load_prior(out, self._flags(RAG_QUERY_TYPE_DIRECTIVES=""))

    def test_corrupt_output_resumes_nothing(self, tmp_path):
        from evaluation.ab_baseline_citation import load_prior

        out = Path(tmp_path) / "baseline_citation_shipped.json"
        out.write_text("{not json", encoding="utf-8")
        assert load_prior(out, self._flags()) == {}

    def test_arm_identity_covers_both_w3_and_w4_flags(self):
        from evaluation.ab_baseline_citation import _run_flags

        flags = _run_flags()
        assert "RAG_QUERY_TYPE_DIRECTIVES" in flags
        assert "ENABLE_STRUCTURED_REASONER" in flags
        # Model is part of the arm too: same tag on a different model is a
        # different experiment.
        assert "RAG_LLM_MODEL" in flags


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
