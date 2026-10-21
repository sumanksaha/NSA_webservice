"""Legal query-type classifier & query-type-aware reranker configuration.

This module implements the empirical query-type routing strategy described in
``CE_RERANK_REVIEW.md`` Sections 7-10: different legal query types respond to
different reranker configurations, and a single global configuration is
suboptimal.

The classifier is **rule-based** (no LLM) for determinism and zero latency
overhead.  It maps user queries to one of the legal query types declared in
:mod:`app.rag.query_type_registry`:

    penalty, direct_provision, definition, exception, obligation, procedure,
    authority, prohibition, cross_reference, offence, enforcement,
    insufficient_evidence, temporal, ambiguous

Patterns are keyed by the registry's canonical ids; the returned strings are
each type's declared ``surface`` (for example ``direct provision`` and
``cross-reference``), so frozen evaluation artifacts keep their spelling while
the vocabulary itself stays canonical.

The ``QueryTypeConfig`` dataclass holds per-type reranker parameters:
CE weight, CE head, hierarchy weight, and a ``feature_weight`` multiplier
for the sec_act features.  This lets each query type tune its own balance.

Key findings from k500 analysis that drove the configuration:

* Penalty: hierarchy works very well (77.8% R@10) — keep config as-is.
* Prohibition: hierarchy HURTS (62.5% -> 58.3%) — reduce hierarchy weight,
  increase CE influence.
* Cross-reference: CE alone is insufficient (16.7%) — needs graph/identifier
  recovery (handled in the retrieval pipeline, not the reranker).
* Authority: mixed — some queries have gold at pool_rank=0 but fail unit match;
  others have gold at rank 35+ and need better section targeting.
* Offence: 0% R@10 — needs identifier route recovery (see STEP 4/6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag import query_type_registry as _registry

# ---------------------------------------------------------------------------
# Legal query types — aligned with the evaluation framework taxonomy
# ---------------------------------------------------------------------------

#: Canonical set of legal query types used throughout the system.
#:
#: Derived from :mod:`app.rag.query_type_registry`, which owns the vocabulary
#: and every alias.  The emitted spellings below are the ones already stored in
#: frozen artifacts (notably ``ceiling_v5/experiment_B_ce_ranking.json``), so
#: they are declared as `surface` in the registry rather than normalised to
#: snake_case here.  Changing them is an artifact-breaking migration, not a
#: cleanup -- see the registry docstring.
LEGAL_QUERY_TYPES: frozenset[str] = frozenset(
    _registry.REGISTRY[c].emitted for c in _registry.CLASSIFIER_TARGETS
)


@dataclass(frozen=True)
class QueryTypeConfig:
    """Per-query-type reranker configuration.

    Attributes:
        ce_weight: Weight applied to min-max-normalized CE scores.
        ce_head: Number of top chunks to score with CE.
        hierarchy_weight: Weight for the hierarchy-level boost (section/sub
            vs. chapter/document root).
        feature_weight: Multiplier for sec_act feature scores (sec_match,
            act_match, exact).  1.0 = standard; < 1.0 = de-emphasize
            features for this type.
        skip_ce: If True, never run CE for this query type (pure sec_act).
        min_ce_score: Minimum CE score (post-normalization) threshold for a
            chunk to receive a CE bonus.  Chunks below this get 0 bonus.

    """

    ce_weight: float = 0.5
    ce_head: int = 30
    hierarchy_weight: float = 0.2
    feature_weight: float = 1.0
    skip_ce: bool = False
    min_ce_score: float = 0.0
    min_ident_confidence: float = 0.0


# ---------------------------------------------------------------------------
# Per-type configurations — derived from k500 empirical analysis
# ---------------------------------------------------------------------------

#: Default/global configuration (matches current production).
DEFAULT_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
    skip_ce=False,
    min_ce_score=0.0,
)

#: Penalty: hierarchy works well — keep standard config.
#: k500: 44.4% -> 77.8% (R@10), the strongest improvement class.
PENALTY_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Prohibition: hierarchy HURTS — reduce hierarchy weight, increase CE.
#: k500: 62.5% -> 58.3% (regression).  Root cause: hierarchy boost pushes
#: chapter-level chunks (which mention "prohibit" broadly) over specific
#: prohibition subsections.  Fix: zero hierarchy, more CE weight.
PROHIBITION_CONFIG = QueryTypeConfig(
    ce_weight=0.6,
    ce_head=30,
    hierarchy_weight=0.0,
    feature_weight=1.0,
)

#: Authority: gold often at pool_rank=0 but unit match fails, OR gold at
#: rank 35+ and needs section targeting.  Increase feature weight for act
#: match (authorities are Act-specific), keep CE for text-level relevance.
AUTHORITY_CONFIG = QueryTypeConfig(
    ce_weight=0.4,
    ce_head=40,
    hierarchy_weight=0.1,
    feature_weight=1.2,
)

#: Direct provision: hierarchy works well — standard config.
DIRECT_PROVISION_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Definition: start at standard weights (no k500 signal yet — tune from
#: the benchmark slice per the universal-multihop plan).  Registered so
#: ``get_config("definition")`` no longer silently falls back to DEFAULT.
DEFINITION_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Exception: hierarchy works well — standard config.
EXCEPTION_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Obligation: moderate improvement — keep standard, slightly more CE.
OBLIGATION_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Procedure: moderate improvement — standard config.
PROCEDURE_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Cross-reference: CE alone is insufficient (16.7%).  Needs identifier/graph
#: recovery.  Here, rely on features + CE with higher head for more coverage.
CROSS_REFERENCE_CONFIG = QueryTypeConfig(
    ce_weight=0.4,
    ce_head=40,
    hierarchy_weight=0.0,
    feature_weight=0.8,
)

#: Offence: 0% R@10 — CE can't fix a retrieval problem.  But where the gold
#: IS in the pool, pure sec_act + CE may help.  Keep standard for now; the
#: real fix is the identifier route (STEP 4/6).
OFFENCE_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Enforcement: no improvement from ensemble (31.7% -> 31.7%).  Features
#: may be missing the right signals.  Try higher feature weight + CE.
ENFORCEMENT_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.1,
)

#: Insufficient-evidence: moderate improvement.  Standard config.
INSUFFICIENT_EVIDENCE_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Temporal: already 100%.  Any config works.
TEMPORAL_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.2,
    feature_weight=1.0,
)

#: Ambiguous: mixed types.  Conservative — less hierarchy, more CE.
AMBIGUOUS_CONFIG = QueryTypeConfig(
    ce_weight=0.5,
    ce_head=30,
    hierarchy_weight=0.1,
    feature_weight=0.8,
)

#: Canonical id -> reranker config.  Keys are canonical so the registry owns
#: the spelling; the public ``QUERY_TYPE_CONFIGS`` table below projects them
#: through each type's declared emitted surface.
_CONFIGS_BY_CANONICAL: dict[str, QueryTypeConfig] = {
    "penalty": PENALTY_CONFIG,
    "direct_provision": DIRECT_PROVISION_CONFIG,
    "definition": DEFINITION_CONFIG,
    "exception": EXCEPTION_CONFIG,
    "obligation": OBLIGATION_CONFIG,
    "procedure": PROCEDURE_CONFIG,
    "authority": AUTHORITY_CONFIG,
    "prohibition": PROHIBITION_CONFIG,
    "cross_reference": CROSS_REFERENCE_CONFIG,
    "offence": OFFENCE_CONFIG,
    "enforcement": ENFORCEMENT_CONFIG,
    "insufficient_evidence": INSUFFICIENT_EVIDENCE_CONFIG,
    "temporal": TEMPORAL_CONFIG,
    "ambiguous": AMBIGUOUS_CONFIG,
}

assert set(_CONFIGS_BY_CANONICAL) == _registry.CLASSIFIER_TARGETS, (
    "reranker configs must cover exactly the registry's classifier targets"
)

#: Master mapping: emitted query-type surface -> QueryTypeConfig
QUERY_TYPE_CONFIGS: dict[str, QueryTypeConfig] = {
    _registry.REGISTRY[canonical].emitted: config
    for canonical, config in _CONFIGS_BY_CANONICAL.items()
}


def get_config(query_type: str) -> QueryTypeConfig:
    """Get the reranker configuration for a query type (falls back to DEFAULT).

    Both canonical ids (``cross_reference``) and the emitted /
    benchmark spellings (``cross-reference``, ``Cross-reference``) resolve
    through the registry, so callers do not need to normalise first.
    """
    canonical = _registry.resolve(query_type)
    if canonical is None:
        return DEFAULT_CONFIG
    surface = _registry.REGISTRY[canonical].emitted
    return QUERY_TYPE_CONFIGS.get(surface, DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Legal query-type classifier (rule-based, deterministic)
# ---------------------------------------------------------------------------

#: Keyword patterns for each legal query type.
#: Order matters: more specific types are checked first.
_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    # penalty — monetary/term punishment, fine, imprisonment, penalty amounts.
    # Stems are prefix-matched (no trailing \b) so inflections are caught:
    # penalt|y|ies, punish|ment|ed|able, imprison|ment.
    (
        "penalty",
        [
            r"\bpenalt",
            r"\bimprison",
            r"\bpunish",
            # "fine" is left STRICT: a prefix would match "finger",
            # "refine", "finesse".  Use the phrase forms instead.
            r"\bfine\b",
            r"\bfined\b",
            r"\brs\.?\s*\d+[,\d]*\s*(?:per|for|each|or)\b",
            r"\bmonetary\s+penalt",
            r"\bcost\s+of\s+violation\b",
        ],
    ),
    # offence — criminal liability, commission of offence, prosecution
    (
        "offence",
        [
            r"\boffence",
            r"\bprosecut",
            r"\bcriminal\s+liability\b",
            r"\bguilty\b",
            r"\bconvict",
            r"\bliable\s+to\s+be\s+punish",
        ],
    ),
    # prohibition — prohibition orders, what is prohibited, restrictions
    (
        "prohibition",
        [
            r"\bprohibit",
            r"\brestrict",
            r"\bshall\s+not\b",
            r"\bmay\s+not\b",
            r"\bmust\s+not\b",
            r"\bno\s+(?:person|food\s*business|premises|establishment)",
            r"\bprohibition\s+order\b",
        ],
    ),
    # exception — carve-outs, exemptions, exceptions to provisions
    (
        "exception",
        [
            r"\bexcept",
            r"\bexempt",
            r"\bnot\s+applicable\b",
            r"\bdoesn'?t\s+apply\b",
            r"\bsaving\b",
            r"\bsubject\s+to\b",
            r"\bnotwithstanding\b",
            r"\bnon[- ]applicab",
        ],
    ),
    # authority — officers, boards, agencies, who has power.
    # Placed BEFORE enforcement so that queries mentioning both an authority
    # keyword (officer, tribunal, fso) and an enforcement action keyword
    # (inspect, sample) keep the authority tie — the actor wins over the
    # action when both are present.  ``tribunal`` is duplicated in the
    # procedure list; on a tie the earlier (authority) entry wins.
    (
        "authority",
        [
            r"\bofficer",
            r"\bauth\b",
            r"\bauthorit",
            r"\bboard\b",
            r"\bagenc",
            r"\bcommission\b",
            r"\bfso\b",
            r"\bfsoi\b",
            r"\bwho\s+is\b.*\borgan",
            r"\bempowered\b",
            r"\bprincipal\s+enforc",
            r"\bfood\s+analyst\b",
            r"\bappellate\b",
            r"\bjurisdicti",
            r"\btribunal\b",
        ],
    ),
    # enforcement — enforcement actions, inspection, sampling, seizure.
    # Placed AFTER authority so that queries like "The officer shall inspect"
    # keep the authority tie (officer + inspect → authority wins, earlier
    # entry).  Pure enforcement queries ("When will the FSO conduct sampling?"
    # with no authority keyword) still classify as enforcement.
    (
        "enforcement",
        [
            r"\binspect",
            r"\bsampl",
            r"\benforc",
            r"\bseiz",
            r"\bdetention\b",
            r"\bclosure\s+notice\b",
            r"\bshow\s+cause\b",
            # exercise-of-power vocabulary: the action an officer may take
        ],
    ),
    # cross-reference — "as provided under", "read with", "refer to", schedule
    (
        "cross_reference",
        [
            r"\breferred\s+to\s+in\b",
            r"\bread\s+with\b",
            r"\bas\s+provided\s+under\b",
            r"\bas\s+per\b",
            r"\bschedule\s+[a-z]?\d+\b",
            r"\brule\s+\d+\b",
            r"\bshall\s+apply\s+in\s+accordance\b",
            r"\bmeans\s+and\s+includes\b",
            r"\bnotwithstanding\s+anything\s+contained\b",
            r"\bsubject\s+to\s+the\s+provision",
            r"\bexplained\s+in\b",
            r"\bgiven\s+under\b",
        ],
    ),
    # temporal — when, time limits, deadlines, validity periods, and the
    # version-in-force shape ("which version applies", "as in force in 2019").
    # The in-force/version family is generic statutory drafting language; with
    # only the first family present every temporal question fell through to
    # "ambiguous".
    (
        "temporal",
        [
            r"\bhow\s+long\b",
            r"\btime\s+limit",
            r"\bdeadline",
            r"\bvalid\s+for\b",
            r"\bvalidity\b",
            r"\bperiod\s+of\b",
            r"\bwithin\s+\d+\s+(?:days|months|years)\b",
            r"\bas\s+of\s+which\s+date\b",
            r"\bin\s+force\b",
            r"\bcome\s+into\s+force\b",
            r"\bcome\s+into\s+operation\b",
            r"\bversion\b",
            r"\bprior\s+to\b",
            r"\bsubsequent\s+to\b",
            r"\bwith\s+effect\s+from\b",
        ],
    ),
    # definition — explicit definition asks + statutory definition shapes.
    # Borrowed from the planner keyword list (define/definition/refers to)
    # and the chunk/taxonomy guards (quoted-term "means", for the purposes
    # of, shall have the meaning).  Bare "means"/"includes" are DELIBERATELY
    # absent: they fire on ordinary prose ("this means the result") — the
    # quoted-term guard is what makes this precise.  Placed before
    # ``obligation`` so a definition ask containing "shall" wins the tie.
    (
        "definition",
        [
            r"""['"][^'"]{1,60}['"]\s+means\b""",
            r"\bdefin",
            r"\bfor\s+the\s+purposes\s+of\b",
            r"\bshall\s+have\s+the\s+meaning\b",
            r"\brefer(?:s)?\s+to\b",
            r"\bwhat\s+is\s+meant\s+by\b",
            r"\bmeaning\s+of\b",
        ],
    ),
    # procedure — process/how-to asks + forum words (shared with the
    # chunk-side provision-type list).  Placed before ``obligation`` so
    # "how shall I appeal" types as procedure, not obligation.  ``tribunal``
    # intentionally duplicates the authority list — ties keep authority
    # (earlier entry), count-wins still resolve genuinely procedural
    # queries here.
    (
        "procedure",
        [
            r"\bprocedure\b",
            r"\bappeal",
            r"\bhearing",
            r"\bcompound(?:ing|able|ed)?\b",
            r"\bhow\s+to\b",
            r"\bapply\s+(?:for|to)\b",
            r"\bregistration\s+process\b",
            r"\bsteps?\b",
            r"\bform\s+(?:ii|iii|iv|v|vi|vii|viii)\b",
            r"\bseiz",
        ],
    ),
    # direct provision — verbatim-text asks.  Deliberately narrow: this is
    # the default lookup shape, and a broad pattern here would swallow
    # penalty/prohibition queries that cite a section.
    (
        "direct_provision",
        [
            r"\bwhat\s+does\s+section\b",
            r"\bwhat\s+does\s+the\s+act\b",
            r"\btext\s+of\b",
            r"\bbare\s+act\b",
            r"\bword(?:ing)?\s+of\s+section\b",
        ],
    ),
    # obligation — duties, responsibilities, what must be done.
    # NOTE: ``shall`` is deliberately removed from this list — it is too
    # common and would swallow procedural, penalty, and prohibition
    # queries.  The more specific obligation keywords (duty, obligation,
    # responsibility) are sufficient to identify obligation intent.
    (
        "obligation",
        [
            r"\bdut(?:y|ies)\b",
            r"\bobligat",
            r"\bresponsibilit",
            r"\bmust\b",
            r"\brequir",
            r"\bexpected\s+to\b",
            r"\bestablish\b",
            r"\belligib",
            r"\bcriteri",
            r"\bno\s+\w+\s+shall\b",
        ],
    ),
    # insufficient-evidence — evidentiary standards, burden of proof
    (
        "insufficient_evidence",
        [
            r"\bevidenc",
            r"\bburden\s+of\s+proof\b",
            r"\binsufficient\b",
            r"\bnot\s+sufficient\b",
            r"\breasonable\s+suspicion\b",
            r"\bprobable\s+cause\b",
            r"\bno\s+(?:evidence|record|document)",
        ],
    ),
]

#: Pattern labels are canonical ids; emitted strings are the registry surfaces.
for _label, _ in _TYPE_PATTERNS:
    assert _registry.resolve(_label) == _label, (
        f"legal classifier pattern label {_label!r} must be canonical"
    )
_AMBIGUOUS_SURFACE: str = _registry.REGISTRY["ambiguous"].emitted
del _label


#: Compiled patterns cache — list of (canonical id, compiled_patterns)
_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (label, [re.compile(p, re.IGNORECASE) for p in pats]) for label, pats in _TYPE_PATTERNS
]


def classify_legal_query(query: str) -> str:
    """Classify a legal query into the registry's emitted surface string.

    Uses keyword patterns keyed by canonical id and ordered by specificity.
    Returns the first matching type's declared surface, or ``"ambiguous"`` if
    none match confidently.

    The classifier is deliberately conservative: when multiple types match,
    it picks the one with the most keyword hits, not just the first match.
    This avoids misclassifying mixed queries (e.g., "officer prosecution"
    which could be Authority or Offence).
    """
    if not query or not query.strip():
        return _AMBIGUOUS_SURFACE

    scores: dict[str, int] = {}

    for label, patterns in _COMPILED:
        count = sum(1 for p in patterns if p.search(query))
        if count > 0:
            scores[label] = count

    if not scores:
        return _AMBIGUOUS_SURFACE

    # Return the type with the most keyword hits (key=s is a callable returning
    # int, which mypy's max overload rejects — use itemgetter instead)
    best_label = max(scores.items(), key=lambda item: item[1])[0]
    return _registry.REGISTRY[best_label].emitted


def classify_with_confidence(query: str) -> tuple[str, float]:
    """Classify a legal query and return (emitted surface, confidence 0-1).

    Confidence = fraction of matched keywords among all keywords for that type.
    """
    if not query or not query.strip():
        return _AMBIGUOUS_SURFACE, 0.0

    scores: dict[str, tuple[int, int]] = {}
    for label, patterns in _COMPILED:
        count = sum(1 for p in patterns if p.search(query))
        if count > 0:
            scores[label] = (count, len(patterns))

    if not scores:
        return _AMBIGUOUS_SURFACE, 0.0

    best_label, pair = max(scores.items(), key=lambda item: item[1][0])
    hits, total = pair
    confidence = hits / total if total > 0 else 0.0
    return _registry.REGISTRY[best_label].emitted, confidence


# ---------------------------------------------------------------------------
# QueryTypeConfig collection for backward compatibility with measure_ensemble_live
# ---------------------------------------------------------------------------

__all__ = [
    "AMBIGUOUS_CONFIG",
    "AUTHORITY_CONFIG",
    "CROSS_REFERENCE_CONFIG",
    "DEFAULT_CONFIG",
    "DEFINITION_CONFIG",
    "DIRECT_PROVISION_CONFIG",
    "ENFORCEMENT_CONFIG",
    "EXCEPTION_CONFIG",
    "INSUFFICIENT_EVIDENCE_CONFIG",
    "LEGAL_QUERY_TYPES",
    "OBLIGATION_CONFIG",
    "OFFENCE_CONFIG",
    "PENALTY_CONFIG",
    "PROCEDURE_CONFIG",
    "PROHIBITION_CONFIG",
    "QUERY_TYPE_CONFIGS",
    "TEMPORAL_CONFIG",
    "QueryTypeConfig",
    "classify_legal_query",
    "classify_with_confidence",
    "get_config",
]
