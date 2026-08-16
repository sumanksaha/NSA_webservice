"""Unit tests for legal identity, hierarchy, and section-chunk relationships."""

from dataclasses import dataclass

import pytest

from app.rag.retrieval.legal_identity import LegalIdentity, parse_legal_identity
from app.rag.retrieval.legal_hierarchy import (
    adjacent_section,
    exact_section_match,
    hierarchy_depth,
    hierarchy_proximity,
    parent_child,
    parse_section_chain,
    same_act,
    same_section_family,
    sibling,
    subsection_relationship,
)


@dataclass
class FakeChunk:
    chunk_id: str = "test"
    text: str = ""
    section_number: str | None = None
    document_title: str = ""
    act_name: str = ""
    document_type: str = ""
    authority: str = ""
    hierarchy_level: int = 3
    score: float = 1.0


class TestLegalIdentity:
    def test_act_extraction(self):
        chunk = FakeChunk(
            text="Some text about food safety",
            section_number="31",
            act_name="Food Safety and Standards Act, 2006",
            document_title="Food Safety and Standards Act, 2006",
        )
        ident = parse_legal_identity(chunk)
        assert ident.act == "Food Safety and Standards Act, 2006"

    def test_section_extraction(self):
        chunk = FakeChunk(
            text="Section 31 is about something",
            section_number="31",
            act_name="Food Safety and Standards Act, 2006",
        )
        ident = parse_legal_identity(chunk)
        assert ident.section == "31"

    def test_subsection_extraction(self):
        chunk = FakeChunk(
            text="Section 31(2)(a) clause text",
            section_number="31(2)(a)",
            act_name="Food Safety and Standards Act, 2006",
        )
        ident = parse_legal_identity(chunk)
        assert ident.section == "31"
        assert ident.subsection == ["2"]
        assert ident.clause == ["a"]

    def test_subsection_extraction_3_levels(self):
        chunk = FakeChunk(
            text="Section 31(2)(a)(iii) clause text",
            section_number="31(2)(a)(iii)",
            act_name="Food Safety and Standards Act, 2006",
        )
        ident = parse_legal_identity(chunk)
        assert ident.section == "31"
        assert ident.subsection == ["2"]
        assert ident.clause == ["a", "iii"]

    def test_canonical_id_full(self):
        ident = LegalIdentity(
            act="Food Safety and Standards Act, 2006",
            section="31",
            subsection=["2"],
            clause=["a"],
        )
        cid = ident.canonical_id()
        assert "Food Safety and Standards Act, 2006" in cid
        assert "31(2)(a)" in cid

    def test_canonical_id_section_only(self):
        ident = LegalIdentity(section="31")
        assert ident.canonical_id() == "31"

    def test_canonical_id_act_only(self):
        ident = LegalIdentity(act="FSS Act, 2006")
        assert ident.canonical_id() == "FSS Act, 2006"

    def test_canonical_id_unknown(self):
        ident = LegalIdentity()
        assert ident.canonical_id() == "UNKNOWN"

    def test_tolerates_missing_fields(self):
        chunk = FakeChunk(text="no section info at all", act_name="")
        ident = parse_legal_identity(chunk)
        assert ident.act is None or ident.act == ""
        assert ident.section is None

    def test_does_not_fabricate_identifiers(self):
        """Empty text must produce empty identity, never fake identifiers."""
        chunk = FakeChunk(text="", act_name="", section_number=None)
        ident = parse_legal_identity(chunk)
        assert ident.act is None or ident.act == ""
        assert ident.section is None or ident.section == ""


class TestLegalHierarchy:
    def test_parse_chain_simple(self):
        assert parse_section_chain("31") == ["31"]

    def test_parse_chain_subsection(self):
        assert parse_section_chain("31(2)") == ["31", "2"]

    def test_parse_chain_clause(self):
        assert parse_section_chain("31(2)(a)") == ["31", "2", "a"]

    def test_parse_chain_deep(self):
        assert parse_section_chain("31(2)(a)(iii)") == ["31", "2", "a", "iii"]

    def test_parse_chain_none(self):
        assert parse_section_chain(None) == []

    def test_parse_chain_empty(self):
        assert parse_section_chain("") == []

    def test_hierarchy_depth_root(self):
        assert hierarchy_depth(None) == 1
        assert hierarchy_depth("") == 1

    def test_hierarchy_depth_section(self):
        assert hierarchy_depth("31") == 3

    def test_hierarchy_depth_subsection(self):
        assert hierarchy_depth("31(2)") == 4

    def test_hierarchy_depth_clause(self):
        assert hierarchy_depth("31(2)(a)") == 5

    def test_exact_match_true(self):
        assert exact_section_match("31(2)(a)", "31(2)(a)")

    def test_exact_match_false(self):
        assert not exact_section_match("31(2)", "31(2)(a)")

    def test_same_section_family(self):
        assert same_section_family("31(2)", "31(2)(a)")
        assert same_section_family("31", "31(2)(a)")
        assert not same_section_family("31", "32")

    def test_parent_child(self):
        assert parent_child("31", "31(2)")
        assert parent_child("31(2)", "31")
        assert not parent_child("31", "32")

    def test_sibling(self):
        assert sibling("31(2)", "31(3)")
        assert sibling("31(2)(a)", "31(2)(b)")

    def test_adjacent_section(self):
        assert adjacent_section("31", "32")
        assert adjacent_section("32", "31")
        assert not adjacent_section("31", "33")

    def test_subsection_relationship(self):
        assert subsection_relationship("31", "31(2)")
        assert subsection_relationship("31(2)(a)", "31(2)")
        assert not subsection_relationship("31", "32")

    def test_same_act(self):
        assert same_act("FSS Act, 2006", "FSS Act, 2006")
        assert not same_act("FSS Act, 2006", "EPA, 1986")
        assert not same_act(None, "FSS Act, 2006")

    def test_hierarchy_proximity_exact(self):
        assert hierarchy_proximity("31(2)(a)", "31(2)(a)") == 1.0

    def test_hierarchy_proximity_parent_child(self):
        assert hierarchy_proximity("31(2)", "31(2)(a)") == 0.75
        assert hierarchy_proximity("31", "31(2)") == 0.75

    def test_hierarchy_proximity_same_family(self):
        assert hierarchy_proximity("31(2)", "31(3)") == 0.5

    def test_hierarchy_proximity_adjacent(self):
        assert hierarchy_proximity("31", "32") == 0.25

    def test_hierarchy_proximity_unrelated(self):
        assert hierarchy_proximity("31", "99") == 0.0
        assert hierarchy_proximity(None, None) == 0.0
