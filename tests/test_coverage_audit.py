"""Tests for the corpus-wide identity coverage audit (2026-08-18).

Covers ``evaluation.coverage_audit`` — the identity definition, gap
classification and the aggregate report used by ``docs/COVERAGE_COMPLETENESS.md``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.coverage_audit import _gap_bucket, audit, is_identified


# --------------------------------------------------------------------------- #
# identity definition (G8 semantics)
# --------------------------------------------------------------------------- #
class TestIsIdentified:
    def test_act_identified_by_section(self):
        assert is_identified({"document_type": "act", "section_number": "3"})
        assert not is_identified({"document_type": "act", "clause_number": "2.4.15"})
        assert not is_identified({"document_type": "act"})

    def test_regulation_identified_by_clause(self):
        assert is_identified({"document_type": "regulation", "clause_number": "2.4.15"})
        # a section_number on a regulation is noise (G8) — never identity
        assert not is_identified({"document_type": "regulation", "section_number": "36"})

    def test_rule_notification_circular_use_clause(self):
        for dt in ("rule", "notification", "circular"):
            assert is_identified({**{"document_type": dt}, "clause_number": "4"})
            assert not is_identified({"document_type": dt, "section_number": "4"})

    def test_unknown_accepts_either(self):
        assert is_identified({"document_type": "unknown", "clause_number": "1"})
        assert is_identified({"document_type": "unknown", "section_number": "1"})


# --------------------------------------------------------------------------- #
# gap classification
# --------------------------------------------------------------------------- #
class TestGapBucket:
    def test_paren_fragment(self):
        assert _gap_bucket({"chunk_text": "(a) manufactures or sells any article of food"}) == "paren_fragment"

    def test_gazette_header(self):
        assert _gap_bucket({"chunk_text": "40 THE GAZETTE OF INDIA : EXTRAORDINARY [PART II"}) == "gazette_header"

    def test_rule_header(self):
        assert _gap_bucket({"document_type": "rule", "chunk_text": "4 Rural areas 1 With a population"}) == "rule_header"

    def test_dotted_unstamped(self):
        assert _gap_bucket({"chunk_text": "2.4.15 BAKERY PRODUCTS"}) == "dotted_unstamped"

    def test_prose(self):
        assert _gap_bucket({"chunk_text": "The remaining provisions of this Act shall apply"}) == "prose"

    def test_empty_text(self):
        assert _gap_bucket({"chunk_text": "   "}) == "empty_text"


# --------------------------------------------------------------------------- #
# aggregate audit over synthetic payloads
# --------------------------------------------------------------------------- #
class TestAudit:
    def _payloads(self):
        return [
            # act doc: section-stamped chunk + fillable paren fragment
            {"document_id": "d1", "document_type": "act", "hierarchy_level": 2,
             "chunk_text": "3. Definitions", "section_number": "3", "legal_domain": "BUSINESS_CIVIL"},
            {"document_id": "d1", "document_type": "act", "hierarchy_level": 3,
             "chunk_text": "(a) \"adulterant\" means", "legal_domain": "BUSINESS_CIVIL"},
            # regulation with clause + unstamped fragment
            {"document_id": "d2", "document_type": "regulation", "hierarchy_level": 3,
             "chunk_text": "2.4.15 BAKERY PRODUCTS", "clause_number": "2.4.15", "legal_domain": "FOOD_SAFETY"},
            {"document_id": "d2", "document_type": "regulation", "hierarchy_level": 3,
             "chunk_text": "(1) Biscuits shall be made", "legal_domain": "FOOD_SAFETY"},
            # hl1 noise — never counted as missing
            {"document_id": "d3", "document_type": "rule", "hierarchy_level": 1,
             "chunk_text": "Address:", "legal_domain": "FOOD_SAFETY"},
            # rule with noise section_number only — NOT identified (G8)
            {"document_id": "d4", "document_type": "rule", "hierarchy_level": 2,
             "chunk_text": "6 Summary of the mechanisms", "section_number": "6", "legal_domain": "FOOD_SAFETY"},
        ]

    def test_counts(self):
        rep = audit(self._payloads())
        assert rep["n_chunks"] == 6
        # identified: act 1 (sec) + regulation 1 (clause) = 2; rule sec is noise
        assert rep["identified"] == 2
        # substantive = 5 (hl1 excluded); identified substantive = 2
        assert rep["substantive_chunks"] == 5
        assert rep["substantive_identified"] == 2

    def test_gap_buckets(self):
        rep = audit(self._payloads())
        buckets = {g["bucket"]: g["chunks"] for g in rep["gap_buckets"]}
        assert buckets["paren_fragment"] == 2  # d1 (a) + d2 (1)
        assert buckets["rule_header"] == 1

    def test_per_collection(self):
        rep = audit(self._payloads())
        assert rep["by_collection"]["commercial_legal_768"]["chunks"] == 2
        assert rep["by_collection"]["fssai_legal_768"]["chunks"] == 3

    def test_json_serializable(self):
        rep = audit(self._payloads())
        json.dumps(rep)  # must not raise
