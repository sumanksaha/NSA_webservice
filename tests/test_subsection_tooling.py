"""Tests for the G6/G8 chunk-enrichment tooling (2026-08-17/18).

Covers:
  * ``app.rag.chunker._extract_clause_number`` — guarded dotted-clause
    extraction (genuine clauses vs dates/measurements/OCR residue),
  * ``scripts.backfill_subsection.derive`` — never-overwrite fill logic,
  * ``scripts.backfill_subsection.derive_clause_propagation`` — L6
    header-anchored clause propagation (G8 step 2+3),
  * ``scripts.strip_reg_section_noise.derive_strip`` — G8 step-1
    spurious-section strip,
  * ``evaluation.subsection_audit.audit`` — substantive-vs-hl1 report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag.chunker import _extract_clause_number
from evaluation.subsection_audit import audit, render
from scripts.backfill_subsection import derive, derive_clause_propagation
from scripts.strip_reg_section_noise import derive_strip


# --------------------------------------------------------------------------- #
# _extract_clause_number
# --------------------------------------------------------------------------- #
class TestExtractClauseNumber:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2.4.15 BAKERY PRODUCTS: 1. Biscuits", "2.4.15"),
            ("3.04 All processing tables", "3.04"),
            ("5.2.4 Raw food, particularly", "5.2.4"),
            ("11.4 In case, at any stage", "11.4"),
            ("2.3The floor of food processing", "2.3"),  # no space before capital
            ("5.06 Washbasin made of stainless steel", "5.06"),
            ("1.2.3 Rule heading", "1.2.3"),
        ],
    )
    def test_genuine_clauses(self, text, expected):
        assert _extract_clause_number(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "0.75 g-1.25 g",            # measurement
            "23.3.2001, for the words",  # date (4-digit year segment)
            "22.12.1997. for the words",  # date
            "0.001",                     # bare number (no capital after)
            "6.5-7.5 (1:5",              # range
            "1.2 1.2 1.2 1.2",           # OCR residue (digit after, not capital)
            "(1) Non-veg. products",     # parenthetical — not dotted
            "Section 3(1)(a) Powers",    # section prefix — not dotted
            "4.12. 1995.][* * * *",      # dotted + space + digit (amendment marker)
            "5. 4.12. 1995",             # number + dotted date
            "",
            None,
        ],
    )
    def test_rejects_false_positives(self, text):
        assert _extract_clause_number(text) is None

    def test_dotted_requires_capital_after(self):
        # A dotted clause followed by a parenthetical marker is REJECTED by
        # the [A-Z] guard — allowing '(' would reintroduce the kmc date
        # false positives ("23.3.200 (aa)").  The guard is intentionally
        # strict; dotted clauses in the corpus are followed by a capital word.
        assert _extract_clause_number("2.4.15 (1) Biscuits shall be made") is None
        assert _extract_clause_number("2.4.15: DAHI OR CURD 1. Dahi") == "2.4.15"


# --------------------------------------------------------------------------- #
# backfill derive — never overwrite
# --------------------------------------------------------------------------- #
class TestBackfillDerive:
    def test_fills_missing_subsection(self):
        ch = derive({"chunk_text": "(1)(a) Biscuits shall be", "subsection": None})
        assert ch == {"subsection": "(1)(a)"}

    def test_never_overwrites_existing_subsection(self):
        ch = derive({"chunk_text": "(1)(a) Biscuits shall be", "subsection": "(2)"})
        assert "subsection" not in ch

    def test_adds_clause_number_when_absent(self):
        ch = derive({"chunk_text": "2.4.15 BAKERY PRODUCTS", "subsection": None})
        assert ch == {"clause_number": "2.4.15"}

    def test_keeps_existing_clause_number(self):
        ch = derive({"chunk_text": "2.4.15 BAKERY PRODUCTS", "clause_number": "3.04"})
        assert "clause_number" not in ch

    def test_both_filled_independently(self):
        # dotted clause chunk with a parenthetical marker later: both written
        ch = derive({"chunk_text": "2.4.15 BAKERY PRODUCTS (1) Biscuits", "subsection": "(1)"})
        assert ch == {"clause_number": "2.4.15"}

    def test_no_change_for_plain_text(self):
        assert derive({"chunk_text": "Plain prose without markers."}) == {}


# --------------------------------------------------------------------------- #
# clause propagation (L6, G8 step 2+3)
# --------------------------------------------------------------------------- #
class TestClausePropagation:
    """``derive_clause_propagation`` — header-anchored, never-overwrite."""

    @staticmethod
    def _doc(payloads):
        # payloads must arrive ordered by chunk_index (caller contract).
        for i, pl in enumerate(payloads):
            pl.setdefault("chunk_index", i)
            pl.setdefault("chunk_id", f"p{i}")
        return {"doc-1": payloads}

    def test_fills_fragments_after_clause_boundary(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "2.4.15 BAKERY PRODUCTS", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Biscuits shall be made from", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(a) plain flour only", "hierarchy_level": 4},
        ])
        out = derive_clause_propagation(docs)
        assert out == {"p1": "2.4.15", "p2": "2.4.15"}

    def test_never_overwrites_existing_clause_number(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "2.4.15 BAKERY PRODUCTS", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Biscuits shall be", "hierarchy_level": 3, "clause_number": "3.04"},
        ])
        assert derive_clause_propagation(docs) == {}

    def test_resets_at_structural_boundary(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "2.4.15 BAKERY PRODUCTS", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Biscuits shall be", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "SCHEDULE 2", "hierarchy_level": 1},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Fees for analysis", "hierarchy_level": 2},
        ])
        out = derive_clause_propagation(docs)
        assert out == {"p1": "2.4.15"}  # p3 (after SCHEDULE) not filled

    @pytest.mark.parametrize("marker", ["PART IV", "SCHEDULE 2", "CHAPTER 3", "ANNEXURE A"])
    def test_reset_markers(self, marker):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "2.4.15 BAKERY PRODUCTS", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": marker, "hierarchy_level": 1},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Something new", "hierarchy_level": 2},
        ])
        assert derive_clause_propagation(docs) == {}

    def test_documents_do_not_bleed(self):
        docs = {
            "doc-a": [
                {"chunk_id": "a0", "chunk_index": 0, "document_id": "doc-a",
                 "document_type": "regulation", "chunk_text": "2.4.15 BAKERY PRODUCTS",
                 "hierarchy_level": 3},
            ],
            "doc-b": [
                {"chunk_id": "b0", "chunk_index": 0, "document_id": "doc-b",
                 "document_type": "regulation", "chunk_text": "(1) Unrelated prose",
                 "hierarchy_level": 3},
            ],
        }
        assert derive_clause_propagation(docs) == {}

    def test_act_documents_never_propagate(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "act",
             "chunk_text": "2.4.15 Some Act clause", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "act",
             "chunk_text": "(1) Fragment", "hierarchy_level": 3},
        ])
        assert derive_clause_propagation(docs) == {}

    def test_hl1_fragments_not_filled(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "2.4.15 BAKERY PRODUCTS", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "Address:", "hierarchy_level": 1},
        ])
        assert derive_clause_propagation(docs) == {}

    def test_before_first_boundary_not_filled(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Preamble fragment", "hierarchy_level": 2},
        ])
        assert derive_clause_propagation(docs) == {}

    def test_sub_clause_chain_gets_parent_clause(self):
        # G8 step 3: Licensing ``(2) The petty food manufacturer…`` under
        # the dotted header ``2.1.1 Registration of Petty Food Business``.
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "2.1.1 Registration of Petty Food Business", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(1) Every petty Food Business Operator shall register",
             "hierarchy_level": 3, "subsection": "(1)"},
            {"document_id": "doc-1", "document_type": "regulation",
             "chunk_text": "(2) The petty food manufacturer shall apply",
             "hierarchy_level": 3, "subsection": "(2)"},
        ])
        assert derive_clause_propagation(docs) == {"p1": "2.1.1", "p2": "2.1.1"}

    def test_document_type_scope_configurable(self):
        docs = self._doc([
            {"document_id": "doc-1", "document_type": "rule",
             "chunk_text": "1.2.3 Rule heading", "hierarchy_level": 3},
            {"document_id": "doc-1", "document_type": "rule",
             "chunk_text": "(1) Sub-rule", "hierarchy_level": 3},
        ])
        assert derive_clause_propagation(docs, ("regulation",)) == {}
        assert derive_clause_propagation(docs, ("regulation", "rule")) == {"p1": "1.2.3"}


# --------------------------------------------------------------------------- #
# strip spurious regulation section_number (G8 step 1)
# --------------------------------------------------------------------------- #
class TestStripRegSectionNoise:
    """``derive_strip`` — deletes section_number on reg/notification only."""

    def test_strips_regulation(self):
        assert derive_strip({"document_type": "regulation", "section_number": "41"}) \
            == {"section_number": None}

    def test_strips_notification(self):
        assert derive_strip({"document_type": "notification", "section_number": "4"}) \
            == {"section_number": None}

    def test_leaves_act_alone(self):
        assert derive_strip({"document_type": "act", "section_number": "3"}) is None

    def test_strips_rule_by_default(self):
        # rule chunks carry the same page-number/def-list/xref noise
        # (e.g. ``161 27960/2022/UPC-II-HO``) — included in the default scope
        # since 2026-08-18.
        assert derive_strip({"document_type": "rule", "section_number": "161"}) \
            == {"section_number": None}

    def test_scope_restrictable(self):
        # passing an explicit tuple restricts the strip; rule is untouched.
        assert derive_strip({"document_type": "rule", "section_number": "161"},
                            ("regulation", "notification")) is None

    def test_no_section_number_noop(self):
        assert derive_strip({"document_type": "regulation", "section_number": None}) is None
        assert derive_strip({"document_type": "regulation"}) is None

    def test_configurable_types(self):
        assert derive_strip({"document_type": "circular", "section_number": "1"},
                            ("circular",)) == {"section_number": None}


# --------------------------------------------------------------------------- #
# audit — substantive vs hl1
# --------------------------------------------------------------------------- #
class TestAudit:
    def _payloads(self):
        return [
            # substantive with subsection (fssai)
            {"act_name": "Food Safety and Standards Act, 2006", "section_number": "3",
             "subsection": "(1)", "hierarchy_level": 2, "chunk_text": "(1) x"},
            # substantive without subsection but dotted clause (fssai)
            {"act_name": "Food Safety and Standards Act, 2006", "section_number": None,
             "hierarchy_level": 3, "chunk_text": "2.4.15 BAKERY PRODUCTS"},
            # hl1 header (no subsection)
            {"act_name": "Food Safety and Standards Act, 2006", "section_number": None,
             "hierarchy_level": 1, "chunk_text": "Address:"},
            # substantive, no subsection, no marker (env)
            {"act_name": "Environment (Protection) Act, 1986", "section_number": "5",
             "hierarchy_level": 2, "chunk_text": "Plain prose"},
        ]

    def test_substantive_vs_hl1_split(self):
        rep = audit(self._payloads())
        assert rep["n_chunks"] == 4
        assert rep["subsection_overall"] == 1
        assert rep["pct_overall"] == 25.0
        # substantive = 3 chunks (hl1 excluded), 1 with subsection
        assert rep["substantive_chunks"] == 3
        assert rep["substantive_with_subsection"] == 1
        assert rep["pct_substantive"] == round(1 / 3 * 100, 1)
        assert rep["hl1_chunks"] == 1

    def test_clause_number_recovery_detected(self):
        rep = audit(self._payloads())
        assert rep["clause_number_recovery"]["chunks_gaining"] == 1

    def test_render_is_ascii_safe(self):
        rep = audit(self._payloads())
        text = render(rep)
        text.encode("cp1252")  # must not raise — the crash the G1 harness fixed

    def test_json_report_writes(self, tmp_path):
        rep = audit(self._payloads())
        out = tmp_path / "audit.json"
        out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
        assert json.loads(out.read_text(encoding="utf-8"))["n_chunks"] == 4
