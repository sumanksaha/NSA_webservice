"""Coverage invariants for the canonical query-type registry.

Five vocabularies previously described the same concept with different
spellings and nothing connected them, so entries were silently dropped at each
boundary (the legacy classifier emitted ``duty`` where a directive keyed
``obligation`` was looked up; the budget table keyed ``general`` where the
runtime emitted ``general_qa``; ~20 evaluation call sites degraded unmapped
labels to ``""``).

These tests pin the invariants that make that class of bug fail CI instead of
silently degrading at runtime.  They are deliberately about *coverage and
agreement*, not about any particular classifier's accuracy -- accuracy is
pinned separately in ``TestClassifierAgainsGold``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag import query_type_registry as registry
from app.rag.generation.context_builder import ContextBuilder
from app.rag.generation.prompt_template import QUERY_TYPE_DIRECTIVES
from app.rag.retrieval.legal_query_classifier import (
    CROSS_REFERENCE_CONFIG,
    DEFAULT_CONFIG,
    LEGAL_QUERY_TYPES,
    OBLIGATION_CONFIG,
    QUERY_TYPE_CONFIGS,
    _TYPE_PATTERNS,
    classify_legal_query,
    get_config,
)
from app.rag.retrieval.query_classifier import (
    CLASSIFIER_TRIAGE,
    QueryType,
    _LABEL_TO_TYPE,
    _QUERY_PATTERNS,
    _RUNTIME_TYPE_BY_CANONICAL,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = PROJECT_ROOT / "benchmark" / "benchmark_v1.0.jsonl"


@pytest.fixture(scope="module")
def benchmark_types() -> dict[str, list[str]]:
    """{question_id: [raw question_type label, ...]}"""
    if not BENCHMARK.exists():
        pytest.skip(f"benchmark not present at {BENCHMARK}")
    out: dict[str, list[str]] = {}
    for line in BENCHMARK.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        out[rec["question_id"]] = list(rec.get("question_type") or [])
    return out


class TestRegistryInvariants:
    def test_registry_is_self_consistent(self):
        registry.assert_registry_consistent()

    def test_no_alias_is_claimed_by_two_types(self):
        """The whole point of the registry: a spelling means exactly one thing."""
        seen: dict[str, str] = {}
        for spec in registry.REGISTRY.values():
            for form in {spec.canonical, *spec.aliases}:
                key = form.strip().lower().replace("-", "_").replace(" ", "_")
                assert key not in seen or seen[key] == spec.canonical, (
                    f"{form!r} claimed by {seen.get(key)!r} and {spec.canonical!r}"
                )
                seen[key] = spec.canonical

    def test_every_canonical_resolves_to_itself(self):
        for spec in registry.REGISTRY.values():
            assert registry.resolve(spec.canonical) == spec.canonical

    def test_unknown_value_resolves_to_none(self):
        """Unmapped input must be *visible*, never silently coerced."""
        assert registry.resolve("no_such_query_type") is None
        assert registry.resolve(None) is None

    def test_every_runtime_query_type_resolves(self):
        """A ``QueryType`` the registry does not know is dropped at every
        boundary that resolves spellings (directives, budgets, eval slicing)."""
        unmapped = [q.value for q in QueryType if registry.resolve(q.value) is None]
        assert not unmapped, f"QueryType values absent from the registry: {unmapped}"

    def test_runtime_type_bridge_is_derived_from_registry(self):
        assert set(_RUNTIME_TYPE_BY_CANONICAL) == {
            registry.resolve(q.value) for q in QueryType
        }
        assert set(_RUNTIME_TYPE_BY_CANONICAL.values()) == set(QueryType)
        for canonical in _RUNTIME_TYPE_BY_CANONICAL:
            assert registry.resolve(canonical) == canonical


class TestBenchmarkLabelsAreMapped:
    """Every human-curated benchmark label must resolve. This is the invariant
    whose absence made ``ab_baseline_citation --qtype`` select an empty set."""

    def test_every_benchmark_label_resolves(self, benchmark_types):
        unmapped = {
            label
            for labels in benchmark_types.values()
            for label in labels
            if registry.resolve(label) is None
        }
        assert not unmapped, f"benchmark labels absent from the registry: {sorted(unmapped)}"

    def test_every_benchmark_question_has_at_least_one_resolvable_type(
        self, benchmark_types,
    ):
        assert benchmark_types, "benchmark parsed empty"
        for qid, labels in benchmark_types.items():
            assert registry.resolve_many(labels), f"{qid} has no resolvable question_type"

    def test_benchmark_labels_are_multi_label_in_practice(self, benchmark_types):
        """52 of 150 carry >1 type; the precedence rule must be exercised."""
        multi = sum(1 for labels in benchmark_types.values() if len(registry.resolve_many(labels)) > 1)
        assert multi > 0, "no multi-type questions -- precedence would be untested"


class TestClassifierAgreesWithRegistry:
    def test_legal_pattern_labels_are_canonical(self):
        for label, _ in _TYPE_PATTERNS:
            assert registry.resolve(label) == label, (
                f"legal classifier pattern label {label!r} is not canonical"
            )

    def test_query_classifier_labels_are_canonical(self):
        for label, _ in _QUERY_PATTERNS:
            assert registry.resolve(label) == label, (
                f"query classifier pattern label {label!r} is not canonical"
            )
            assert _LABEL_TO_TYPE[label] is not None

    def test_triage_is_registry_derived(self):
        assert set(CLASSIFIER_TRIAGE.values()) == set(QueryType)
        for form, query_type in CLASSIFIER_TRIAGE.items():
            assert registry.resolve(form) == registry.resolve(query_type.value)

    def test_configs_and_legal_types_agree(self):
        assert set(QUERY_TYPE_CONFIGS) == set(LEGAL_QUERY_TYPES)

    def test_every_target_has_a_reranker_config(self):
        for canonical in registry.CLASSIFIER_TARGETS:
            surface = registry.REGISTRY[canonical].emitted
            assert surface in QUERY_TYPE_CONFIGS, f"no reranker config for {surface!r}"

    def test_get_config_accepts_canonical_and_benchmark_spellings(self):
        assert get_config("cross_reference") is CROSS_REFERENCE_CONFIG
        assert get_config("cross-reference") is CROSS_REFERENCE_CONFIG
        assert get_config("Cross-reference") is CROSS_REFERENCE_CONFIG
        assert get_config("duty") is OBLIGATION_CONFIG
        assert get_config("no_such_type") is DEFAULT_CONFIG

    def test_emitted_surfaces_match_registry(self):
        for canonical in registry.CLASSIFIER_TARGETS:
            surface = registry.REGISTRY[canonical].emitted
            assert surface in LEGAL_QUERY_TYPES

    def test_classifier_never_emits_an_unknown_type(self, benchmark_types):
        for qid in benchmark_types:
            emitted = classify_legal_query(_question_text(qid))
            assert emitted in LEGAL_QUERY_TYPES or registry.resolve(emitted), (
                f"{qid}: classifier emitted undeclared {emitted!r}"
            )


class TestDirectiveCoverage:
    def test_directive_table_is_registry_derived(self):
        assert set(QUERY_TYPE_DIRECTIVES) == set(registry.DIRECTIVES)

    def test_every_directive_key_is_a_known_type(self):
        for key in QUERY_TYPE_DIRECTIVES:
            assert registry.resolve(key) == key, f"directive key {key!r} is not canonical"

    def test_directive_text_is_non_empty(self):
        for key, text in QUERY_TYPE_DIRECTIVES.items():
            assert text and text.strip(), f"{key} has an empty directive"

    def test_directive_bearing_types_are_resolvable_from_benchmark_labels(
        self, benchmark_types,
    ):
        """A directive nothing can ever reach is dead weight -- flag it."""
        reachable = {registry.resolve(l) for labels in benchmark_types.values() for l in labels}
        unreachable = set(registry.DIRECTIVES) - reachable
        # `definition` is known-unreachable: no benchmark question carries it.
        assert unreachable <= {"definition"}, (
            f"directives unreachable from benchmark labels: {sorted(unreachable)}"
        )


class TestBudgetTableCoverage:
    def test_budget_table_is_registry_derived(self):
        assert set(ContextBuilder._QUERY_TYPE_BUDGETS) == set(registry.BUDGETS)

    def test_fallback_type_resolves_to_its_budget(self):
        """The original table keyed ``general`` while the runtime emits
        ``general_qa``, so the fallback entry never matched."""
        b = ContextBuilder(query_type="general_qa")
        assert b.max_context_chunks == 50
        assert b.max_context_chars == 40_000

    def test_benchmark_label_spelling_resolves(self):
        b = ContextBuilder(query_type="Penalty")
        assert b.max_context_chunks == 50

    def test_unknown_type_falls_back_to_configured_ceiling(self):
        b = ContextBuilder(query_type="no_such_type")
        assert b._query_type == "no_such_type"
        assert b.max_context_chunks > 0


class TestPrecedence:
    def test_penalty_outranks_obligation(self):
        """Most-specific-answer-form first: an inexact sanction figure is the
        more damaging failure, so penalty must win over obligation."""
        assert registry.primary_type(["Obligation", "Penalty"]) == "penalty"

    def test_precedence_is_independent_of_input_order(self):
        a = registry.primary_type(["Obligation", "Procedure", "Temporal"])
        b = registry.primary_type(["Temporal", "Procedure", "Obligation"])
        assert a == b == "temporal"

    def test_resolve_many_is_deduplicated(self):
        assert registry.resolve_many(["Penalty", "penalty", "PENALTY"]) == ["penalty"]

    def test_primary_type_is_none_for_unmapped_only(self):
        assert registry.primary_type(["no_such_a", "no_such_b"]) is None


class TestClassifierAgainsGold:
    """Pin the rule-based classifier's agreement with the benchmark's human labels.

    The patterns used strict ``\\bstem\\b``, so they missed inflections of the very
    concept they were written to catch (``\\bpunish\\b`` does not match
    "punishment").  Fixing that moved agreement 38.0% -> 41.3%.

    The floor below is deliberately well under the current value: it catches
    morphological regression, it does not enshrine a score.  Note the honest
    ceiling -- ``insufficient_evidence`` and ``direct_provision`` are annotator
    judgements about answerability, not lexical properties of the question, so a
    keyword classifier cannot reach them.  Do NOT raise the floor by adding
    keywords harvested from these 150 questions: the benchmark is the only
    evaluation set available, so tuning against it would invalidate the very
    number being pinned.
    """

    FLOOR = 0.40

    def _agreement(self, benchmark_types) -> float:
        hit = sum(
            1
            for qid, labels in benchmark_types.items()
            if registry.resolve(classify_legal_query(_question_text(qid)))
            in {registry.resolve(l) for l in labels}
        )
        return hit / len(benchmark_types)

    def test_agreement_with_gold_does_not_regress(self, benchmark_types):
        rate = self._agreement(benchmark_types)
        assert rate >= self.FLOOR, (
            f"classifier agreement with gold fell to {rate:.1%} "
            f"(floor {self.FLOOR:.0%}) -- check for morphological regression"
        )

    def test_classifier_is_not_viable_as_a_treatment_source(self, benchmark_types):
        """Documents WHY E2 must use gold labels as the treatment source.

        If this ever starts failing comfortably, the oracle-routing choice can be
        revisited -- but that needs a fresh held-out set, not this one.
        """
        assert self._agreement(benchmark_types) < 0.60, (
            "classifier agreement crossed 60%; it may now serve as the treatment "
            "source, but that must be re-decided against a held-out set"
        )

    def test_ambiguous_fallback_is_bounded(self, benchmark_types):
        """A classifier that answers 'ambiguous' for a third of the corpus is
        barely classifying.  The morphological fix took this 66/150 -> 50/150."""
        n_amb = sum(
            1 for qid in benchmark_types
            if classify_legal_query(_question_text(qid)) == "ambiguous"
        )
        assert n_amb < len(benchmark_types) * 0.45, (
            f"{n_amb}/{len(benchmark_types)} classified as ambiguous"
        )


# --- helpers -----------------------------------------------------------------

_QUESTIONS: dict[str, str] = {}


def _question_text(qid: str) -> str:
    if not _QUESTIONS:
        for line in BENCHMARK.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                _QUESTIONS[rec["question_id"]] = rec["question"]
    return _QUESTIONS[qid]