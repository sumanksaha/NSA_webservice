"""Tests for the G6 subsection coverage tooling (2026-08-17).

Covers:
  * ``app.rag.chunker._extract_clause_number`` — guarded dotted-clause
    extraction (genuine clauses vs dates/measurements/OCR residue),
  * ``scripts.backfill_subsection.derive`` — never-overwrite fill logic,
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
from scripts.backfill_subsection import derive


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
