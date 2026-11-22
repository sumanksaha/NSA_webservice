"""Tests for the contrastive statutory reasoning pipeline (Milestone 2).

The pipeline is the thing the plan puts between the user and the statute, so
the ways it can get wrong matter:

* **the answer key must never leak.**  Stage 1 and Stage 2 may use the
  question and the retrieved context only.  ``gold_concepts``,
  ``gold_authorities`` and the reference conclusion are the answer key; if
  either stage reads them the experiment measures nothing.
* **the gate must reject fabrication.**  A selected span that was never
  offered, a rule that is not in the span, a citation outside the window, an
  answer that cites no source, and an answer that reports the rule is absent
  must all be rejected.
* **format slips are not fabrications.**  The adjudicator writes candidate
  ids where source numbers belong (and the reverse); those are repairable.
* **a heading is not an operative rule.**  "Punishment for obstructing or
  impersonating a Food Safety Officer." names the provision; it does not
  state it, and it wins the naive overlap test for the wrong reason.

0 LLM calls.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.experiment_g_contrastive_reasoning import (  # noqa: E402
    Span,
    _act_matches,
    _has_figure,
    _is_heading,
    _normalise,
    _repair_span_refs,
    _span_block,
    build_adjudication_prompt,
    compose_patch,
    extract_spans,
    parse_adjudication,
    select_deterministically,
    split_sentences,
    verify,
)
from evaluation.soft_token_gap import _figure_tokens  # noqa: E402

#: The answer key.  None of these may be read by Stage 1 or Stage 2.
ANSWER_KEY = ("gold_concepts", "gold_authorities", "recall_units", "relevant_units", "acceptable_conclusion")

#: The stages that must never see the answer key.
PROMPT_PATH = (
    "extract_spans",
    "_span_block",
    "build_adjudication_prompt",
    "verify",
    "parse_adjudication",
    "_repair_span_refs",
)


def _chunk(
    chunk_id: str,
    text: str,
    *,
    section: str | None = None,
    act: str = "Food Safety and Standards Act, 2006",
    title: str = "Food Safety and Standards Act 2006",
    doc_type: str = "act",
) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "section_number": section,
        "act_name": act,
        "document_title": title,
        "document_type": doc_type,
        "authority": "Parliament of India",
        "score": 1.0,
    }


class TestNoAnswerKeyLeak:
    def test_the_prompt_path_never_reads_the_answer_key(self):
        import evaluation.experiment_g_contrastive_reasoning as mod

        for name in PROMPT_PATH:
            source = inspect.getsource(getattr(mod, name))
            for key in ANSWER_KEY:
                assert key not in source, f"{name} references {key}"

    def test_the_module_only_touches_the_reference_in_the_scorer(self):
        import evaluation.experiment_g_contrastive_reasoning as mod

        source = inspect.getsource(mod)
        for key in ("gold_concepts", "gold_authorities", "recall_units", "relevant_units"):
            assert key not in source, f"the module references {key}"
        # the reference conclusion is used only to SCORE, never to build the prompt
        assert "acceptable_conclusion" in inspect.getsource(mod._score_one)
        assert "acceptable_conclusion" not in inspect.getsource(mod.extract_spans)


class TestSentenceAndTokenHelpers:
    def test_split_sentences_handles_statutory_text(self):
        text = "(1) The Food Safety Officer may inspect. (2) He may seize adulterants."
        assert len(split_sentences(text)) == 2

    def test_a_heading_is_not_an_operative_rule(self):
        assert _is_heading("Punishment for obstructing a Food Safety Officer.", has_modal=False, has_figure=False)
        assert not _is_heading("He shall be punished with a fine.", has_modal=True, has_figure=False)

    def test_a_year_is_not_a_figure(self):
        assert not _has_figure("The Bengal Diseases of Animals Act, 1944.")
        assert _has_figure("a fine which may extend to five lakh rupees")

    def test_a_spelled_out_figure_is_a_figure(self):
        # Statutory figures are routinely written out, not digitised.
        assert _has_figure("imprisonment for a term which may extend to six months")
        assert _has_figure("a fine which may extend to five lakh rupees")
        assert not _has_figure("the Food Safety Officer may inspect any place")

    def test_figure_tokens_cover_money_and_terms(self):
        toks = _figure_tokens("5 lakh rupees and six months")
        assert "5" in toks and "lakh" in toks and "rupees" in toks and "months" in toks
        assert "fine" not in toks

    def test_act_matching_ignores_generic_words(self):
        assert _act_matches("Food Safety and Standards Act, 2006", "Food Safety and Standards Act, 2006")
        assert not _act_matches("Delhi Police Act, 1978", "Food Safety and Standards Act, 2006")

    def test_normalise_collapses_whitespace_and_case(self):
        assert _normalise("  The  FOOD\nSafety ") == "the food safety"


class TestSpanExtraction:
    def _window(self):
        return [
            _chunk("c1", "Punishment for obstructing a Food Safety Officer.", section="60"),
            _chunk(
                "c2",
                "(3) The Food Safety Officer may enter and inspect any place where food is "
                "manufactured and take samples of such articles of food for analysis.",
                section="38",
            ),
            _chunk(
                "c3",
                "Any person who sells unsafe food shall be punished with a fine which may extend to five lakh rupees.",
                section="59",
            ),
        ]

    def test_chunks_of_one_provision_group_together(self):
        window = [
            _chunk("c1", "Part one of section 38.", section="38"),
            _chunk("c2", "Part two of section 38.", section="38"),
            _chunk("c3", "Section 60 text.", section="60"),
        ]
        spans = extract_spans("what does section 38 say?", window, {"c1": 1, "c2": 2, "c3": 3})
        assert len(spans) == 2
        s38 = next(s for s in spans if s.section == "38")
        assert set(s38.chunk_ids) == {"c1", "c2"}

    def test_unlabelled_chunks_stand_alone(self):
        window = [_chunk("c1", "Some unlabelled text."), _chunk("c2", "Other unlabelled text.")]
        spans = extract_spans("a question", window, {"c1": 1, "c2": 2})
        assert len(spans) == 2

    def test_a_heading_is_not_chosen_as_the_operative_rule(self):
        spans = extract_spans(
            "What is the punishment for obstructing a Food Safety Officer?",
            self._window(),
            {"c1": 1, "c2": 2, "c3": 3},
        )
        s60 = next(s for s in spans if s.section == "60")
        # The chunk holds only a heading, so there is nothing else to pick --
        # but the span says so, so the adjudicator is not misled.
        assert s60.operative_is_heading is True
        assert s60.operative_rule == "Punishment for obstructing a Food Safety Officer."

    def test_a_span_with_a_real_rule_is_not_marked_as_a_heading(self):
        spans = extract_spans(
            "What is the punishment for selling unsafe food?",
            self._window(),
            {"c1": 1, "c2": 2, "c3": 3},
        )
        s59 = next(s for s in spans if s.section == "59")
        assert s59.operative_is_heading is False
        assert "five lakh rupees" in s59.operative_excerpt

    def test_the_operative_rule_keeps_the_figure_bearing_sentences(self):
        # A penalty ladder lives in its own sentence; showing only the
        # governing sentence lost the figures.
        spans = extract_spans(
            "What is the punishment for selling unsafe food?",
            self._window(),
            {"c1": 1, "c2": 2, "c3": 3},
        )
        s59 = next(s for s in spans if s.section == "59")
        assert "five lakh rupees" in s59.operative_excerpt

    def test_a_section_number_from_another_act_does_not_earn_the_bonus(self):
        window = [
            _chunk(
                "c1",
                "Some Delhi Police Act text about section 59.",
                section="59",
                act="Delhi Police Act, 1978",
                title="Delhi Police Act",
            ),
            _chunk("c2", "The Food Safety Officer may inspect premises.", section="59"),
        ]
        spans = extract_spans(
            "Under Section 59 of the FSS Act 2006, what is the punishment?",
            window,
            {"c1": 1, "c2": 2},
        )
        other_act = next(s for s in spans if s.act == "Delhi Police Act, 1978")
        assert other_act.score_parts.get("named_section") is None
        assert other_act.score_parts.get("named_section_other_act") == -10.0

    def test_the_max_spans_cut_is_reported(self):
        window = [_chunk(f"c{i}", f"text {i}", section=str(i)) for i in range(10)]
        spans = extract_spans("a question", window, {f"c{i}": i + 1 for i in range(10)}, max_spans=3)
        assert len(spans) == 3
        assert spans[0].group_total == 10


class TestParsing:
    def test_it_parses_the_fixed_format(self):
        adj = parse_adjudication(
            "SPANS: S3, S8\n"
            "RULE: some operative words\n"
            "CONSEQUENCE: a consequence\n"
            "ANSWER: the answer [7] [11]\n"
            "CITATIONS: [7] [11]\n"
        )
        assert adj.span_ids == ["S3", "S8"]
        assert adj.rule == "some operative words"
        assert adj.consequence == "a consequence"
        assert adj.citations == [7, 11]
        assert adj.parsed

    def test_a_multiline_field_is_captured_whole(self):
        adj = parse_adjudication(
            "SPANS: S3\nRULE: first line of the rule\nsecond line of the rule\nANSWER: the answer [7]\n"
        )
        assert "second line of the rule" in adj.rule
        assert adj.answer == "the answer [7]"

    def test_citations_are_derived_from_the_answer_when_the_field_is_missing(self):
        adj = parse_adjudication("SPANS: S3\nANSWER: the answer [7] and [11]\n")
        assert adj.citations == [7, 11]

    def test_none_is_recognised(self):
        adj = parse_adjudication("SPANS: NONE\n")
        assert adj.none

    def test_a_response_that_echoes_the_candidates_does_not_parse(self):
        adj = parse_adjudication('S31: operative words: "(3) The functions ..."\n')
        assert not adj.parsed


class TestRepairs:
    def _spans(self):
        return [
            Span(
                span_id="S1",
                act="Act",
                section="1",
                document_type="act",
                document_title="Act",
                text="alpha",
                chunk_ids=["c1"],
                citation_indices=[7],
            ),
            Span(
                span_id="S2",
                act="Act",
                section="2",
                document_type="act",
                document_title="Act",
                text="beta",
                chunk_ids=["c2"],
                citation_indices=[11],
            ),
        ]

    def test_a_candidate_id_citation_becomes_a_source_number(self):
        adj = parse_adjudication("SPANS: S1\nANSWER: the rule holds [S1] and [Source: S2]\n")
        adj = _repair_span_refs(adj, self._spans())
        assert "[7]" in adj.answer
        assert "[11]" in adj.answer
        assert "S1" not in adj.answer

    def test_a_source_number_in_the_spans_field_becomes_a_candidate_id(self):
        adj = parse_adjudication("SPANS: [7]\nANSWER: the answer [7]\n")
        adj = _repair_span_refs(adj, self._spans())
        assert adj.span_ids == ["S1"]

    def test_an_unresolvable_reference_is_left_alone(self):
        adj = parse_adjudication("SPANS: S1\nANSWER: the answer [99]\n")
        adj = _repair_span_refs(adj, self._spans())
        assert "[99]" in adj.answer


class TestVerificationGate:
    def _spans(self):
        return [
            Span(
                span_id="S1",
                act="Act",
                section="1",
                document_type="act",
                document_title="Act",
                text="The officer may seize adulterants and impose a penalty.",
                chunk_ids=["c1"],
                citation_indices=[7],
            ),
        ]

    def _good(self):
        return parse_adjudication(
            "SPANS: S1\n"
            "RULE: The officer may seize adulterants and impose a penalty.\n"
            "ANSWER: The officer may seize adulterants and impose a penalty. [7]\n"
            "CITATIONS: [7]\n"
        )

    def test_a_sound_adjudication_is_accepted(self):
        ok, reasons = verify(self._good(), self._spans(), {7})
        assert ok, reasons

    def test_a_span_that_was_never_offered_is_rejected(self):
        adj = self._good()
        adj.span_ids = ["S99"]
        ok, reasons = verify(adj, self._spans(), {7})
        assert not ok
        assert any("not offered" in r for r in reasons)

    def test_a_rule_that_is_not_in_the_span_is_rejected(self):
        adj = self._good()
        adj.rule = "The minister may revoke the licence at will without notice"
        ok, reasons = verify(adj, self._spans(), {7})
        assert not ok
        assert any("verbatim" in r for r in reasons)

    def test_a_citation_outside_the_window_is_rejected(self):
        adj = self._good()
        adj.answer = "The officer may seize adulterants and impose a penalty. [99]"
        adj.citations = [99]
        ok, reasons = verify(adj, self._spans(), {7})
        assert not ok
        assert any("not in the window" in r for r in reasons)

    def test_an_answer_without_a_citation_is_rejected(self):
        adj = self._good()
        adj.answer = "The officer may seize adulterants and impose a penalty."
        adj.citations = []
        ok, reasons = verify(adj, self._spans(), {7})
        assert not ok

    def test_an_answer_that_does_not_cite_the_selected_span_is_rejected(self):
        adj = self._good()
        adj.answer = "Some other statement entirely [3]"
        adj.citations = [3]
        ok, reasons = verify(adj, self._spans(), {7})
        assert not ok

    def test_an_answer_reporting_the_rule_is_absent_is_rejected(self):
        adj = self._good()
        adj.answer = "The punishment is not detailed in the given operative words. [7]"
        ok, reasons = verify(adj, self._spans(), {7})
        assert not ok
        assert any("absent" in r for r in reasons)

    def test_ocr_noise_does_not_fail_the_verbatim_check(self):
        # The corpus is OCR-noisy; a copied clause with one stray character
        # is still a copy, not a paraphrase.
        spans = self._spans()
        adj = self._good()
        adj.rule = "The officer may seize adulterants and impose a penaltv."
        ok, reasons = verify(adj, spans, {7})
        assert ok, reasons


class TestPromptConstruction:
    def test_the_candidate_id_is_not_bracketed(self):
        # A bracketed token in this prompt means "cite this source"; a
        # bracketed candidate id invited the adjudicator to cite the id.
        span = Span(
            span_id="S1",
            act="Act",
            section="1",
            document_type="act",
            document_title="Act",
            text="alpha",
            chunk_ids=["c1"],
            citation_indices=[7],
            operative_rule="alpha",
            operative_excerpt="alpha",
        )
        block = _span_block(span)
        assert "[S1]" not in block
        assert "CANDIDATE S1" in block
        assert "[7]" in block

    def test_the_prompt_carries_the_facts_and_every_candidate(self):
        spans = [
            Span(
                span_id="S1",
                act="Act",
                section="1",
                document_type="act",
                document_title="Act",
                text="alpha",
                chunk_ids=["c1"],
                citation_indices=[7],
                operative_rule="alpha",
            ),
            Span(
                span_id="S2",
                act="Act",
                section="2",
                document_type="act",
                document_title="Act",
                text="beta",
                chunk_ids=["c2"],
                citation_indices=[11],
                operative_rule="beta",
            ),
        ]
        prompt = build_adjudication_prompt("What is the penalty?", spans)
        assert "What is the penalty?" in prompt
        assert "CANDIDATE S1" in prompt and "CANDIDATE S2" in prompt


class TestDeterministicSelection:
    """The variance fix: the model no longer picks the governing spans."""

    def _spans(self):
        return [
            Span(
                span_id=f"S{i}",
                act="Act",
                section=str(i),
                document_type="act",
                document_title="Act",
                text=f"rule {i}",
                chunk_ids=[f"c{i}"],
                citation_indices=[i],
                score=10.0 - i,
                rank=i,
            )
            for i in range(1, 6)
        ]

    def test_the_top_ranked_spans_are_taken_in_order(self):
        chosen = select_deterministically(self._spans(), keep=3)
        assert [s.span_id for s in chosen] == ["S1", "S2", "S3"]

    def test_fewer_spans_than_asked_is_fine(self):
        assert [s.span_id for s in select_deterministically(self._spans()[:1], keep=3)] == ["S1"]
        assert select_deterministically([], keep=3) == []

    def test_the_prompt_tells_the_model_the_selection_is_made(self):
        spans = self._spans()
        prompt = build_adjudication_prompt("What is the penalty?", spans, preselected=spans[:2])
        assert "ALREADY been selected" in prompt
        assert "S1, S2" in prompt
        assert "Do NOT select or reject candidates" in prompt
        # every candidate is still shown, so the composer can read the ones
        # it was handed
        assert "CANDIDATE S5" in prompt

    def test_the_model_prompt_does_not_preselect(self):
        prompt = build_adjudication_prompt("What is the penalty?", self._spans())
        assert "ALREADY been selected" not in prompt


class TestFormatLeakGate:
    def test_format_reasoning_in_the_output_is_rejected(self):
        # Two of six variance-run responses leaked the model's reasoning
        # about the output format into the answer; one even parsed as a
        # span list.
        spans = [
            Span(
                span_id="S1",
                act="Act",
                section="1",
                document_type="act",
                document_title="Act",
                text="The officer may seize adulterants.",
                chunk_ids=["c1"],
                citation_indices=[7],
            ),
        ]
        adj = parse_adjudication(
            "SPANS: S1\n"
            "RULE: The officer may seize adulterants.\n"
            "ANSWER: The officer may seize adulterants. [7]\n"
            "But note: The ids are S1,S2 (comma separated). But wait, the output "
            "should be exactly that, maybe with a newline?\n"
        )
        ok, reasons = verify(adj, spans, {7})
        assert not ok
        assert any("format" in r for r in reasons)

    def test_a_clean_output_is_not_flagged(self):
        spans = [
            Span(
                span_id="S1",
                act="Act",
                section="1",
                document_type="act",
                document_title="Act",
                text="The officer may seize adulterants.",
                chunk_ids=["c1"],
                citation_indices=[7],
            ),
        ]
        adj = parse_adjudication(
            "SPANS: S1\nRULE: The officer may seize adulterants.\nANSWER: The officer may seize adulterants. [7]\n"
        )
        ok, reasons = verify(adj, spans, {7})
        assert ok, reasons


class TestComposeModeGate:
    """The deterministic-selection arm: the model only composes."""

    def _spans(self):
        return [
            Span(
                span_id="S1",
                act="Act",
                section="1",
                document_type="act",
                document_title="Act",
                text="The officer may seize adulterants and impose a penalty.",
                chunk_ids=["c1"],
                citation_indices=[7],
            ),
            Span(
                span_id="S2",
                act="Act",
                section="2",
                document_type="act",
                document_title="Act",
                text="The Commissioner may revoke a licence.",
                chunk_ids=["c2"],
                citation_indices=[11],
            ),
        ]

    def test_citing_a_subset_of_the_preselected_spans_is_accepted(self):
        # The composer may legitimately use one of two pre-selected
        # candidates when the other does not bear on the question.
        adj = parse_adjudication(
            "SPANS: S1, S2\n"
            "RULE: whatever the composer wrote\n"
            "ANSWER: The officer may seize adulterants and impose a penalty. [7]\n"
        )
        ok, reasons = verify(adj, self._spans(), {7, 11}, compose_mode=True)
        assert ok, reasons

    def test_a_paraphrased_rule_is_not_a_compose_mode_rejection(self):
        # The composer is not claiming a verbatim quote; the anti-fabrication
        # guarantee it needs is real citations from an offered span.
        adj = parse_adjudication(
            "SPANS: S1\n"
            "RULE: The officer may take the adulterants away and punish.\n"
            "ANSWER: The officer may seize adulterants and impose a penalty. [7]\n"
        )
        ok, reasons = verify(adj, self._spans(), {7, 11}, compose_mode=True)
        assert ok, reasons

    def test_a_fabricated_citation_is_still_rejected(self):
        adj = parse_adjudication(
            "SPANS: S1\nRULE: The officer may seize adulterants.\nANSWER: The officer may seize adulterants. [99]\n"
        )
        ok, reasons = verify(adj, self._spans(), {7, 11}, compose_mode=True)
        assert not ok
        assert any("window" in r for r in reasons)

    def test_citing_no_selected_span_is_still_rejected(self):
        # A third span was offered but not selected; using only it is a
        # selection the composer was told not to make.
        third = Span(
            span_id="S3",
            act="Act",
            section="3",
            document_type="act",
            document_title="Act",
            text="The adjudicating officer hears appeals.",
            chunk_ids=["c3"],
            citation_indices=[3],
        )
        adj = parse_adjudication(
            "SPANS: S1\n"
            "RULE: The adjudicating officer hears appeals.\n"
            "ANSWER: The adjudicating officer hears appeals. [3]\n"
        )
        ok, reasons = verify(adj, self._spans() + [third], {3, 7, 11}, compose_mode=True)
        assert not ok
        assert any("cites none" in r for r in reasons)

    def test_preamble_before_the_fields_is_not_a_rejection(self):
        # The parser is field-scoped: chain-of-thought before the first
        # field header never reaches the answer, and two of six variance-run
        # outputs were recoverable exactly this way.
        adj = parse_adjudication(
            "We are given three candidate provisions. Let me think.\n"
            "SPANS: S1\n"
            "RULE: The officer may seize adulterants.\n"
            "ANSWER: The officer may seize adulterants. [7]\n"
        )
        ok, reasons = verify(adj, self._spans(), {7, 11}, compose_mode=True)
        assert ok, reasons


def _patch_span(
    span_id="S1",
    section="38",
    text="The officer may seize adulterants and impose a penalty.",
    excerpt=None,
    heading=False,
    cites=(7,),
):
    return Span(
        span_id=span_id,
        act="Food Safety and Standards Act, 2006",
        section=section,
        document_type="act",
        document_title="Food Safety and Standards Act 2006",
        text=text,
        chunk_ids=["c1"],
        citation_indices=list(cites),
        operative_rule=excerpt or text,
        operative_excerpt=excerpt or text,
        operative_is_heading=heading,
    )


class TestComposePatch:
    """The zero-variance arm: baseline answer plus verbatim clauses."""

    def test_an_uncovered_span_is_appended_verbatim_with_its_citation(self):
        span = _patch_span(excerpt="The officer may seize adulterants and impose a penalty.")
        baseline = "The question concerns licensing."
        patched, appended = compose_patch(baseline, [span], budget_pct=100, budget_floor=100)
        assert len(appended) == 1
        assert baseline in patched
        assert '"The officer may seize adulterants and impose a penalty."' in patched
        assert "Section 38 of Food Safety and Standards Act, 2006" in patched
        assert "[7]" in patched

    def test_a_covered_span_is_not_appended(self):
        span = _patch_span(excerpt="The officer may seize adulterants.")
        baseline = "The officer may seize adulterants under the Act."
        patched, appended = compose_patch(baseline, [span])
        assert appended == []
        assert patched == baseline

    def test_an_abstention_is_left_alone(self):
        span = _patch_span()
        baseline = "The provided context does not contain the answer."
        patched, appended = compose_patch(baseline, [span])
        assert appended == []
        assert patched == baseline

    def test_an_empty_baseline_is_left_alone(self):
        span = _patch_span()
        patched, appended = compose_patch("", [span])
        assert patched == "" and appended == []

    def test_the_clause_count_is_capped(self):
        spans = [
            _patch_span(
                span_id=f"S{i}",
                section=str(i),
                text=f"Distinct rule number {i} applies here.",
                excerpt=f"Distinct rule number {i} applies here.",
            )
            for i in range(1, 5)
        ]
        baseline = "A short baseline about licensing."
        patched, appended = compose_patch(baseline, spans, max_clauses=2, budget_pct=100, budget_floor=200)
        assert len(appended) == 2
        assert patched.count("Under Section") == 2

    def test_the_word_budget_is_respected(self):
        # The v2 length gate is +10%; the append is budgeted to it.  A
        # clause that does not fit is skipped, and a shorter one later in
        # the ranking may still be taken.
        long_clause = "The officer may seize adulterants and impose a penalty on the adulterant."
        spans = [
            _patch_span(span_id="S1", section="1", text=long_clause, excerpt=long_clause),
            _patch_span(
                span_id="S2",
                section="2",
                text="The Commissioner may compound.",
                excerpt="The Commissioner may compound.",
            ),
        ]
        baseline = "A short baseline."
        patched, appended = compose_patch(baseline, spans, budget_pct=10, budget_floor=15)
        # budget = max(15, 3) = 15 words; the long clause (22 words with its
        # framing) does not fit, the short one (12) does.
        assert [s.span_id for s in appended] == ["S2"]
        assert "The Commissioner may compound." in patched

    def test_budget_floor_lets_a_short_answer_get_one_clause(self):
        span = _patch_span(excerpt="The officer may seize.")
        baseline = "Short."
        patched, appended = compose_patch(baseline, [span], budget_pct=10, budget_floor=15)
        assert len(appended) == 1

    def test_a_heading_only_span_is_never_appended(self):
        # A heading names the provision; it does not state it, and a
        # appended heading would be noise.
        span = _patch_span(heading=True, excerpt="Punishment for obstructing a Food Safety Officer.")
        baseline = "A short baseline."
        patched, appended = compose_patch(baseline, [span])
        assert appended == []
        assert patched == baseline

    def test_every_appended_citation_is_the_span_s_own(self):
        span = _patch_span(excerpt="The Commissioner may compound an offence.", cites=(11,))
        baseline = "A short baseline."
        patched, appended = compose_patch(baseline, [span], budget_pct=100, budget_floor=100)
        assert appended and "[11]" in patched and "[7]" not in patched
