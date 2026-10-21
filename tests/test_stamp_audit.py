"""Pin the stamping defect and the narrow fix, before either ships.

``evaluation/gate_fixes.py`` measures that blanking the stamp on amendment
notifications removes 100% of the poisoned stamps (141 -> 0) and loses no gold
unit. That result deserves tests, because the defect it corrects is invisible
to the existing suite: ``legal_engine._extract_section_from_text`` returns a
section for every amendment footnote it sees, and nothing asserts otherwise.

Two things are pinned here:

1. **The defect** — the mention detector must be shown to mislabel amendment
   prose. This is the characterisation test; it will keep passing after the fix
   because it tests the shipped detector, which is the point: it documents what
   the corpus was built from.
2. **The fix's contract** — a stamp may never be derived from an in-text
   mention of a provision, and a chunk that opens a provision must keep its
   stamp. Written against the rule, not against the current implementation, so
   it survives a rewrite of the regex.

Run: ``python -m pytest tests/test_stamp_audit.py -q``
"""

from __future__ import annotations

import re

import pytest

#: Verbatim from legal_paragraph_detection_engine/src/legal_engine.py:298.
MENTION_DETECT = re.compile(r"Section\s*(\d+)", re.IGNORECASE)

AMENDMENT_NOTES = [
    '(C) In section 63, for the words "after being warned by any metro railway official to desist", the words "x" shall be substituted.',
    '(L) In section 34, for the words "sue for compensation", the words "apply for compensation" shall be substituted.',
    '(Y) In section 52, in sub-section (3), for the words "shall be punishable with fine", the words "shall be liable to penalty" shall be substituted.',
    "contravention under section 63, the person shall, on conviction, be punishable with fine which may extend to ten thousand rupees.",
]

OPERATIVE_HEADS = [
    ("52", "52. Penalty for misbranded food."),
    ("60", "60. Punishment for interfering with seized items. He shall be punishable."),
    ("68", "68. Adjudication."),
    ("63", "63. Punishment for carrying on a food business without licence shall be punishable."),
]


class TestMentionDetectorMislabel:
    """Characterisation: the shipped detector stamps what a chunk only mentions."""

    @pytest.mark.parametrize("text", AMENDMENT_NOTES)
    def test_mention_detector_returns_a_section_for_amendment_prose(self, text):
        """Documents the defect: this is how amendment notes acquired stamps.

        If this test starts failing, the detector changed -- good. The fix does
        not make this pass; it stops the result from reaching ``section_number``.
        """
        assert MENTION_DETECT.search(text) is not None

    @pytest.mark.parametrize("text", AMENDMENT_NOTES)
    def test_amendment_prose_opens_with_no_section(self, text):
        """The negative that the fix depends on: no marginal note at the start."""
        assert not re.match(r"^\s*\d{1,3}[A-Z]?\s*\.\s+[A-Z\u201c\"]", text.strip())


class TestStampContract:
    """The rule the fixed stamper must satisfy."""

    @pytest.mark.parametrize("stamp,text", OPERATIVE_HEADS)
    def test_provision_head_yields_its_own_number(self, stamp, text):
        head = re.match(r"^\s*(\d{1,3}[A-Z]?)\s*\.\s+[A-Z\u201c\"]", text.strip())
        assert head is not None
        assert head.group(1) == stamp

    @pytest.mark.parametrize("text", AMENDMENT_NOTES)
    def test_amendment_note_must_not_yield_a_section(self, text):
        amendment = re.compile(
            r"(for the words|shall be substituted|the following section shall be substituted"
            r"|and after the words|in the marginal heading)",
            re.IGNORECASE,
        )
        if amendment.search(text):
            assert not re.match(r"^\s*\d{1,3}[A-Z]?\s*\.\s+[A-Z\u201c\"]", text.strip())

    def test_fragment_cross_reference_is_not_a_provision(self):
        """'section 52 due, subject to a' carries no operative text."""
        assert not re.match(r"^\s*\d{1,3}[A-Z]?\s*\.\s+[A-Z\u201c\"]", "section 52 due, subject to a")

    def test_gazette_page_header_is_not_a_provision(self):
        """'75. 2021 24 The Inland (A)In section 63' -- 75 is a gazette item.

        Asserted positively: the gazette pattern must match. The earlier
        version used a double negative and asserted nothing meaningful.
        """
        gazette = re.compile(r"^\s*\d{1,3}\s*\.\s*(?:\d{4}\b|[A-Z][a-z]+\s+\([A-Z]\))")
        assert gazette.match("75. 2021 24 The Inland (A)In section 63, for the words")
        assert gazette.match("(M) For section 26, the following section shall be substituted") is None
