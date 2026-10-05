"""Regression tests for the KG reasoning engine (2026-10-05).

``app/rag/planning/kg_reasoner.py`` was written against a schema the graph
never had: ``Section`` / ``Penalty`` / ``Exception`` / ``Temporal`` nodes,
``HAS_AUTHORITY`` / ``HAS_EXCEPTION`` / ``HAS_CROSS_REFERENCES`` edges, and
an ``FSSA::31`` provision-id namespace.  Every one of those is absent from
the live graph, so ``reason_from_query`` returned an empty list for every
query and the node recorded a successful-looking no-op.

These tests pin the three properties that broke:

1. The Cypher allowlist and templates reference only relationships and
   labels the graph actually has.
2. Section numbers resolve to real ``provision_id`` values, never the
   invented ``FSSA::`` namespace.
3. ``kg_paths`` is declared on ``RAGState`` — LangGraph silently drops any
   key a node returns that the state schema does not declare, so the node's
   output was discarded even once the node was registered.

Pure-function tests: no Neo4j, Qdrant or LLM required.
"""

from __future__ import annotations

from app.rag.agent.state import RAGState
from app.rag.planning.kg_reasoner import (
    _CYPHER_PATTERNS,
    ALLOWED_RELATIONS,
    _extract_sections,
    _sanitize_param,
    generate_cypher,
)

#: Labels and relationship types that exist in the live Neo4j graph.
#: Verified against the Aura instance on 2026-10-05.
REAL_LABELS = frozenset({
    "Chunk",
    "LegalProvision",
    "Document",
    "Act",
    "LegalConcept",
    "Authority",
    "Regulation",
    "Rule",
    "Notification",
    "LegalDomain",
    "Jurisdiction",
    "Person",
    "Circular",
})

REAL_RELATIONS = frozenset({
    "HAS_CHUNK",
    "SUPPORTED_BY",
    "BELONGS_TO_DOMAIN",
    "CONTAINS",
    "SOURCE_OF",
    "IMPOSES_DUTY",
    "APPLIES_TO",
    "PRESCRIBES_PENALTY",
    "PROHIBITS",
    "DEFINES",
    "EXEMPTS",
    "CREATES_OFFENCE",
    "DECLARES",
    "PRESCRIBES",
    "GRANTS_POWER_TO",
    "GRANTS_PERMISSION",
    "RELEVANT_IN",
    "ISSUED_BY",
    "APPLIES_TO_JURISDICTION",
    "SUPERSEDED_BY",
    "AMENDED_BY",
})

#: Node labels that were assumed by the original implementation and do not
#: exist in the graph at all.
FICTIONAL_LABELS = ("Section", "Penalty", "Exception", "Temporal", "Provision")


# --------------------------------------------------------------------------- #
# 1. Cypher allowlist / templates must match the real schema
# --------------------------------------------------------------------------- #


def test_allowed_relations_all_exist_in_the_graph():
    """A relation in the allowlist that no node carries can never match.

    The original allowlist was entirely fictional, so the validate-query
    check passed while every generated query matched nothing.
    """
    fictional = ALLOWED_RELATIONS - REAL_RELATIONS
    assert not fictional, f"allowlist references non-existent relations: {sorted(fictional)}"


def test_templates_use_only_real_relations():
    import re

    for intent, template in _CYPHER_PATTERNS.items():
        for group in re.findall(r"\[:([^:\]]+)", template):
            for branch in group.replace("*1..5", "").split("|"):
                rel = branch.strip().lstrip(":")
                if not rel:
                    continue
                assert rel in REAL_RELATIONS, f"intent {intent!r} uses non-existent relation {rel!r}"


def test_templates_use_only_real_labels():
    import re

    for intent, template in _CYPHER_PATTERNS.items():
        # Strip relationship annotations first — ``[:REL|REL]`` would
        # otherwise have its contents read as node labels.
        without_rels = re.sub(r"\[:[^:\]]+\]", "[]", template)
        for label in re.findall(r":(\w+)", without_rels):
            assert label in REAL_LABELS, f"intent {intent!r} uses non-existent label {label!r}"


def test_templates_do_not_reference_fictional_nodes():
    for intent, template in _CYPHER_PATTERNS.items():
        for label in FICTIONAL_LABELS:
            # Match the label as a node annotation, not as a substring of a
            # property name or an unrelated word.
            assert f":{label} " not in template and f":{label})" not in template, (
                f"intent {intent!r} still references the fictional node type {label!r}"
            )


def test_section_pattern_targets_legal_provision():
    """``$section`` is substituted with a ``provision_id``, so the template
    must match that property on the real label."""
    cypher = generate_cypher("permission", {"section": "FSS_ACT_2006_SEC_31"})
    assert cypher is not None
    assert ":LegalProvision" in cypher
    assert 'provision_id: "FSS_ACT_2006_SEC_31"' in cypher


def test_generate_cypher_still_blocks_unlisted_relations():
    """The validate-query guard must remain a real guard, not a no-op."""
    original = dict(_CYPHER_PATTERNS)
    try:
        _CYPHER_PATTERNS["permission"] = "MATCH (a)-[:TOTALLY_MADE_UP]->(b) RETURN a, b"
        assert generate_cypher("permission", {"section": "X"}) is None
    finally:
        _CYPHER_PATTERNS.clear()
        _CYPHER_PATTERNS.update(original)


# --------------------------------------------------------------------------- #
# 2. Section numbers must not be invented into an FSSA:: namespace
# --------------------------------------------------------------------------- #


def test_extract_sections_returns_bare_numbers():
    """Section numbers are graph ``provision_number`` values, not ids."""
    assert _extract_sections("What is the penalty under Section 31?") == ["31"]
    assert _extract_sections("Under section 16 and Section 31") == ["16", "31"]


def test_extract_sections_dedupes():
    assert _extract_sections("Section 31 and again section 31") == ["31"]


def test_extract_sections_never_emits_fssa_namespace():
    """The original built ``FSSA::<n>``, which matches no node in the graph.

    ``_resolve_provision_ids`` is now the only thing that mints ids, and it
    does so by querying the graph.
    """
    for query in ("Section 31", "see section 59 IPC", "Section 7(2) of the Act"):
        for value in _extract_sections(query):
            assert "::" not in value
            assert not value.upper().startswith("FSSA")


def test_sanitize_param_preserves_real_provision_ids():
    """Ids contain underscores and digits; the sanitizer must not strip them."""
    for pid in ("FSS_ACT_2006_SEC_31", "IPC_1860_SEC_1", "PCA_1960_SEC_12"):
        assert _sanitize_param(pid) == pid


def test_sanitize_param_still_strips_injection_characters():
    assert '"' not in _sanitize_param('a" OR 1=1 //')
    assert ";" not in _sanitize_param("x; MATCH (n) DETACH DELETE n")


# --------------------------------------------------------------------------- #
# 3. kg_paths must be declared on RAGState
# --------------------------------------------------------------------------- #


def test_kg_state_keys_are_declared():
    """LangGraph drops node-returned keys the state schema does not declare.

    ``kg_reason_node`` returns ``kg_paths`` / ``kg_cypher``, and
    ``targeted_retry`` reads ``kg_paths``.  Undeclared, the traversal ran and
    its result was silently discarded.
    """
    hints = RAGState.__annotations__
    for key in ("kg_paths", "kg_cypher", "kg_traversal_failed"):
        assert key in hints, f"{key} is not declared on RAGState; LangGraph will drop it"


def test_kg_reason_node_is_registered_in_the_graph():
    """The node was implemented and exported but never added to the graph."""
    import inspect

    from app.rag.agent import graph as graph_mod

    source = inspect.getsource(graph_mod)
    assert 'add_node("kg_reason"' in source
    # …and it must sit on the plan -> retrieval path, not be orphaned.
    assert 'builder.add_edge("plan", "kg_reason")' in source


# End of test_kg_reasoner.py
