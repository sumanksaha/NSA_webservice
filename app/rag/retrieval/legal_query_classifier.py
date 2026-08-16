"""Legal query-type classifier & query-type-aware reranker configuration.

This module implements the empirical query-type routing strategy described in
``CE_RERANK_REVIEW.md`` Sections 7-10: different legal query types respond to
different reranker configurations, and a single global configuration is
suboptimal.

The classifier is **rule-based** (no LLM) for determinism and zero latency
overhead.  It maps user queries to one of the legal query types used in the
evaluation framework:

    penalty, direct provision, exception, obligation, procedure,
    authority, prohibition, cross-reference, offence, enforcement,
    insufficient-evidence, temporal, ambiguous

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
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Legal query types — aligned with the evaluation framework taxonomy
# ---------------------------------------------------------------------------

#: Canonical set of legal query types used throughout the system.
LEGAL_QUERY_TYPES = frozenset({
    "penalty",
    "direct provision",
    "exception",
    "obligation",
    "procedure",
    "authority",
    "prohibition",
    "cross-reference",
    "offence",
    "enforcement",
    "insufficient-evidence",
    "temporal",
    "ambiguous",
})


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

#: Master mapping: query type string -> QueryTypeConfig
QUERY_TYPE_CONFIGS: dict[str, QueryTypeConfig] = {
    "penalty": PENALTY_CONFIG,
    "direct provision": DIRECT_PROVISION_CONFIG,
    "exception": EXCEPTION_CONFIG,
    "obligation": OBLIGATION_CONFIG,
    "procedure": PROCEDURE_CONFIG,
    "authority": AUTHORITY_CONFIG,
    "prohibition": PROHIBITION_CONFIG,
    "cross-reference": CROSS_REFERENCE_CONFIG,
    "offence": OFFENCE_CONFIG,
    "enforcement": ENFORCEMENT_CONFIG,
    "insufficient-evidence": INSUFFICIENT_EVIDENCE_CONFIG,
    "temporal": TEMPORAL_CONFIG,
    "ambiguous": AMBIGUOUS_CONFIG,
}


def get_config(query_type: str) -> QueryTypeConfig:
    """Get the reranker configuration for a query type (falls back to DEFAULT)."""
    return QUERY_TYPE_CONFIGS.get(query_type, DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Legal query-type classifier (rule-based, deterministic)
# ---------------------------------------------------------------------------

#: Keyword patterns for each legal query type.
#: Order matters: more specific types are checked first.
_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    # penalty — monetary/term punishment, fine, imprisonment, penalty amounts
    (
        "penalty",
        [
            r"\bpenalty\b",
            r"\bimprison\b",
            r"\bfine\b",
            r"\bpunish\b",
            r"\brs\.?\s*\d+[,\d]*\s*(?:per|for|each|or)\b",
            r"\bshall\s+be\s+punish",
            r"\bwith\s+imprisonment",
            r"\bpunishable\b",
            r"\bmonetary\s+penalty\b",
        ],
    ),
    # offence — criminal liability, commission of offence, prosecution
    (
        "offence",
        [
            r"\boffence\b",
            r"\bcommit\s+an?\s+offence\b",
            r"\bcommission\s+of\s+offence\b",
            r"\bprosecut(e|ion)\b",
            r"\bliable\s+to\s+prosecut\b",
            r"\bcriminal\s+liability\b",
            r"\bguilty\b",
            r"\bconvict",
        ],
    ),
    # prohibition — prohibition orders, what is prohibited, restrictions
    (
        "prohibition",
        [
            r"\bprohibit\b",
            r"\bprohibition\b",
            r"\bprohibited\b",
            r"\bshall\s+not\b",
            r"\bno\s+(?:person|food\s*business)",
            r"\brestrict\b",
            r"\brestriction\b",
            r"\bprohibition\s+order\b",
        ],
    ),
    # exception — carve-outs, exemptions, exceptions to provisions
    (
        "exception",
        [
            r"\bexceptions?\b",
            r"\bexempt\b",
            r"\bexemption\b",
            r"\bnot\s+applicable\b",
            r"\bdoesn't\s+apply\b",
            r"\bdoesn\t+apply\b",
            r"\bsaving\b",
            r"\bsubject\s+to\b",
            r"\bnotwithstanding\b",
        ],
    ),
    # authority — officers, boards, agencies, who has power
    (
        "authority",
        [
            r"\bofficer\b",
            r"\bauth\b",
            r"\bauthority\b",
            r"\bboard\b",
            r"\bagency\b",
            r"\bcommission\b",
            r"\btribunal\b",
            r"\bdesignated\s+officer\b",
            r"\bfso\b",
            r"\bfsoi\b",
            r"\bwho\s+is\b.*\borgan",
            r"\bempowered\b",
            r"\bprincipal\s+enforcement\b",
            r"\bfood\s+analyst\b",
            r"\bappellate\b",
            r"\bjurisdicti",
        ],
    ),
    # cross-reference — "as provided under", "read with", "refer to", schedule
    (
        "cross-reference",
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
            r"\bsubject\s+to\s+the\s+provisions\b",
            r"\bexplained\s+in\b",
        ],
    ),
    # temporal — when, time limits, deadlines, validity periods
    (
        "temporal",
        [
            r"\bhow\s+long\b",
            r"\btime\s+limit\b",
            r"\bdeadline\b",
            r"\bvalid\s+for\b",
            r"\bvalidity\b",
            r"\bperiod\s+of\b",
            r"\bwithin\s+\d+\s+(?:days|months|years)\b",
        ],
    ),
    # obligation — duties, responsibilities, what must be done
    (
        "obligation",
        [
            r"\bresponsibility\b",
            r"\bduty\b",
            r"\bobligation\b",
            r"\bmust\b",
            r"\bshall\b",
            r"\brequired\s+to\b",
            r"\bexpected\s+to\b",
            r"\bestablish\b",
        ],
    ),
    # enforcement — enforcement actions, inspection, sampling
    (
        "enforcement",
        [
            r"\binspect\b",
            r"\bsample\b",
            r"\benforcement\b",
            r"\benter\s+and\s+inspect\b",
            r"\bseiz\b",
            r"\bdetention\b",
            r"\bclosure\s+notice\b",
            r"\bshow\s+cause\b",
        ],
    ),
    # insufficient-evidence — evidentiary standards, burden of proof
    (
        "insufficient-evidence",
        [
            r"\bevidence\b",
            r"\bburden\s+of\s+proof\b",
            r"\binsufficient\b",
            r"\bnot\s+sufficient\b",
            r"\breasonable\s+suspicion\b",
            r"\bprobable\s+cause\b",
        ],
    ),
]

#: Compiled patterns cache — list of (label, compiled_patterns)
_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (label, [re.compile(p, re.IGNORECASE) for p in pats]) for label, pats in _TYPE_PATTERNS
]


def classify_legal_query(query: str) -> str:
    """Classify a legal query into a query type string.

    Uses keyword patterns ordered by specificity.  Returns the first matching
    type, or ``"ambiguous"`` if none match confidently.

    The classifier is deliberately conservative: when multiple types match,
    it picks the one with the most keyword hits, not just the first match.
    This avoids misclassifying mixed queries (e.g., "officer prosecution"
    which could be Authority or Offence).
    """
    if not query or not query.strip():
        return "ambiguous"

    scores: dict[str, int] = {}

    for label, patterns in _COMPILED:
        count = sum(1 for p in patterns if p.search(query))
        if count > 0:
            scores[label] = count

    if not scores:
        return "ambiguous"

    # Return the type with the most keyword hits (key=s is a callable returning
    # int, which mypy's max overload rejects — use itemgetter instead)
    return max(scores.items(), key=lambda item: item[1])[0]


def classify_with_confidence(query: str) -> tuple[str, float]:
    """Classify a legal query and return (type, confidence 0-1).

    Confidence = fraction of matched keywords among all keywords for that type.
    """
    if not query or not query.strip():
        return "ambiguous", 0.0

    scores: dict[str, tuple[int, int]] = {}
    for label, patterns in _COMPILED:
        count = sum(1 for p in patterns if p.search(query))
        if count > 0:
            scores[label] = (count, len(patterns))

    if not scores:
        return "ambiguous", 0.0

    best_label, pair = max(scores.items(), key=lambda item: item[1][0])
    hits, total = pair
    confidence = hits / total if total > 0 else 0.0
    return best_label, confidence


# ---------------------------------------------------------------------------
# QueryTypeConfig collection for backward compatibility with measure_ensemble_live
# ---------------------------------------------------------------------------

__all__ = [
    "LEGAL_QUERY_TYPES",
    "QueryTypeConfig",
    "DEFAULT_CONFIG",
    "PENALTY_CONFIG",
    "PROHIBITION_CONFIG",
    "AUTHORITY_CONFIG",
    "DIRECT_PROVISION_CONFIG",
    "EXCEPTION_CONFIG",
    "OBLIGATION_CONFIG",
    "PROCEDURE_CONFIG",
    "CROSS_REFERENCE_CONFIG",
    "OFFENCE_CONFIG",
    "ENFORCEMENT_CONFIG",
    "INSUFFICIENT_EVIDENCE_CONFIG",
    "TEMPORAL_CONFIG",
    "AMBIGUOUS_CONFIG",
    "QUERY_TYPE_CONFIGS",
    "get_config",
    "classify_legal_query",
    "classify_with_confidence",
]
