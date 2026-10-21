"""Tests for the text-based gold resolution fallback.

Pure-function tests: no Qdrant, no network, no LLM.  The module under test
is ``evaluation.gold_text_match`` — the text-based fallback that resolves
gold provisions when the ``section_number`` stamp is missing or wrong.

These tests cover the exact scenarios documented in
``FINDINGS_89_STATUS.md`` and ``RAG_ANSWER_QUALITY_IMPROVEMENT_ANALYSIS.md``:

* Present-but-untagged chunks (``section_number=None``) whose text is legible
* Wrong-stamp chunks (amendment notifications carry the section number of
  the parent provision, not the operative text)
* Corrupt OCR chunks that must NOT false-match
* Family-guard correctness (no cross-act contamination)
* Backward compatibility (text_fallback=False preserves legacy behaviour)
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import GoldUnit
from evaluation.gold_text_match import (
    MIN_TITLE_WORDS,
    matches_gold_enhanced,
    resolve_by_text,
    text_matches_title,
    title_is_distinctive,
    title_phrase,
)
from evaluation.resolution import FamilyMap, matches_gold

_FSSAI_ACT = "Food Safety and Standards Act, 2006"
_PCRA_ACT = "The Prevention of Cruelty to Animals Act, 1960"


def _unit(pid, family, title, section="99"):
    return GoldUnit(
        provision_id=pid, family=family, section=section,
        act=_FSSAI_ACT if family == "fssai" else _PCRA_ACT,
        collection="c", document_id="doc1", gain=2.0, role="primary", title=title,
    )


def _payload(text, *, act_name=_FSSAI_ACT, document_title="Food Safety and Standards Act, 2006",
             section_number=None, sections_covered=None):
    return {
        "act_name": act_name, "document_title": document_title,
        "section_number": section_number, "sections_covered": sections_covered,
        "chunk_text": text,
    }


# --------------------------------------------------------------------------- #
# title_phrase / title_is_distinctive
# --------------------------------------------------------------------------- #

class TestTitlePhrase:
    def test_distinctive_multi_word_title(self):
        assert title_phrase("Punitive damages") == "punitive damages"

    def test_long_title_truncated(self):
        long_title = " ".join(["word"] * 30)
        phrase = title_phrase(long_title)
        assert phrase is not None
        assert len(phrase) <= 80

    def test_single_word_title_rejected(self):
        assert title_phrase("Penalty") is None

    def test_empty_title_rejected(self):
        assert title_phrase("") is None
        assert title_phrase(None) is None  # type: ignore

    def test_punctuation_stripped(self):
        assert title_phrase("Punitive, damages!") == "punitive damages"


class TestTitleIsDistinctive:
    def test_distinctive(self):
        assert title_is_distinctive("Punitive damages") is True

    def test_not_distinctive(self):
        assert title_is_distinctive("Penalty") is False
        assert title_is_distinctive("") is False


# --------------------------------------------------------------------------- #
# text_matches_title
# --------------------------------------------------------------------------- #

class TestTextMatchesTitle:
    def test_exact_match(self):
        assert text_matches_title("Section 52 - Punitive damages for misbranding", "Punitive damages") is True

    def test_case_insensitive(self):
        assert text_matches_title("PUNITIVE DAMAGES are available under s52", "Punitive damages") is True

    def test_word_boundary_prevents_partial_match(self):
        assert text_matches_title("impunitive damages found here", "Punitive damages") is False

    def test_word_boundary_prevents_substring_in_longer_phrase(self):
        assert text_matches_title("non-punitive damages apply", "Punitive damages") is False

    def test_whitespace_variants_match(self):
        assert text_matches_title("Punitive\ndamages\n\nare available", "Punitive damages") is True

    def test_title_not_in_text(self):
        assert text_matches_title("The food shall be stored properly", "Punitive damages") is False

    def test_corrupt_ocr_gibberish_no_false_match(self):
        corrupt = "line\nin breeding _ \n4 X}"
        assert text_matches_title(corrupt, "Punitive damages") is False
        assert text_matches_title(corrupt, "Penalty") is False  # single word -> rejected

    def test_empty_text(self):
        assert text_matches_title("", "Punitive damages") is False
        assert text_matches_title(None, "Punitive damages") is False  # type: ignore

    def test_short_title_rejected_before_match(self):
        assert text_matches_title("Section 52 Penalty for misbranded food", "Penalty") is False

    def test_multi_word_title_in_long_text(self):
        long_text = "Various provisions apply. Section 31 covers duties. Section 52 - Punitive damages may be awarded."
        assert text_matches_title(long_text, "Punitive damages") is True


# --------------------------------------------------------------------------- #
# _payload_families
# --------------------------------------------------------------------------- #

class TestPayloadFamilies:
    def test_fssai_family(self):
        from evaluation.gold_text_match import _payload_families
        fm = FamilyMap()
        payload = _payload("text")
        assert _payload_families(payload, fm) == {"fssai"}

    def test_pcra_family(self):
        from evaluation.gold_text_match import _payload_families
        fm = FamilyMap()
        payload = _payload("text", act_name=_PCRA_ACT,
                           document_title="The Prevention of Cruelty to Animals (Rules) 2017")
        assert _payload_families(payload, fm) == {"pcra"}

    def test_no_act_name(self):
        from evaluation.gold_text_match import _payload_families
        fm = FamilyMap()
        payload = {"chunk_text": "some text"}
        assert _payload_families(payload, fm) == set()

    def test_non_matching_family(self):
        from evaluation.gold_text_match import _payload_families
        fm = FamilyMap()
        payload = _payload("text", act_name="Some other Act, 2020",
                           document_title="Some other Act, 2020")
        assert _payload_families(payload, fm) == set()


# --------------------------------------------------------------------------- #
# resolve_by_text
# --------------------------------------------------------------------------- #

class TestResolveByText:
    def test_match_when_stamp_missing_but_text_present(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload(
            "Section 52 - Punitive damages may be awarded for misbranding.",
            section_number=None,
        )
        assert resolve_by_text(payload, unit, fm) is True

    def test_no_match_when_family_mismatch(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload(
            "Punitive damages found here",
            act_name="Environment (Protection) Act, 1986",
            document_title="Environment (Protection) Act, 1986",
        )
        assert resolve_by_text(payload, unit, fm) is False

    def test_no_match_when_text_empty(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = {"act_name": _FSSAI_ACT, "section_number": None}
        assert resolve_by_text(payload, unit, fm) is False

    def test_no_match_when_unit_has_no_family(self):
        fm = FamilyMap()
        unit = _unit("unknown:s52", "", "Punitive damages", section="52")
        payload = _payload("Punitive damages", section_number=None)
        assert resolve_by_text(payload, unit, fm) is False

    def test_no_match_when_title_not_distinctive(self):
        fm = FamilyMap()
        unit = _unit("fssai:s53", "fssai", "Penalty", section="53")
        payload = _payload("Penalty for misbranding is severe.", section_number=None)
        assert resolve_by_text(payload, unit, fm) is False

    def test_corrupt_ocr_does_not_match(self):
        fm = FamilyMap()
        unit = _unit("pcra:s4", "pcra", "Liability of owner of breeding centre", section="4")
        payload = _payload(
            "4 The owner of Breeding Centre shall abide (59 of and the rules and nolifications",
            act_name=_PCRA_ACT, section_number=None,
        )
        assert resolve_by_text(payload, unit, fm) is False

    def test_pcra_chunk_resolves_via_title_when_text_clean(self):
        fm = FamilyMap()
        unit = _unit("pcra:s4", "pcra", "Liability of owner of breeding centre", section="4")
        payload = _payload(
            "Rule 4. Liability of owner of breeding centre: The owner shall abide.",
            act_name=_PCRA_ACT,
            document_title="The Prevention of Cruelty to Animals (Rules) 2017",
            section_number=None,
        )
        assert resolve_by_text(payload, unit, fm) is True


# --------------------------------------------------------------------------- #
# matches_gold_enhanced — backward compatibility + text fallback
# --------------------------------------------------------------------------- #

class TestMatchesGoldEnhanced:
    def test_backward_compatible_when_text_fallback_false(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload(
            "52. Punitive damages for misbranding. The court may award punitive damages.",
            section_number="52",
        )
        assert matches_gold(payload, unit, fm) is True
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=False) is True

    def test_enhanced_finds_text_when_stamp_fails(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload(
            "Amendment: In section 52, for the words 'penalty', 'Punitive damages' shall be substituted.",
            section_number="53",
        )
        assert matches_gold(payload, unit, fm) is False
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=True) is True

    def test_enhanced_fails_when_text_fallback_false_and_stamp_fails(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload("Amendment mentioning Punitive damages", section_number="53")
        assert matches_gold(payload, unit, fm) is False
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=False) is False

    def test_enhanced_stamp_takes_priority(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload(
            "Section 52. Punitive damages for misbranding. Punitive damages may be awarded.",
            section_number="52",
        )
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=True) is True

    def test_enhanced_family_guard_blocks_cross_act_text_match(self):
        fm = FamilyMap()
        unit = _unit("fssai:s52", "fssai", "Punitive damages", section="52")
        payload = _payload(
            "Punitive damages apply here but this is an env Act chunk",
            section_number=None,
            act_name="Environment (Protection) Act, 1986",
            document_title="Environment (Protection) Act, 1986",
        )
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=True) is False

    def test_enhanced_no_false_positive_on_corrupt_ocr(self):
        fm = FamilyMap()
        unit = _unit("pcra:s4", "pcra", "Liability of owner of breeding centre", section="4")
        payload = _payload("4 x} line in breeding _", act_name=_PCRA_ACT)
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=True) is False

    def test_enhanced_instrument_level_unit_family_only(self):
        fm = FamilyMap()
        unit = _unit("fssai:regs/licensing", "fssai", "", section=None)
        payload = _payload("Any text whatever", section_number=None)
        assert matches_gold(payload, unit, fm) is True
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=True) is True

    def test_enhanced_text_fallback_finds_phrase_in_index_run(self):
        """Section-index run: section_number=None but text lists section headers."""
        fm = FamilyMap()
        unit = _unit("fssai:s41", "fssai", "Sampling and analysis", section="41")
        payload = _payload(
            "40. Power to prohibit.\n41. Sampling and analysis.\nThe food shall be sampled.",
            section_number=None,
        )
        assert matches_gold(payload, unit, fm) is False
        assert resolve_by_text(payload, unit, fm) is True
        assert matches_gold_enhanced(payload, unit, fm, text_fallback=True) is True
