"""Query-understanding seam: one parse, every view agrees.

The interface is the test surface — these tests pin that
``understand()`` returns exactly what the individual detectors return, so
callers can migrate to the seam without behavior drift.
"""

from app.rag.retrieval import QueryClassifier, QueryParser, understand
from app.rag.retrieval.identifier import detect_act, detect_section, identifier_query
from app.rag.retrieval.legal_query_classifier import (
    LEGAL_QUERY_TYPES,
    classify_legal_query,
    classify_with_confidence,
    get_config,
)
from app.rag.retrieval.query_classifier import (
    AuthorityQueryParser,
    CaseLawQueryParser,
    JurisdictionQueryParser,
    QueryType,
    SectionQueryParser,
)
from app.rag.retrieval.query_classifier import (
    QueryParser as QP,
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

# Expanded query set covering all new QueryType values
EXPANDED_QUERIES = [
    ("What is the definition of food safety?", QueryType.DEFINITION),
    ("State the prohibition on selling adulterated food", QueryType.PROHIBITION),
    ("What authority is empowered to inspect?", QueryType.AUTHORITY),
    ("How to get FSSAI registration?", QueryType.PROCEDURE),
    ("What are the penalties for non-compliance?", QueryType.PENALTY),
    ("How does Section 33 read with Section 34?", QueryType.SECTION_LOOKUP),
    ("As provided under the Act, what applies?", QueryType.CROSS_REFERENCE),
    ("What is the FSS Act?", QueryType.PROVISION_SEARCH),
]

# Queries covering all 14 legal query types
LEGAL_TYPE_QUERIES = [
    ("What is the penalty for selling unsafe food?", "penalty"),
    ("What offence is committed by adulteration?", "offence"),
    ("What is prohibited under Section 31?", "prohibition"),
    ("Are there exceptions to the FSS Act?", "exception"),
    ("Who has the authority to oversee food safety?", "authority"),
    ("Read with Section 31", "cross-reference"),
    ("How long is the retention period?", "temporal"),
    ("Define 'food safety'", "definition"),
    ("What is the appeal procedure?", "procedure"),
    ("What does Section 55 say?", "direct provision"),
    ("What is my duty as a manufacturer?", "obligation"),
    ("When will the officer conduct seizure?", "enforcement"),
    ("Is the evidence sufficient?", "insufficient-evidence"),
]


def test_legacy_view_matches_classifier_and_parser():
    for q in QUERIES:
        u = understand(q)
        assert u.query_type == QueryClassifier().classify(q)
        assert u.parsed_filters == (QueryParser().parse(q, u.query_type) or {})


def test_expanded_queries_classify_correctly():
    """Verify all new QueryType patterns produce the expected classification."""
    for query, expected_type in EXPANDED_QUERIES:
        u = understand(query)
        assert u.query_type == expected_type, f"Query '{query}' classified as {u.query_type}, expected {expected_type}"


def test_legal_view_matches_classifier():
    for q in QUERIES:
        u = understand(q)
        assert u.legal_type == classify_legal_query(q)
        assert u.legal_confidence == classify_with_confidence(q)[1]
        assert u.rerank_config == get_config(u.legal_type)


def test_legal_types_cover_all_vocabulary():
    """Verify queries matching legal classifier keywords produce non-ambiguous types."""
    for query, expected_type in LEGAL_TYPE_QUERIES:
        result = classify_legal_query(query)
        assert result == expected_type, f"Query '{query}' classified as '{result}', expected '{expected_type}'"
        assert result in LEGAL_QUERY_TYPES, f"'{result}' not in LEGAL_QUERY_TYPES"


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
        "definition",
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


def test_whitespace_only_query_is_safe():
    """Verify whitespace-only queries are handled gracefully."""
    u = understand("   ")
    assert u.query_type == QueryType.GENERAL_QA
    assert u.legal_type == "ambiguous"
    assert u.legal_confidence == 0.0
    assert u.parsed_filters == {}
    assert u.identifier_query is None


def test_none_query_is_safe():
    """Verify None query is handled gracefully (empty-safe)."""
    u = understand("")
    assert u.query == ""


def test_normalize_query_type_canonicalizes_both_vocabularies():
    from app.rag.retrieval import normalize_query_type

    assert normalize_query_type("cross-reference") == "cross_reference"
    assert normalize_query_type("cross_reference") == "cross_reference"
    assert normalize_query_type("direct provision") == "direct_provision"
    assert normalize_query_type("general_qa") == "general"
    assert normalize_query_type("general") == "general"
    assert normalize_query_type(None) == "general"
    assert normalize_query_type("  ") == "general"


def test_effective_query_type_prefers_specific_legal_view():
    from app.rag.retrieval import effective_query_type

    assert effective_query_type("general_qa", "penalty") == "penalty"
    assert effective_query_type("provision_search", "cross-reference") == "cross_reference"
    assert effective_query_type("general_qa", "ambiguous") == "general"
    assert effective_query_type("section_lookup", "ambiguous") == "section_lookup"
    assert effective_query_type("general_qa", "") == "general"


def test_jurisdiction_abbreviation_matching():
    """Verify state abbreviations are resolved to full names."""
    u = understand("Maharashtra food safety rules")
    assert u.jurisdiction == "Maharashtra"
    u = understand("Food safety rules in UP")
    assert u.jurisdiction == "Uttar Pradesh"
    u = understand("Rules in MH")
    assert u.jurisdiction == "Maharashtra"


def test_authority_fuzzy_matching():
    """Verify authority fuzzy matching works for partial names."""
    u = understand("Ministry of Health notification on food labeling")
    assert u.authority == "Ministry of Health"
    u = understand("MoHFW guidelines for food safety")
    assert u.authority == "MoHFW"


def test_form_reference_detection():
    """Verify form references are detected in the QueryUnderstanding."""
    u = understand("Which form does the FSO give as a notice?")
    assert u.form == "v"
    assert u.form_query == "Form V"
    u = understand("How can the FBO appeal using Form VIII?")
    assert u.form == "viii"


def test_query_parser_dispatch_all_types():
    """Verify QueryParser dispatches to the right parser for all query types."""
    test_cases = [
        (QueryType.SECTION_LOOKUP, SectionQueryParser),
        (QueryType.PENALTY, SectionQueryParser),
        (QueryType.AUTHORITY, AuthorityQueryParser),
        (QueryType.PROCEDURE, SectionQueryParser),
        (QueryType.PROVISION_SEARCH, AuthorityQueryParser),
        (QueryType.CASE_LAW, CaseLawQueryParser),
        (QueryType.JURISDICTION, JurisdictionQueryParser),
        (QueryType.GENERAL_QA, AuthorityQueryParser),
    ]
    for query_type, expected_parser in test_cases:
        assert QP._PARSERS.get(query_type) == expected_parser, (
            f"QueryType {query_type} not mapped to {expected_parser.__name__}"
        )


def test_amendment_classification():
    """Verify amendment queries are classified correctly."""
    u = understand("Has Section 31 been amended since 2020?")
    assert u.query_type == QueryType.AMENDMENT_QUERY
    assert u.parsed_filters.get("section_number") == "31"


def test_section_lookup_classification():
    """Verify section lookup queries are classified correctly."""
    u = understand("What does Section 55 say?")
    assert u.query_type == QueryType.SECTION_LOOKUP
    assert u.section == "55"
