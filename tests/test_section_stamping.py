"""The stamping contract, asserted against ``LegalParagraphEngine`` itself.

``_extract_section_from_text`` used ``Section\\s*(\\d+)``, which matched any
in-text reference. A gazette amendment notification — *"(C) In section 63, for
the words X, the words Y shall be substituted"* — was therefore stamped 63,
and ``Chunk.from_paragraph`` copies that value straight into
``section_number``. Measured live: 141 stamps were backed only by amendment
prose, so ``matches_gold`` credited amendment notes as gold and the structural
arm filtered on them.

These tests pin the corrected rule at the point where it is applied, using the
strings the corpus actually contains. The characterisation tests deliberately
keep asserting what the *old* regex did, so the defect stays documented and a
future regression to it is visible.

Run: ``python -m pytest tests/test_section_stamping.py -q``
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "legal_paragraph_detection_engine") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "legal_paragraph_detection_engine"))

from src.legal_engine import LegalParagraphEngine  # noqa: E402

OLD_DETECT = __import__("re").compile(r"Section\s*(\d+)", __import__("re").IGNORECASE)


@pytest.fixture(scope="module")
def engine() -> LegalParagraphEngine:
    return LegalParagraphEngine()


# Corpus strings that must NOT acquire the section they mention.
AMENDMENT_NOTES = [
    '(C) In section 63, for the words "after being warned by any metro railway official to desist", the words "x" shall be substituted.',
    "75. 2021 24 The Inland (A)In section 63, for the words \"or an offence under Vessels Act\", the words \"y\" shall be substituted.",
    '(L) In section 34, for the words "sue for compensation", the words "apply for compensation" shall be substituted.',
    '(Y) In section 52, in sub-section (3), for the words "shall be punishable with fine", the words "shall be liable to penalty" shall be substituted.',
    '(M) For section 26, the following section shall be substituted, namely: "26. Penalty for damage caused to land or crops."',
]

# Real marginal notes, which must keep their section.
OPERATIVE_HEADS = [
    ("52", "52. Penalty for misbranded food."),
    ("60", "60. Punishment for interfering with seized items. He shall be punishable."),
    ("68", "68. Adjudication."),
    ("63", "63. Punishment for carrying on a food business without licence shall be punishable."),
]

# Fragments and cross-references that own nothing.
FRAGMENTS = [
    "section 52 due, subject to a",
    "of section 52 or any rule made under sub-section (3)",
    "sub-section (3) of section 52.",
    "(C) In section 63,",
]


class TestAmendmentNotesOwnNothing:
    @pytest.mark.parametrize("text", AMENDMENT_NOTES)
    def test_amendment_note_gets_no_section(self, engine, text):
        assert engine._extract_section_from_text(text) is None

    @pytest.mark.parametrize("text", AMENDMENT_NOTES)
    def test_old_detector_would_have_mislabelled(self, text):
        """Characterisation: keep the defect visible in the suite.

        Fails loudly if someone restores the mention regex.
        """
        assert OLD_DETECT.search(text) is not None


class TestOperativeHeadingsKeepTheirSection:
    @pytest.mark.parametrize("stamp,text", OPERATIVE_HEADS)
    def test_marginal_note_yields_its_own_number(self, engine, stamp, text):
        assert engine._extract_section_from_text(text) == stamp

    @pytest.mark.parametrize("_stamp,text", OPERATIVE_HEADS)
    def test_old_detector_missed_every_one(self, _stamp, text):
        """Characterisation: the old regex could not see a marginal note."""
        assert OLD_DETECT.search(text) is None


class TestFragmentsOwnNothing:
    @pytest.mark.parametrize("text", FRAGMENTS)
    def test_fragment_gets_no_section(self, engine, text):
        assert engine._extract_section_from_text(text) is None


class TestEdgeCases:
    def test_empty_text(self, engine):
        assert engine._extract_section_from_text("") is None

    def test_none_text(self, engine):
        assert engine._extract_section_from_text(None) is None

    def test_leading_whitespace_is_tolerated(self, engine):
        assert engine._extract_section_from_text("   \n 52. Penalty for misbranded food.") == "52"

    def test_suffixed_section_letter(self, engine):
        assert engine._extract_section_from_text('14A. Restrictions on grant of licence.') == "14A"

    def test_dotted_clause_number_is_not_a_section(self, engine):
        """'2.4.1.3' must not read as section 2."""
        assert engine._extract_section_from_text("2.4.1.3 The hygiene requirements are:" ) is None

    def test_lowercase_after_period_is_not_a_marginal_note(self, engine):
        """A sentence ending in a numeral must not claim a section."""
        assert engine._extract_section_from_text("52. penalty for misbranded food") is None
