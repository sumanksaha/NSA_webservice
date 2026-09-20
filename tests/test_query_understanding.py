"""Query-understanding seam: one parse, every view agrees.

The interface is the test surface — these tests pin that
``understand()`` returns exactly what the individual detectors return, so
callers can migrate to the seam without behavior drift.
"""

from app.rag.retrieval import QueryClassifier, QueryParser, understand
from app.rag.retrieval.identifier import detect_act, detect_section, identifier_query
from app.rag.retrieval.legal_query_classifier import (
    classify_legal_query,
    classify_with_confidence,
    get_config,
)
from app.rag.retrieval.query_classifier import (
    AuthorityQueryParser,
    CaseLawQueryParser,
    JurisdictionQueryParser,
)

QUERIES = [
    "What does Section 55 say about penalty?",
    "Penalty for selling unsafe food under the FSS Act?",
    "What did the Supreme Court say in 2023 SCC 123?",
    "Ministry of Health notification on food labeling",
    "Maharashtra food safety rules for licensing",
    "What is food safety?",
    "Section 55(2) of the FSS Act and Section 56",
    "Has Section 31 been amended since 2020?",
    "",
    "u/s 32 improvement notice procedure",
]


def test_legacy_view_matches_classifier_and_parser():
    for q in QUERIES:
        u = understand(q)
        assert u.query_type == QueryClassifier().classify(q)
        assert u.parsed_filters == (QueryParser().parse(q, u.query_type) or {})


def test_legal_view_matches_classifier():
    for q in QUERIES:
        u = understand(q)
        assert u.legal_type == classify_legal_query(q)
        assert u.legal_confidence == classify_with_confidence(q)[1]
        assert u.rerank_config == get_config(u.legal_type)


def test_entity_views_match_detectors():
    for q in QUERIES:
        u = understand(q)
        assert u.act == detect_act(q)
        assert (u.section, u.subsection) == detect_section(q)
        assert u.authority == AuthorityQueryParser.parse(q).get("authority")
        case_law = CaseLawQueryParser.parse(q)
        assert u.citation == case_law.get("citation")
        assert u.court == case_law.get("court")
        jurisdiction = JurisdictionQueryParser.parse(q)
        assert u.jurisdiction == jurisdiction.get("jurisdiction")
        assert u.jurisdiction_level == jurisdiction.get("level")


def test_identifier_view_matches_builder():
    for q in QUERIES:
        u = understand(q)
        text, meta = identifier_query(q)
        assert u.identifier_query == text
        assert u.identifier_meta == meta


def test_profile_weights_match_direct_lookup():
    from app.rag.planning.profiles import ProfileManager

    u = understand("What does Section 55 say?")
    assert u.profile_weights("definition") == ProfileManager().get_query_profile("standard").rerank_weights_for(
        "definition"
    )
    # Unknown profile falls back to standard, as the historical call site did.
    assert u.profile_weights("definition", "no-such-profile") == u.profile_weights("definition")


def test_empty_query_is_safe():
    u = understand("")
    assert u.query_type == QueryClassifier().classify("")
    assert u.legal_type == "ambiguous"
    assert u.act is None and u.section is None
    assert u.parsed_filters == {}
    assert u.identifier_query is None
