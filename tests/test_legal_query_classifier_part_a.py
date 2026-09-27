"""Part A (universal multihop): definition/procedure/direct-provision typing.

Pins that the legal query classifier emits the three previously-unreachable
types, that the quoted-``means`` guard keeps ordinary prose out of
``definition``, that pre-existing winners keep their ties, and that the
planner produces PROCEDURE requirements (plus the adversarial-permission
DEFINITION requirement that Part B's chunk/requirement signals consume).
"""

from __future__ import annotations

from app.rag.evidence_task import EvidenceRequirement
from app.rag.planning.query_planner import Intent, QueryPlanner
from app.rag.retrieval.legal_query_classifier import (
    DEFINITION_CONFIG,
    DEFAULT_CONFIG,
    LEGAL_QUERY_TYPES,
    QUERY_TYPE_CONFIGS,
    classify_legal_query,
    classify_with_confidence,
    get_config,
)


def test_definition_explicit_ask():
    assert classify_legal_query("What is the definition of food under the FSS Act?") == "definition"


def test_definition_quoted_means_shape():
    q = 'The term "food" means what for the purposes of the Act?'
    assert classify_legal_query(q) == "definition"


def test_definition_guard_bare_means_not_definition():
    assert classify_legal_query("This means the result is wrong") != "definition"


def test_procedure_compounding_ask():
    assert (
        classify_legal_query("What is the procedure for compounding an offence under Section 69?")
        == "procedure"
    )


def test_procedure_beats_obligation_shall_on_tie():
    assert classify_legal_query("How shall I appeal against the order?") == "procedure"


def test_authority_keeps_tribunal_tie():
    # ``tribunal`` is in both lists; on a true tie the earlier (authority)
    # entry wins.  ("Which tribunal hears the appeal?" is procedure by
    # count — appeal + tribunal vs tribunal alone.)
    assert classify_legal_query("The tribunal may inspect records") == "authority"


def test_officer_shall_stays_authority():
    # Pre-existing tie behavior preserved: authority precedes obligation.
    assert classify_legal_query("The officer shall inspect the premises") == "authority"


def test_direct_provision_verbatim_ask():
    assert (
        classify_legal_query("What is the text of Section 12 of the bare act?")
        == "direct provision"
    )


def test_direct_provision_stays_narrow():
    assert classify_legal_query("What does Section 55 say about penalty?") == "penalty"


def test_refer_to_plural_counts_as_definition_signal():
    # Approximate: a numberless "refer to" ask carries no cross-reference
    # pattern hit, so the definition signal wins the count. Harmless now
    # that routing is type-independent (affects only weights/flavor).
    assert classify_legal_query("which rule does section 12 refer to") == "definition"


def test_adversarial_permission_not_definition_at_query_level():
    # Documents the Part A boundary: no definition keywords in the query, so
    # query typing cannot catch it — Part B (requirement graph + chunk
    # markers) is the repair path.
    assert classify_legal_query("Does Section 58 permit the sale of handmade cosmetics?") != "definition"


def test_definition_has_dedicated_config():
    assert get_config("definition") is DEFINITION_CONFIG
    assert DEFINITION_CONFIG is not DEFAULT_CONFIG


def test_configs_cover_all_legal_types():
    assert set(QUERY_TYPE_CONFIGS) == set(LEGAL_QUERY_TYPES)


def test_classify_with_confidence_definition():
    label, conf = classify_with_confidence("Define food safety under the Act")
    assert label == "definition"
    assert conf > 0.0


def test_planner_emits_procedure_requirement():
    plan = QueryPlanner().plan("What is the procedure for compounding under Section 69?")
    assert any(
        t.evidence_requirement == EvidenceRequirement.PROCEDURE for t in plan.tasks
    )


def test_planner_adversarial_permission_yields_definition():
    plan = QueryPlanner().plan("Does Section 58 permit sale of handmade cosmetics?")
    assert any(
        t.evidence_requirement == EvidenceRequirement.DEFINITION for t in plan.tasks
    )


def test_planner_intent_procedure_maps():
    from app.rag.planning.query_planner import _REQUIREMENT_TO_INTENT

    assert _REQUIREMENT_TO_INTENT[EvidenceRequirement.PROCEDURE] is Intent.PROCEDURE


def test_planner_pure_procedure_ask_yields_procedure_intent():
    # Finding 4: the PROCEDURE keyword entry makes Intent.PROCEDURE
    # reachable for asks with no provision/penalty/exception signals.
    from app.rag.planning.query_planner import _extract_intent

    assert _extract_intent("how to appeal against the order") is Intent.PROCEDURE


def test_planner_provision_query_keeps_lookup_intent():
    # Keyword order: existing winners keep priority over PROCEDURE.
    from app.rag.planning.query_planner import _extract_intent

    assert _extract_intent("procedure under Section 69") is Intent.LOOKUP
