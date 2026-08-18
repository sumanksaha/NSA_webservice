"""Tests for the RAG query classifier and structured query parsers.

Phase 1, Day 1 — 12 tests covering QueryType classification and each parser
(section, authority, case law, jurisdiction).

Follows the test pattern from ``tests/test_ai_assistant.py``: no DB required
for pure-logic components, but uses ``create_app()`` context where needed.
"""

from __future__ import annotations

from app.rag.retrieval.query_classifier import (
    AuthorityQueryParser,
    CaseLawQueryParser,
    JurisdictionQueryParser,
    QueryClassifier,
    QueryParser,
    QueryType,
    SectionQueryParser,
)


class TestQueryClassifier:
    """Tests for QueryClassifier — rule-based query classification."""

    def setup_method(self):
        self.classifier = QueryClassifier()

    def test_empty_query_defaults_to_general_qa(self):
        assert self.classifier.classify("") == QueryType.GENERAL_QA
        assert self.classifier.classify("   ") == QueryType.GENERAL_QA

    def test_section_lookup(self):
        assert self.classifier.classify("What does Section 55 say?") == QueryType.SECTION_LOOKUP

    def test_section_lookup_abbreviation(self):
        assert self.classifier.classify("What is u/s 37?") == QueryType.SECTION_LOOKUP

    def test_section_lookup_sec_dot(self):
        assert self.classifier.classify("What is sec. 52?") == QueryType.SECTION_LOOKUP

    def test_case_law(self):
        assert self.classifier.classify("What did the Supreme Court decide?") == QueryType.CASE_LAW

    def test_case_law_citation(self):
        assert self.classifier.classify("Tell me about 2023 SCC 123") == QueryType.CASE_LAW

    def test_provision_search(self):
        assert self.classifier.classify("FSS Act food safety provisions") == QueryType.PROVISION_SEARCH

    def test_amendment_query(self):
        assert self.classifier.classify("When was Section 55 amended?") == QueryType.AMENDMENT_QUERY

    def test_general_qa(self):
        assert self.classifier.classify("What is food safety?") == QueryType.GENERAL_QA

    def test_priority_amendment_over_section(self):
        """Amendment keywords should take priority over section keywords."""
        assert self.classifier.classify("Section 55 amendment") == QueryType.AMENDMENT_QUERY


class TestSectionQueryParser:
    """Tests for SectionQueryParser — extract section numbers from queries."""

    def test_single_section(self):
        result = SectionQueryParser.parse("What does Section 55 say?")
        assert result["section_number"] == "55"

    def test_section_with_subsection(self):
        result = SectionQueryParser.parse("Section 55(2) of the FSS Act")
        assert result["section_number"] == "55"
        assert result["subsection"] == "2"

    def test_multiple_sections(self):
        result = SectionQueryParser.parse("Sections 55, 56 and 58")
        assert result["section_numbers"] == ["55", "56", "58"]

    def test_u_s_abbreviation(self):
        result = SectionQueryParser.parse("What is u/s 37?")
        assert result["section_number"] == "37"

    def test_sec_dot_abbreviation(self):
        result = SectionQueryParser.parse("What is sec. 52?")
        assert result["section_number"] == "52"

    def test_no_section_found(self):
        result = SectionQueryParser.parse("Tell me about food safety")
        assert result == {}


class TestAuthorityQueryParser:
    """Tests for AuthorityQueryParser — extract issuing authority from queries."""

    def test_known_authority(self):
        result = AuthorityQueryParser.parse("Ministry of Health notification")
        assert result["authority"] == "Ministry of Health"

    def test_fssai_authority(self):
        result = AuthorityQueryParser.parse("FSSAI regulation on labeling")
        assert result["authority"] == "FSSAI"

    def test_no_authority(self):
        result = AuthorityQueryParser.parse("What is food safety?")
        assert result == {}


class TestCaseLawQueryParser:
    """Tests for CaseLawQueryParser — extract case citations from queries."""

    def test_case_citation(self):
        result = CaseLawQueryParser.parse("2023 SCC 123 Supreme Court")
        assert "citation" in result
        assert "2023" in result["citation"]
        assert result["court"] == "Supreme Court"

    def test_no_case_citation(self):
        result = CaseLawQueryParser.parse("Section 55 of the Act")
        assert result == {}


class TestJurisdictionQueryParser:
    """Tests for JurisdictionQueryParser — extract jurisdiction from queries."""

    def test_state_jurisdiction(self):
        result = JurisdictionQueryParser.parse("Maharashtra food safety rules")
        assert result["jurisdiction"] == "Maharashtra"
        assert result["level"] == "state"

    def test_central_jurisdiction(self):
        result = JurisdictionQueryParser.parse("Central Government notification")
        assert result["jurisdiction"] == "India"
        assert result["level"] == "central"

    def test_no_jurisdiction(self):
        result = JurisdictionQueryParser.parse("Section 55")
        assert result == {}


class TestQueryParserDispatcher:
    """Tests for QueryParser — dispatches to the correct sub-parser."""

    def test_section_type_dispatches_to_section_parser(self):
        parser = QueryParser()
        result = parser.parse("What does Section 55 say?", QueryType.SECTION_LOOKUP)
        assert result["section_number"] == "55"

    def test_provision_type_dispatches_to_authority_parser(self):
        parser = QueryParser()
        result = parser.parse("FSSAI regulation on labeling", QueryType.PROVISION_SEARCH)
        assert "authority" in result

    def test_case_law_type_dispatches_to_case_law_parser(self):
        parser = QueryParser()
        result = parser.parse("2023 SCC 123", QueryType.CASE_LAW)
        assert "citation" in result
