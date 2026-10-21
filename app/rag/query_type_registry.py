"""Canonical query-type registry -- the single source of truth.

Why this module exists
----------------------
Five independent vocabularies in this codebase described the *same* concept with
different spellings, and nothing connected them:

1. ``legal_query_classifier.LEGAL_QUERY_TYPES`` / ``QUERY_TYPE_CONFIGS`` --
   14 types keyed with natural-language spacing: ``"direct provision"``,
   ``"cross-reference"``, ``"insufficient-evidence"``.
2. ``prompt_template.QUERY_TYPE_DIRECTIVES`` -- 9 types keyed ``snake_case``:
   ``"obligation"``, ``"offence"``.
3. ``query_classifier.QueryType`` (StrEnum) -- 24 routing values, including
   ``duty`` and the legacy ``general_qa``/``section_lookup`` family.
4. ``context_builder._QUERY_TYPE_BUDGETS`` -- 7 keys drawn from (3).
5. ``benchmark_v1.0.jsonl`` ``question_type`` -- the only *human-curated* set,
   keyed ``Title-Case`` with spacing: ``"Obligation"``, ``"Cross-reference"``.

Consumers bridged the gap with ad-hoc ``.lower()`` / ``replace("-", "_")``
surgery.  That silently dropped entries at each boundary: the legacy classifier
emitted ``duty`` where a directive keyed ``obligation`` was looked up, the
budget table keyed ``general`` where the runtime emitted ``general_qa``, and
~20 evaluation call sites degraded unmapped labels to ``""``.

Design
------
* The **canonical id** is ``snake_case`` and is the only spelling new code
  should write.
* Every legacy spelling is registered as an explicit **alias** on the canonical
  type it belongs to.  Aliases are *not* separate types -- ``duty`` and
  ``obligation`` are one concept with two surface forms.
* Collision between aliases is a hard ``AssertionError`` at import time, so a
  new type cannot silently steal an existing alias.
* ``PRECEDENCE`` fixes the order for resolving multi-label questions
  (52 of the 150 benchmark questions carry more than one type).  It is
  most-specific-answer-form-first and is pre-registered: it must not depend on
  question order or dict ordering.

This module deliberately imports nothing from ``app`` so it can be depended on
by both the retrieval and generation layers without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "BUDGETS",
    "CANONICAL_QUERY_TYPES",
    "CLASSIFIER_TARGETS",
    "DIRECTIVES",
    "PRECEDENCE",
    "REGISTRY",
    "QueryTypeSpec",
    "assert_registry_consistent",
    "budget_for",
    "has_directive",
    "primary_type",
    "resolve",
    "resolve_many",
]


@dataclass(frozen=True, slots=True)
class QueryTypeSpec:
    """One canonical query type.

    Attributes:
        canonical: snake_case id -- the only spelling new code should emit.
        directive: prompt directive appended before ``Question:`` when
            ``RAG_QUERY_TYPE_DIRECTIVES`` is on, or ``None`` when the type has
            no directive (e.g. ``ambiguous``, ``insufficient_evidence``).
        budget: ``(max_chunks, max_context_chars)`` window cap, or ``None``.
        aliases: every accepted surface form -- benchmark labels, legacy
            classifier labels, ``QueryType`` enum values.
        classifier_target: whether ``classify_legal_query`` is expected to be
            able to emit this type.  ``False`` means "benchmark-only" (an
            annotator category with no production analogue) and is asserted
            against by the registry test.
    """

    canonical: str
    directive: str | None = None
    budget: tuple[int, int] | None = None
    aliases: frozenset[str] = frozenset()
    classifier_target: bool = True
    #: Spelling emitted by ``classify_legal_query``.  Defaults to the
    #: canonical id; set only where the shipped classifier emits something
    #: different.  This is the *declared* irregularity, so it can no longer
    #: drift -- it is recorded here rather than being an accident of string
    #: literals in three places.
    surface: str | None = None

    @property
    def has_directive(self) -> bool:
        return self.directive is not None

    @property
    def emitted(self) -> str:
        """The exact string ``classify_legal_query`` returns for this type."""
        return self.surface or self.canonical


#: Window cap applied to query types that declare one.  Uniform across every
#: entry that declared a budget before the registry existed, so encoding it once
#: here is behaviour-preserving rather than a tuning change.
_DEFAULT_BUDGET: Final[tuple[int, int]] = (50, 40_000)

# Order is meaningful: most-specific-answer-form first.  A question tagged both
# ``obligation`` and ``penalty`` is best served by the penalty directive, because
# an inexact sanction figure is the more damaging failure.  Do not reorder
# without re-running the precedence regression test.
_SPECS: Final[tuple[QueryTypeSpec, ...]] = (
    QueryTypeSpec(
        canonical="penalty",
        directive=(
            "For this penalty question, state the full structure of the sanction — "
            "base fine and/or imprisonment, any continuing daily fine, and any "
            "escalation on repeat conviction — with exact figures as printed in "
            "the source, never a rounded summary."
        ),
        budget=_DEFAULT_BUDGET,
        aliases=frozenset({"penalty", "penalties", "Penalty"}),
    ),
    QueryTypeSpec(
        canonical="offence",
        directive=(
            "For this offence question, identify the actus reus elements the "
            "source states, all of them, and the authority that takes cognizance."
        ),
        aliases=frozenset({"offence", "offense", "Offence"}),
    ),
    QueryTypeSpec(
        canonical="prohibition",
        directive=(
            "For this prohibition question, restate every element the source "
            "joins with 'and' — dropping one changes the offence — and do not "
            "soften the prohibition into a permission."
        ),
        budget=_DEFAULT_BUDGET,
        aliases=frozenset({"prohibition", "Prohibition"}),
    ),
    QueryTypeSpec(
        canonical="exception",
        directive=(
            "For this exemption question, quote the exception, proviso or "
            "non-applicability clause itself, with its own [n] citation, before "
            "stating what the general rule provides."
        ),
        # "exemption" is the benchmark annotator's spelling of this concept.
        aliases=frozenset({"exception", "exemption", "Exception", "Exemption"}),
    ),
    QueryTypeSpec(
        canonical="enforcement",
        directive=(
            "For this enforcement question, name the enforcing authority and "
            "the specific power it exercises under the cited provision."
        ),
        aliases=frozenset({"enforcement", "Enforcement"}),
    ),
    QueryTypeSpec(
        canonical="temporal",
        directive=(
            "For this temporal question, state which version of the provision "
            "the question asks about and how the versions differ."
        ),
        aliases=frozenset({"temporal", "Temporal"}),
    ),
    QueryTypeSpec(
        canonical="cross_reference",
        budget=_DEFAULT_BUDGET,
        aliases=frozenset({"cross_reference", "cross-reference", "Cross-reference"}),
        # Shipped spelling is hyphenated and is stored in the frozen
        # experiment_B_ce_ranking.json; do not "tidy" it without a migration.
        surface="cross-reference",
    ),
    QueryTypeSpec(
        canonical="definition",
        directive=(
            "For this definition question, answer from the operative provision "
            "that governs, not from a general definition: name the operative "
            "clause and say how the definition bears on it."
        ),
        budget=_DEFAULT_BUDGET,
        aliases=frozenset({"definition", "definitions", "Definition"}),
    ),
    QueryTypeSpec(
        canonical="procedure",
        directive=(
            "For this procedure question, state the designated authority, any "
            "statutory deadline in days, and the statutory Form by name."
        ),
        budget=_DEFAULT_BUDGET,
        aliases=frozenset({"procedure", "procedural", "Procedure"}),
    ),
    QueryTypeSpec(
        canonical="obligation",
        directive=(
            "For this obligation question, restate the duty as written, with "
            "every cumulative requirement, and identify who must discharge it."
        ),
        # ``duty`` is the legacy QueryType enum value the rule-based classifier
        # emits for this concept; it is an alias, NOT a separate type.
        aliases=frozenset({"obligation", "Obligation", "duty", "Duty"}),
    ),
    QueryTypeSpec(
        canonical="authority",
        aliases=frozenset({"authority", "Authority"}),
    ),
    QueryTypeSpec(
        canonical="direct_provision",
        aliases=frozenset({"direct_provision", "direct provision", "Direct provision"}),
        surface="direct provision",
    ),
    QueryTypeSpec(
        canonical="enforceable_right",
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"enforceable_right", "enforceable-right", "Enforceable-right"}),
    ),
    QueryTypeSpec(
        canonical="insufficient_evidence",
        aliases=frozenset({"insufficient_evidence", "insufficient-evidence", "Insufficient-evidence"}),
        surface="insufficient-evidence",
    ),
    QueryTypeSpec(
        canonical="ambiguous",
        aliases=frozenset({"ambiguous", "Ambiguous"}),
    ),
    # --- runtime-only routing types (no benchmark label, no directive) -------
    QueryTypeSpec(
        canonical="case_law",
        budget=_DEFAULT_BUDGET,
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"case_law", "case-law"}),
    ),
    QueryTypeSpec(
        canonical="amendment",
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"amendment", "amendment_query", "Amendment"}),
    ),
    QueryTypeSpec(
        canonical="section_lookup",
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"section_lookup", "section"}),
    ),
    QueryTypeSpec(
        canonical="provision_search",
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"provision_search", "provision"}),
    ),
    QueryTypeSpec(
        canonical="general_qa",
        budget=_DEFAULT_BUDGET,
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"general_qa", "general", "general-qa"}),
    ),
    # `right` is a QueryType enum member with no directive and no benchmark
    # analogue; kept as an alias so legacy classifier output still resolves.
    QueryTypeSpec(canonical="right", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"right"})),
    QueryTypeSpec(canonical="power", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"power"})),
    QueryTypeSpec(canonical="lookup", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"lookup"})),
    QueryTypeSpec(canonical="identification", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"identification"})),
    QueryTypeSpec(canonical="applicability", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"applicability"})),
    QueryTypeSpec(canonical="jurisdiction", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"jurisdiction"})),
    QueryTypeSpec(canonical="multi_hop", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"multi_hop"})),
    QueryTypeSpec(canonical="fact_pattern", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"fact_pattern"})),
    QueryTypeSpec(canonical="comparison", classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"comparison"})),
    QueryTypeSpec(
        canonical="compliance_assessment",
        classifier_target=False,  # runtime-only routing type
        aliases=frozenset({"compliance_assessment"}),
    ),
)

#: Precedence order used to pick ONE directive for a multi-label question.
PRECEDENCE: Final[tuple[str, ...]] = tuple(s.canonical for s in _SPECS)
_RANK: Final[dict[str, int]] = {c: i for i, c in enumerate(PRECEDENCE)}

REGISTRY: Final[dict[str, QueryTypeSpec]] = {s.canonical: s for s in _SPECS}

#: Every canonical id, in :data:`PRECEDENCE` order.
CANONICAL_QUERY_TYPES: Final[frozenset[str]] = frozenset(REGISTRY)

#: canonical id -> directive text.  Replaces the hand-maintained
#: ``prompt_template.QUERY_TYPE_DIRECTIVES`` table.
DIRECTIVES: Final[dict[str, str]] = {
    s.canonical: s.directive for s in _SPECS if s.directive is not None
}

#: canonical id -> (max_chunks, max_context_chars)
BUDGETS: Final[dict[str, tuple[int, int]]] = {
    s.canonical: s.budget for s in _SPECS if s.budget is not None
}

#: Types ``classify_legal_query`` is expected to emit (the 14 legal types plus
#: ``ambiguous``).  Benchmark-only annotator categories are excluded.
CLASSIFIER_TARGETS: Final[frozenset[str]] = frozenset(
    s.canonical for s in _SPECS if s.classifier_target
)


def _build_alias_index() -> dict[str, str]:
    """surface form -> canonical id, asserting no alias is claimed twice."""
    index: dict[str, str] = {}
    for spec in _SPECS:
        forms = {spec.canonical, *spec.aliases}
        for form in forms:
            key = form.strip().lower().replace("-", "_").replace(" ", "_")
            owner = index.get(key)
            assert owner is None or owner == spec.canonical, (
                f"alias {form!r} is claimed by both {owner!r} and "
                f"{spec.canonical!r}; aliases must be unambiguous"
            )
            index[key] = spec.canonical
    return index


_ALIAS_INDEX: Final[dict[str, str]] = _build_alias_index()


def resolve(value: object) -> str | None:
    """Resolve any known surface form to its canonical id, else ``None``.

    Accepts every alias above plus benchmark ``Title-Case`` labels and
    ``snake_case`` / spaced / hyphenated spellings.
    """
    if value is None:
        return None
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIAS_INDEX.get(key)


def resolve_many(values: Iterable[object]) -> list[str]:
    """Resolve an iterable of surface forms to canonical ids, deduplicated and
    returned in :data:`PRECEDENCE` order.  Unresolvable values are dropped."""
    seen = {c for c in (resolve(v) for v in values) if c is not None}
    return [c for c in PRECEDENCE if c in seen]


def primary_type(values: Iterable[object]) -> str | None:
    """Pick the single highest-precedence canonical id, or ``None`` if none."""
    resolved = resolve_many(values)
    return resolved[0] if resolved else None


def has_directive(canonical: str) -> bool:
    spec = REGISTRY.get(canonical)
    return bool(spec and spec.has_directive)


def budget_for(canonical: str) -> tuple[int, int] | None:
    return BUDGETS.get(canonical)


def assert_registry_consistent() -> None:
    """Re-run the import-time invariants.  Exposed so tests can call it
    explicitly and so a caller that mutates a registry table fails loudly."""
    # 1. alias uniqueness (rebuild rather than trust the cached index)
    fresh = _build_alias_index()
    assert fresh == _ALIAS_INDEX, "alias index drifted from _SPECS"
    # 2. every canonical is in the precedence order, exactly once
    assert set(_RANK) == set(REGISTRY), "PRECEDENCE and REGISTRY disagree"
    assert len(_RANK) == len(PRECEDENCE), "PRECEDENCE contains duplicates"
    # 3. no canonical is a prefix-shadow of another in the alias index
    for spec in _SPECS:
        assert spec.canonical == spec.canonical.strip().lower(), (
            f"canonical {spec.canonical!r} must be lowercase snake_case"
        )
    # 4. every canonical resolves to itself
    for spec in _SPECS:
        assert resolve(spec.canonical) == spec.canonical, (
            f"{spec.canonical!r} does not resolve to itself"
        )