"""Tests for COVERAGE_COMPLETENESS P1 + P2 (2026-08-18).

Covers:
  * ``scripts.backfill_payload_identity.header_trust_number`` /
    ``amendment_anchor`` / ``derive_l7`` — L7 header-trust correction and
    amendment-anchor propagation (the paren-fragment gap in consolidated and
    amendment acts),
  * ``scripts.backfill_document_title.derive_title`` / ``derive_changes`` —
    P1 document_title backfill from ``document_uri`` filenames.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.backfill_document_title import derive_changes, derive_title
from scripts.backfill_payload_identity import amendment_anchor, derive_l7, header_trust_number


# --------------------------------------------------------------------------- #
# header-trust detection
# --------------------------------------------------------------------------- #
class TestHeaderTrustNumber:
    def test_genuine_header(self):
        assert header_trust_number("50. Prosecution. If, from the report under section 49") == 50

    def test_letter_suffix_header(self):
        assert header_trust_number("77A. Cognizance of offences. No court, other than") == 77

    def test_num_space_capital_rejected(self):
        # ``N Word`` (space) form is intentionally NOT header-trust: it matches
        # page-number fragments and title pages (``3 THE AIR …``, ``2 Stoppage
        # in transit``) that L4's range-validated analysis later overrides
        # (verified: 29/31 L4-vs-L7 conflicts were space-form, 2026-08-18).
        assert header_trust_number("33 THE FIRST SCHEDULE") is None
        assert header_trust_number("3 THE AIR (PREVENTION AND CONTROL OF POLLUTION) ACT, 1981") is None
        assert header_trust_number("2 Stoppage in transit 50. Right of stoppage in transit") is None

    def test_short_title(self):
        assert header_trust_number("1. Short title and commencement") == 1

    def test_gazette_page_header_rejected(self):
        assert header_trust_number("40 THE GAZETTE OF INDIA : EXTRAORDINARY [PART II") is None

    def test_amendment_footnote_rejected(self):
        assert header_trust_number("2. Subs. by s. 21, ibid., for section 69 (w.e.f. 1-4-2022).") is None
        assert header_trust_number("3. Ins. by Act 18 of 2018, s. 2 (w.e.f. 1-10-2018).") is None

    def test_dotted_clause_rejected(self):
        assert header_trust_number("5.06 Washbasin made of stainless steel") is None

    def test_amendment_schedule_residue_rejected(self):
        assert header_trust_number("1. 1870 7 The Court- (A) In section 34") is None

    def test_paren_fragment_rejected(self):
        assert header_trust_number("(a) manufactures or sells any article of food") is None

    def test_arrangement_page_rejected(self):
        assert header_trust_number("ARRANGEMENT OF SECTIONS") is None

    def test_empty(self):
        assert header_trust_number("") is None
        assert header_trust_number(None) is None


# --------------------------------------------------------------------------- #
# amendment-anchor rule
# --------------------------------------------------------------------------- #
class TestAmendmentAnchor:
    def test_stamped_and_named_in_text(self):
        pl = {"hierarchy_level": 3, "section_number": "34",
              "chunk_text": "In section 34, for sub-section (3), the following shall be substituted"}
        assert amendment_anchor(pl, None) == "34"

    def test_backwards_reference_never_resets(self):
        pl = {"hierarchy_level": 3, "section_number": "2",
              "chunk_text": "as defined in section 2 of the principal Act"}
        assert amendment_anchor(pl, "34") is None

    def test_first_anchor_accepts_any(self):
        pl = {"hierarchy_level": 3, "section_number": "2",
              "chunk_text": "as defined in section 2 of the principal Act"}
        assert amendment_anchor(pl, None) == "2"

    def test_stamp_not_named_in_text_rejected(self):
        pl = {"hierarchy_level": 3, "section_number": "92",
              "chunk_text": "In section 34, for sub-section (3)"}
        assert amendment_anchor(pl, None) is None

    def test_hl1_rejected(self):
        pl = {"hierarchy_level": 1, "section_number": "17",
              "chunk_text": "exempted under section 17 of the Act"}
        assert amendment_anchor(pl, None) is None

    def test_unstamped_rejected(self):
        pl = {"hierarchy_level": 3, "chunk_text": "In section 34, for sub-section (3)"}
        assert amendment_anchor(pl, None) is None


# --------------------------------------------------------------------------- #
# L7 derivation
# --------------------------------------------------------------------------- #
def _doc(payloads):
    for i, pl in enumerate(payloads):
        pl.setdefault("chunk_index", i)
        pl.setdefault("chunk_id", f"p{i}")
        pl.setdefault("document_id", "doc-1")
        pl.setdefault("document_type", "act")
    return {"doc-1": payloads}


class TestDeriveL7:
    def test_header_trust_correction_and_fill(self):
        # LLP-style: header mis-stamped by in-text cross-ref; bodies follow.
        docs = _doc([
            {"chunk_text": "50. Prosecution. If, from the report under section 49, it appears",
             "section_number": "49", "hierarchy_level": 3},
            {"chunk_text": "(a) the contravention is a continuing one", "hierarchy_level": 4},
            {"chunk_text": "(b) the Designated Partner fails to", "hierarchy_level": 4},
        ])
        corrections, fills = derive_l7(docs, {})
        assert corrections == {"p0": "50"}
        assert fills == {"p1": "50", "p2": "50"}

    def test_never_overwrite_stamped_fragment(self):
        docs = _doc([
            {"chunk_text": "50. Prosecution. If, from the report under section 49", "section_number": "49",
             "hierarchy_level": 3},
            {"chunk_text": "(a) fragment already stamped", "section_number": "7", "hierarchy_level": 4},
            {"chunk_text": "(b) unstamped fragment", "hierarchy_level": 4},
        ])
        _c, fills = derive_l7(docs, {})
        assert fills == {"p2": "50"}  # p1 keeps its own stamp

    def test_amendment_mode_ascending_anchors(self):
        docs = _doc([
            {"chunk_text": "In section 34, for sub-section (3), the following shall be substituted",
             "section_number": "34", "hierarchy_level": 4},
            {"chunk_text": "(2) Every order made under sub-section (1) shall", "hierarchy_level": 3},
            {"chunk_text": "as defined in section 2 of the principal Act, the expression",
             "section_number": "2", "hierarchy_level": 4},
            {"chunk_text": "(4) The court which makes a direction under sub-section (3)",
             "hierarchy_level": 3},
        ])
        _c, fills = derive_l7(docs, {})
        assert fills == {"p1": "34", "p3": "34"}  # p2 (sec=2, backwards) skipped

    def test_l4_headers_disable_amendment_mode(self):
        docs = _doc([
            {"chunk_text": "1. Short title", "section_number": "1", "hierarchy_level": 2},
            {"chunk_text": "In section 34, for sub-section (3)", "section_number": "34",
             "hierarchy_level": 4},
            {"chunk_text": "(2) fragment after a cross-ref stamp", "hierarchy_level": 3},
        ])
        l4 = {"p0": ["1"]}  # L4 boundary exists -> no amendment anchors
        _c, fills = derive_l7(docs, l4)
        # p1 is stamped (not a boundary since has_l4 -> header-trust: "In section 34"
        # does not start with a number -> skipped); p2 unstamped after running=1
        assert fills == {"p2": "1"}

    def test_criminal_skipped(self):
        docs = _doc([
            {"chunk_text": "147. AjoinsaninsurrectionagainsttheGovernmentofIndia", "section_number": "147",
             "hierarchy_level": 3, "legal_domain": "CRIMINAL"},
            {"chunk_text": "(2) fragment", "hierarchy_level": 3},
        ])
        assert derive_l7(docs, {}) == ({}, {})

    def test_non_act_skipped(self):
        docs = _doc([
            {"chunk_text": "2.4.15 BAKERY PRODUCTS", "clause_number": "2.4.15",
             "hierarchy_level": 3, "document_type": "regulation"},
            {"chunk_text": "(1) Biscuits shall be made", "hierarchy_level": 3},
        ])
        assert derive_l7(docs, {}) == ({}, {})

    def test_hl1_not_filled(self):
        docs = _doc([
            {"chunk_text": "50. Prosecution.", "section_number": "49", "hierarchy_level": 3},
            {"chunk_text": "Address:", "hierarchy_level": 1},
        ])
        _c, fills = derive_l7(docs, {})
        assert fills == {}

    def test_before_first_boundary_not_filled(self):
        docs = _doc([
            {"chunk_text": "(1) Preamble fragment", "hierarchy_level": 2},
        ])
        assert derive_l7(docs, {}) == ({}, {})

    def test_l4_verified_chunks_are_not_corrected(self):
        # ``39D.``/``76A.``-style headers where L4's range-validated analysis
        # disagrees: L7 must not fight L4 (verified 2026-08-18 — convergence).
        # The correction is skipped, but the chunk is still a boundary (its
        # fragments inherit the L4-verified section).
        docs = _doc([
            {"chunk_text": "39D. Offences for failure to comply with provisions of section 21",
             "section_number": "42", "hierarchy_level": 3},
            {"chunk_text": "(2) fragment", "hierarchy_level": 3},
        ])
        l4 = {"p0": ["42"]}  # L4 verified 42 in this chunk's text
        corrections, fills = derive_l7(docs, l4)
        assert corrections == {}
        assert fills == {"p1": "42"}

    def test_sections_covered_guard_blocks_correction(self):
        docs = _doc([
            {"chunk_text": "76A. Punishment for contravention of section 73 or section 76",
             "section_number": "77", "hierarchy_level": 3, "sections_covered": ["76", "77"]},
        ])
        assert derive_l7(docs, {}) == ({}, {})


# --------------------------------------------------------------------------- #
# P1 — document_title backfill
# --------------------------------------------------------------------------- #
class TestDeriveTitle:
    @staticmethod
    def _u(name):
        return f"C:\\github\\NSA_webservice\\other domain\\{name}"

    def test_underscores_to_spaces(self):
        assert derive_title(self._u("Food_Safety_and_Standards_Act_2006.pdf")) \
            == "Food Safety and Standards Act 2006"

    def test_fssai_uri_style(self):
        assert derive_title("FSSAI_rules documents\\Food_Additives_Regulations-4.pdf") \
            == "Food Additives Regulations-4"

    def test_junk_numeric_prefix_stripped(self):
        assert derive_title("FSSAI_rules documents\\1_Notification dt 10_03_2026.pdf") \
            == "Notification dt 10 03 2026"
        assert derive_title("FSSAI_rules documents\\6928478129442Final Notificat.pdf") \
            == "Final Notificat"

    def test_opaque_filename_kept(self):
        assert derive_title(self._u("A2013-18.pdf")) == "A2013-18"
        assert derive_title(self._u("view-casepdf-1.pdf")) == "view-casepdf-1"

    def test_clean_title_unchanged(self):
        assert derive_title(self._u("FSS_Amendment_Act_3-2023.pdf")) == "FSS Amendment Act 3-2023"

    def test_fragment_suffix_stripped(self):
        assert derive_title("FSSAI_rules documents\\273797-1.pdf#9c826cd9a0d5412f") \
            == "273797-1"

    def test_empty(self):
        assert derive_title("") == ""
        assert derive_title(None) == ""


class TestDeriveChanges:
    def test_fills_empty_titles_per_document(self):
        payloads = {
            "p1": {"document_id": "d1", "document_uri": "Food_Additives_Regulations-4.pdf"},
            "p2": {"document_id": "d1", "document_uri": "Food_Additives_Regulations-4.pdf"},
            "p3": {"document_id": "d2", "document_uri": "A2013-18.pdf"},
        }
        changes = derive_changes(payloads)
        assert changes == {
            "p1": {"document_title": "Food Additives Regulations-4"},
            "p2": {"document_title": "Food Additives Regulations-4"},
            "p3": {"document_title": "A2013-18"},
        }

    def test_never_overwrites_existing_title(self):
        payloads = {"p1": {"document_id": "d1", "document_uri": "x.pdf",
                           "document_title": "Existing Title"}}
        assert derive_changes(payloads) == {}

    def test_no_uri_skipped(self):
        payloads = {"p1": {"document_id": "d1"}}
        assert derive_changes(payloads) == {}
