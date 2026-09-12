"""Gold decomposition dataset (Phase 4, plan item 19 + Phase 3 expansion).

Hand-authored expectations for :class:`app.rag.evaluation.benchmark.DecompositionBenchmark`.
Each entry pairs a query with the *ideal* decomposition under the V2
architecture (EvidenceTask taxonomy + DAG semantics):

- ``gold_task_kinds``: the evidence-requirement kinds an ideal decomposition
  should produce (order-insensitive; compared as a multiset).
- ``gold_dependencies``: which kinds depend on which, keyed by kind.  When a
  kind appears twice, dependencies are compared positionally within the kind.
- ``gold_entities``: entities the tasks should scope to (informational, used
  for evidence-level matching later; not scored by the benchmark yet).
- ``gold_requirements``: requirement-level expectations (Phase 3) — the
  independently verifiable answer requirements with ids, types, answer types,
  evidence signals and mandatory flags, scored by
  ``app.rag.evaluation.decomposition_metrics`` (RC / AS / DE / EC).

Expectations are calibrated against the deterministic planner's *design*
intent, not its current output — entries where the planner under- or
over-decomposes are exactly the signal the benchmark exists to surface.

The dataset covers two tiers:

1. **Core families** (entries 1–5): simple lookup, definitions, multi-part,
   comparative, temporal lookup.
2. **Failure-mode torture tests** (entries 6–10, Phase 3 step 4): nested
   compound queries, multi-hop chains, temporal "before the amendment"
   lookups, adversarial permission questions, and fact-pattern application —
   the query shapes the reviewer's architecture flags as where naive
   decomposers break.

Extend this list as new query families are supported; keep it pure-Python so
it stays versioned, typed, and importable without I/O.
"""

from __future__ import annotations

from typing import Any

GoldEntry = dict[str, Any]

# ---------------------------------------------------------------------------
# Shared requirement literals
# ---------------------------------------------------------------------------
# The governing-provision and penalty requirements for the "late filing of
# annual return" family are identical across the simple and multi-requirement
# queries, so they are defined once and spread into each entry with
# ``dict(...)`` — fresh dicts per entry, so consumers never share mutable
# gold state.

#: R1 — the provision establishing the late-filing requirement.
_REQ_PROVISION_LATE_FILING: dict[str, Any] = {
    "id": "R1",
    "type": "provision",
    "subject": "late filing of annual return",
    "question": "Which provision governs late filing of an annual return under the FSS Act?",
    "answer_type": "citation",
    "evidence_required": ["provision", "section", "act"],
    "mandatory": True,
}

#: R2 — the penalty attached to that requirement.
_REQ_PENALTY_LATE_FILING: dict[str, Any] = {
    "id": "R2",
    "type": "penalty",
    "subject": "late filing of annual return",
    "question": "What penalty applies to late filing of an annual return?",
    "answer_type": "numeric_or_rule",
    "evidence_required": ["penalty", "fine", "imprisonment"],
    "mandatory": True,
}

#: R3 — the exception carve-out for the same family.
_REQ_EXCEPTION_LATE_FILING: dict[str, Any] = {
    "id": "R3",
    "type": "exception",
    "subject": "late filing of annual return",
    "question": "Are there exceptions to penalties for late filing of an annual return?",
    "answer_type": "text",
    "evidence_required": ["exception", "condition", "modified_penalty"],
    "mandatory": True,
}

#: Queries whose *ideal* decomposition is known, with expected task kinds,
#: dependency edges (kind -> kinds it depends on), scoping entities, and
#: (Phase 3) requirement-level expectations.
GOLD_DECOMPOSITION: list[GoldEntry] = [
    # ------------------------------------------------------------------
    # Core families
    # ------------------------------------------------------------------
    {
        "query": "What is the penalty for late filing of an annual return under the FSS Act?",
        "query_class": "penalty_lookup",
        "domain": "food_safety",
        "gold_task_kinds": ["provision", "penalty"],
        "gold_dependencies": {"penalty": ["provision"], "provision": []},
        "gold_entities": ["annual return", "FSS Act", "penalty"],
        "gold_requirements": [
            dict(_REQ_PROVISION_LATE_FILING),
            dict(_REQ_PENALTY_LATE_FILING),
        ],
        "gold_answer_types": {"R1": "citation", "R2": "numeric_or_rule"},
        "gold_mandatory": {"R1", "R2"},
    },
    {
        "query": (
            "What are the penalties for late filing of annual return under the "
            "FSS Act, and what exceptions exist?"
        ),
        "query_class": "multi_requirement",
        "domain": "food_safety",
        # Two distinct requirements (penalty AND exception) + the governing
        # provision — the planner currently under-decomposes this to
        # provision + exception, dropping the penalty task.
        "gold_task_kinds": ["provision", "penalty", "exception"],
        "gold_dependencies": {
            "provision": [],
            "penalty": ["provision"],
            "exception": ["provision"],
        },
        "gold_entities": ["annual return", "FSS Act", "penalty", "exception"],
        "gold_requirements": [
            dict(_REQ_PROVISION_LATE_FILING),
            dict(_REQ_PENALTY_LATE_FILING),
            dict(_REQ_EXCEPTION_LATE_FILING),
        ],
        "gold_answer_types": {"R1": "citation", "R2": "numeric_or_rule", "R3": "text"},
        "gold_mandatory": {"R1", "R2", "R3"},
    },
    {
        "query": (
            "Compare the licensing requirements for small food businesses and "
            "large food manufacturers."
        ),
        "query_class": "comparative",
        "domain": "food_safety",
        # A comparative query decomposes into one atomic task per side, with
        # no dependency between them (parallel retrieval).  The planner
        # currently collapses this to a single provision task.
        "gold_task_kinds": ["condition", "condition"],
        "gold_dependencies": {"condition": []},
        "gold_entities": ["licensing", "small food business", "food manufacturer"],
        "gold_requirements": [
            {
                "id": "R1",
                "type": "condition",
                "subject": "small food businesses",
                "question": "What licensing conditions apply to small food businesses?",
                "answer_type": "text",
                "evidence_required": ["condition", "licensing", "requirement"],
                "mandatory": True,
            },
            {
                "id": "R2",
                "type": "condition",
                "subject": "large food manufacturers",
                "question": "What licensing conditions apply to large food manufacturers?",
                "answer_type": "text",
                "evidence_required": ["condition", "licensing", "requirement"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "text", "R2": "text"},
        "gold_mandatory": {"R1", "R2"},
    },
    {
        "query": "Was Section 12 of the FSS Act amended after 2020?",
        "query_class": "temporal_lookup",
        "domain": "food_safety",
        # Requirement-level gold expects provision + amendment; the kind-level
        # gold must agree (the planner's amendment detector now produces the
        # amendment requirement, resolving through the provision).
        "gold_task_kinds": ["provision", "amendment"],
        "gold_dependencies": {"provision": [], "amendment": ["provision"]},
        "gold_entities": ["Section 12", "FSS Act"],
        "gold_temporal_scope": "after 2020",
        "gold_requirements": [
            {
                "id": "R1",
                "type": "provision",
                "subject": "Section 12 of the FSS Act",
                "question": "Which provision governs Section 12 of the FSS Act?",
                "answer_type": "citation",
                "evidence_required": ["provision", "section", "act"],
                "mandatory": True,
            },
            {
                "id": "R2",
                "type": "amendment",
                "subject": "Section 12 of the FSS Act after 2020",
                "question": "Was Section 12 of the FSS Act amended after 2020?",
                "answer_type": "yes_no_or_rule",
                "evidence_required": ["amendment", "effective_date"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "citation", "R2": "yes_no_or_rule"},
        "gold_mandatory": {"R1", "R2"},
    },
    {
        "query": "What is Section 12?",
        "query_class": "direct_lookup",
        "domain": "food_safety",
        "gold_task_kinds": ["provision"],
        "gold_dependencies": {"provision": []},
        "gold_entities": ["Section 12"],
        "gold_requirements": [
            {
                "id": "R1",
                "type": "provision",
                "subject": "Section 12",
                "question": "What is Section 12 of the FSS Act?",
                "answer_type": "citation",
                "evidence_required": ["provision", "section", "act", "definition"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "citation"},
        "gold_mandatory": {"R1"},
    },
    # ------------------------------------------------------------------
    # Failure-mode torture tests (Phase 3 step 4)
    # ------------------------------------------------------------------
    {
        "query": (
            "What is prohibited under Section 31 of the FSS Act, who can "
            "enforce it, what is the penalty, and what exceptions apply?"
        ),
        "query_class": "nested_compound",
        "domain": "food_safety",
        # Four independently verifiable requirements riding on one provision:
        # the canonical nested query from the improvement plan (§25).  The
        # prohibition requirement resolves first; authority, penalty and
        # exception all resolve *through* it.
        "gold_task_kinds": ["provision", "authority", "penalty", "exception"],
        "gold_dependencies": {
            "provision": [],
            "authority": ["provision"],
            "penalty": ["provision"],
            "exception": ["provision"],
        },
        "gold_entities": ["Section 31", "FSS Act", "prohibition", "penalty", "exception"],
        "gold_requirements": [
            {
                "id": "R1",
                "type": "provision",
                "subject": "Section 31 of the FSS Act",
                "question": "What does Section 31 of the FSS Act prohibit?",
                "answer_type": "citation",
                "evidence_required": ["provision", "section", "act"],
                "mandatory": True,
            },
            {
                "id": "R2",
                "type": "authority",
                "subject": "enforcement of Section 31 of the FSS Act",
                "question": "Who has the power to enforce Section 31 of the FSS Act?",
                "answer_type": "citation",
                "evidence_required": ["authority", "power", "may"],
                "mandatory": True,
            },
            {
                "id": "R3",
                "type": "penalty",
                "subject": "contravention of Section 31 of the FSS Act",
                "question": "What penalty applies to a contravention of Section 31 of the FSS Act?",
                "answer_type": "numeric_or_rule",
                "evidence_required": ["penalty", "fine", "imprisonment"],
                "mandatory": True,
            },
            {
                "id": "R4",
                "type": "exception",
                "subject": "Section 31 of the FSS Act",
                "question": "What exceptions apply to the prohibition in Section 31 of the FSS Act?",
                "answer_type": "text",
                "evidence_required": ["exception", "unless", "notwithstanding"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "citation", "R2": "citation", "R3": "numeric_or_rule", "R4": "text"},
        "gold_mandatory": {"R1", "R2", "R3", "R4"},
    },
    {
        "query": (
            "Which provision authorizes the recall order prescribed by Rule "
            "2.3.1 of the FSS Regulations, and what penalty follows from "
            "non-compliance?"
        ),
        "query_class": "multi_hop",
        "domain": "food_safety",
        # The penalty cannot be located without first resolving the
        # cross-reference: Rule 2.3.1 → authorizing section → penalty section.
        "gold_task_kinds": ["cross_reference", "penalty"],
        "gold_dependencies": {"cross_reference": [], "penalty": ["cross_reference"]},
        "gold_entities": ["Rule 2.3.1", "FSS Regulations", "recall order", "penalty"],
        "gold_requirements": [
            {
                "id": "R1",
                "type": "cross_reference",
                "subject": "recall order under Rule 2.3.1 of the FSS Regulations",
                "question": "Which section of the FSS Act authorizes the recall order prescribed by Rule 2.3.1 of the FSS Regulations?",
                "answer_type": "citation",
                "evidence_required": ["section", "read with", "referred to"],
                "mandatory": True,
            },
            {
                "id": "R2",
                "type": "penalty",
                "subject": "non-compliance with the recall order",
                "question": "What penalty follows from non-compliance with the recall order?",
                "answer_type": "numeric_or_rule",
                "evidence_required": ["penalty", "fine", "imprisonment"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "citation", "R2": "numeric_or_rule"},
        "gold_mandatory": {"R1", "R2"},
    },
    {
        "query": "What was the applicable penalty for substandard food before the 2021 amendment?",
        "query_class": "temporal_before",
        "domain": "food_safety",
        # "Before the amendment" requires resolving the amendment chain, then
        # reading the penalty at the prior temporal state — not just the
        # current text of the penalty provision.
        "gold_task_kinds": ["penalty", "amendment"],
        "gold_dependencies": {"penalty": [], "amendment": ["penalty"]},
        "gold_entities": ["substandard food", "penalty", "2021 amendment"],
        "gold_temporal_scope": "before 2021",
        "gold_requirements": [
            {
                "id": "R1",
                "type": "penalty",
                "subject": "substandard food before the 2021 amendment",
                "question": "What penalty applied to substandard food before the 2021 amendment?",
                "answer_type": "numeric_or_rule",
                "evidence_required": ["penalty", "fine", "effective_date"],
                "mandatory": True,
                "temporal_scope": "before 2021",
            },
            {
                "id": "R2",
                "type": "amendment",
                "subject": "the 2021 amendment to the penalty for substandard food",
                "question": "Which amendment changed the penalty for substandard food in 2021?",
                "answer_type": "citation",
                "evidence_required": ["amendment", "effective_date"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "numeric_or_rule", "R2": "citation"},
        "gold_mandatory": {"R1", "R2"},
    },
    {
        "query": "Does Section 58 of the FSS Act permit the sale of handmade cosmetics?",
        "query_class": "adversarial_exception",
        "domain": "food_safety",
        # Adversarial per the improvement plan (§25): the answer is found only
        # through the section + the governing definition + the exception —
        # a "yes/no" question that decomposes into three requirements.
        "gold_task_kinds": ["provision", "definition", "exception"],
        "gold_dependencies": {"provision": [], "definition": ["provision"], "exception": ["provision"]},
        "gold_entities": ["Section 58", "FSS Act", "handmade cosmetics", "sale"],
        "gold_requirements": [
            {
                "id": "R1",
                "type": "provision",
                "subject": "Section 58 of the FSS Act",
                "question": "What does Section 58 of the FSS Act actually provide?",
                "answer_type": "citation",
                "evidence_required": ["provision", "section", "act"],
                "mandatory": True,
            },
            {
                "id": "R2",
                "type": "definition",
                "subject": "handmade cosmetics and sale",
                "question": "How do the FSS Act and its rules define cosmetics and sale for Section 58?",
                "answer_type": "text",
                "evidence_required": ["means", "definition", "includes"],
                "mandatory": True,
            },
            {
                "id": "R3",
                "type": "exception",
                "subject": "Section 58 of the FSS Act",
                "question": "Does any exception or proviso to Section 58 qualify the permission?",
                "answer_type": "yes_no_or_rule",
                "evidence_required": ["exception", "unless", "notwithstanding"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "citation", "R2": "text", "R3": "yes_no_or_rule"},
        "gold_mandatory": {"R1", "R2", "R3"},
    },
    {
        "query": (
            "A food business operator continued operations after his licence "
            "was suspended. Has he committed an offence under the FSS Act?"
        ),
        "query_class": "fact_pattern",
        "domain": "food_safety",
        # Fact-pattern application: the offence provision plus an explicit
        # apply-the-facts requirement (the "boolean" answer shape the
        # FACT_APPLICATION contract exists for).
        "gold_task_kinds": ["provision", "fact_application"],
        "gold_dependencies": {"provision": [], "fact_application": ["provision"]},
        "gold_entities": ["licence suspension", "continued operations", "offence", "FSS Act"],
        "gold_requirements": [
            {
                "id": "R1",
                "type": "provision",
                "subject": "operating under a suspended licence",
                "question": "Which provision makes operating under a suspended licence an offence under the FSS Act?",
                "answer_type": "citation",
                "evidence_required": ["provision", "section", "act"],
                "mandatory": True,
            },
            {
                "id": "R2",
                "type": "fact_application",
                "subject": "continued operations after licence suspension",
                "question": "Do the stated facts satisfy the elements of the offence?",
                "answer_type": "boolean",
                "evidence_required": ["shall", "contravention", "scenario"],
                "mandatory": True,
            },
        ],
        "gold_answer_types": {"R1": "citation", "R2": "boolean"},
        "gold_mandatory": {"R1", "R2"},
    },
]
