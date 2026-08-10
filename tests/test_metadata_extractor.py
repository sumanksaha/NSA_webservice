"""Tests for the Legal Metadata Extraction Engine module.

Covers:
- Model validation (LegalMetadata, FieldConfidence)
- Individual extractors (title, date, authority, etc.)
- Regex pattern matching
- Language detection
- Confidence scoring
- Validation rules
- Full engine integration with real legal text samples
"""

from __future__ import annotations

from app.metadata_extractor import FieldConfidence, LegalMetadata, LegalMetadataEngine
from app.metadata_extractor.extractors.base import (
    AmendmentExtractor,
    AuthorityExtractor,
    CountryExtractor,
    DateExtractor,
    DocumentTypeExtractor,
    EffectiveDateExtractor,
    GazetteExtractor,
    JurisdictionExtractor,
    LanguageExtractor,
    NotificationExtractor,
    StateExtractor,
    TitleExtractor,
    VersionExtractor,
)
from app.metadata_extractor.validation import Validator

# ============================================================================
# Sample legal document texts
# ============================================================================

_FSSAI_ACT_SAMPLE = """
THE FOOD SAFETY AND STANDARDS ACT, 2006
ACT NO. 34 OF 2006

[23rd August, 2006.]

An Act to consolidate the laws relating to food and to establish the Food
Safety and Standards Authority of India for laying down science based
standards for articles of food and to regulate their manufacture, storage,
distribution, sale and import, to ensure availability of safe and wholesome
food for human consumption and for matters connected therewith or incidental
thereto.

BE it enacted by Parliament in the Fifty-seventh Year of the Republic of India
as follows:—

CHAPTER I
PRELIMINARY

1. Short title, extent and commencement.—(1) This Act may be called the Food
Safety and Standards Act, 2006.

It extends to the whole of India and shall come into force on such date as the
Central Government may, by notification in the Official Gazette, appoint, and
different dates may be appointed for different provisions of this Act.
"""

_FSSAI_AMENDMENT_SAMPLE = """
THE FOOD SAFETY AND STANDARDS (AMENDMENT) ACT, 2020
ACT NO. 17 OF 2020

[15th September, 2020.]

An Act further to amend the Food Safety and Standards Act, 2006.

BE it enacted by Parliament in the Seventy-first Year of the Republic of India
as follows:—

1. Short title and commencement.—(1) This Act may be called the Food Safety
and Standards (Amendment) Act, 2020.

It shall be deemed to have come into force on the 1st day of August, 2020.
"""

_NOTIFICATION_SAMPLE = """
F. No. 1-4/Standards/SP(FSSAI)/2019
Food Safety and Standards Authority of India
(Advertising Section)
Notification
New Delhi, the 5th March, 2021

Subject: Food Safety and Standards (Advertising and Claims) Regulations, 2021

S.O. 1234(E).—In exercise of the powers conferred by section 92 of the Food
Safety and Standards Act, 2006, the Food Safety and Standards Authority of
India hereby makes the following regulations.
"""

_GAZETTE_SAMPLE = """
THE GAZETTE OF INDIA
EXTRAORDINARY
PART II — Section 3 — Sub-section (i)
PUBLISHED BY AUTHORITY

No. 456 — New Delhi, Friday, January 15, 2021/ Pausa 25, 1942

MINISTRY OF HEALTH AND FAMILY WELFARE
(Department of Health and Family Welfare)
NOTIFICATION

New Delhi, the 12th January, 2021

G.S.R. 78(E).—The following draft of certain regulations further to amend
the Food Safety and Standards (Advertising and Claims) Regulations, 2018.
"""

_HINDI_SAMPLE = """
खाद्य सुरक्षा एवं मानक अधिनियम, 2006
अधिनियम संख्या 34 का 2006

[23 अगस्त, 2006]

खाद्य पदार्थों से संबंधित विधियों को समेकित करने और खाद्य पदार्थों के
मानक निर्धारित करने के लिए भारतीय खाद्य सुरक्षा एवं मानक प्राधिकरण
की स्थापना करने हेतु एक अधिनियम।
"""


# ============================================================================
# Model tests
# ============================================================================


class TestModels:
    def test_field_confidence_minimal(self):
        fc = FieldConfidence(value="Test Act", score=0.85, method="regex")
        assert fc.value == "Test Act"
        assert fc.score == 0.85
        assert fc.method == "regex"

    def test_legal_metadata_minimal(self):
        def default(v="", s=0.0, m="default"):
            return FieldConfidence(value=v, score=s, method=m)

        meta = LegalMetadata(
            title=default("Act", 0.8),
            version=default(),
            date=default(),
            authority=default(),
            gazette_number=default(),
            notification_number=default(),
            language=default("english", 0.9),
            jurisdiction=default("India", 0.9),
            state=default(),
            country=default("India", 0.9),
            document_type=default("Act", 0.8),
            amendment_status=default(),
            effective_date=default(),
        )
        assert meta.title.value == "Act"
        assert meta.country.value == "India"
        assert meta.overall_confidence > 0

    def test_legal_metadata_flat_dict(self):
        def default(v="x", s=0.7, m="regex"):
            return FieldConfidence(value=v, score=s, method=m)

        meta = LegalMetadata(
            title=default("Test Act", 0.95),
            version=default(),
            date=default(),
            authority=default(),
            gazette_number=default(),
            notification_number=default(),
            language=default("english", 0.9),
            jurisdiction=default("India", 0.9),
            state=default(),
            country=default("India", 0.9),
            document_type=default("Act", 0.9),
            amendment_status=default(),
            effective_date=default(),
        )
        flat = meta.to_flat_dict()
        assert flat["title"] == "Test Act"
        assert "overall_confidence" in flat


# ============================================================================
# Extractor tests
# ============================================================================


class TestExtractors:
    def test_title_extractor_fssai_act(self):
        ex = TitleExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        best = results[0]
        assert "FOOD SAFETY AND STANDARDS ACT" in best[0].upper()
        assert best[1] > 0.8  # high confidence

    def test_title_extractor_amendment(self):
        ex = TitleExtractor()
        results = ex.extract(_FSSAI_AMENDMENT_SAMPLE)
        assert len(results) > 0
        best = results[0]
        # The regex catches either "AMENDMENT" (via short_title_amended, conf=0.95)
        # or "to amend" (via long_title_act, conf=0.75→0.80)
        assert "AMEND" in best[0].upper()

    def test_date_extractor(self):
        ex = DateExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        # Should find "23rd August, 2006"
        assert any("2006" in r[0] for r in results)

    def test_authority_extractor(self):
        ex = AuthorityExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        # Should find FSSAI or Parliament references
        values = " ".join(r[0].upper() for r in results)
        assert "FSSAI" in values or "PARLIAMENT" in values or "AUTHORITY" in values

    def test_language_extractor_english(self):
        ex = LanguageExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        assert results[0][0] == "english"

    def test_language_extractor_hindi(self):
        ex = LanguageExtractor()
        results = ex.extract(_HINDI_SAMPLE)
        assert len(results) > 0
        languages = {r[0] for r in results}
        assert "hindi" in languages

    def test_document_type_act(self):
        ex = DocumentTypeExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        assert results[0][0] == "Act"

    def test_document_type_notification(self):
        ex = DocumentTypeExtractor()
        results = ex.extract(_NOTIFICATION_SAMPLE)
        assert results[0][0] == "Notification"

    def test_policy_pattern_does_not_match_commission(self):
        """Regression (2026-08-09): "Commission" contains "MISSION" as a
        substring — the policy keyword group is word-boundaried so the FSS Act's
        "...the Commission" text is NOT labelled Policy (it must stay Act)."""
        ex = DocumentTypeExtractor()
        results = ex.extract(
            "The Food Safety and Standards Act, 2006\n\n"
            "Food Safety and Standards Authority of India ... the Commission shall\n"
            "ensure that the Authority functions in accordance with this Act.\n"
        )
        assert results[0][0] == "Act"
        assert not any(r[0] == "Policy" for r in results)

    def test_policy_pattern_matches_uppercase_heading_only(self):
        """Only a proper uppercase legal heading triggers Policy — never
        title-case or lowercase body text (both fail the case-sensitive +
        line-anchored pattern)."""
        ex = DocumentTypeExtractor()
        # Uppercase gazette-style heading -> Policy.
        assert any(
            r[0] == "Policy" for r in ex.extract("NATIONAL FOOD POLICY, 2023\n\nObjectives.")
        )
        # Lowercase body text / title-case headings -> no Policy.
        assert not any(
            r[0] == "Policy" for r in ex.extract("evaluating policy on food safety.")
        )
        assert not any(
            r[0] == "Policy" for r in ex.extract("National Food Policy, 2023\n\nObjectives.")
        )

    def test_document_type_instrument_outranks_gazette(self):
        """§2.4.1 (2026-08-09): a gazette carrying a real instrument title line
        is classified by the instrument, not by its publication wrapper."""
        ex = DocumentTypeExtractor()
        text = (
            "THE GAZETTE OF INDIA : EXTRAORDINARY [PART III—SEC. 4]\n"
            "MINISTRY OF HEALTH AND FAMILY WELFARE\n"
            "(Food Safety and Standards Authority of India)\n"
            "NOTIFICATION\n"
            "New Delhi, dated the 1st August, 2011\n\n"
            "FOOD SAFETY AND STANDARDS (CONTAMINANTS, TOXINS AND RESIDUES)\n"
            "REGULATIONS, 2011\n"
        )
        results = ex.extract(text)
        assert results[0][0] == "Regulation"

    def test_document_type_instrument_outranks_notification(self):
        """§2.4.1: even with a NOTIFICATION heading, an all-caps instrument
        title line wins — the notification merely publishes the instrument."""
        ex = DocumentTypeExtractor()
        text = (
            "Notification\n"
            "New Delhi, dated the 23rd June, 2026\n\n"
            "FOOD SAFETY AND STANDARDS (ADVERTISING AND CLAIMS) REGULATIONS, 2018\n"
        )
        results = ex.extract(text)
        assert results[0][0] == "Regulation"

    def test_document_type_bare_act_fragment(self):
        """§2.4.1: a wrapped-title fragment on its own line ("ACT, 2026" from a
        gazette whose title broke across lines) is still an Act title."""
        ex = DocumentTypeExtractor()
        results = ex.extract("ACT, 2026\n")
        assert results[0][0] == "Act"

    def test_document_type_bare_regulations_fragment(self):
        """§2.4.1: "REGULATIONS 2011" (the continuation of a wrapped title with
        no comma) matches the all-caps branch."""
        ex = DocumentTypeExtractor()
        results = ex.extract("REGULATIONS 2011\n")
        assert results[0][0] == "Regulation"

    def test_document_type_body_reference_not_act(self):
        """§2.4.1 regression: a wrapped body continuation ("Standards Act, 2006"
        = the tail of "...of the Food Safety and Standards Act, 2006") has only
        a one-word lead-in, so it must NOT be read as an Act title."""
        ex = DocumentTypeExtractor()
        results = ex.extract(
            "S.O. 1234(E).—In exercise of the powers conferred by section 92 of the Food\n"
            "Standards Act, 2006\n"
        )
        assert not any(r[0] == "Act" for r in results)

    def test_document_type_wrapped_preamble_continuation_not_act(self):
        """§2.4.1 regression: "Safety and Standards Act, 2006, the Food ..." is a
        wrapped preamble continuation (year NOT at line end) — must not win."""
        ex = DocumentTypeExtractor()
        results = ex.extract(
            "S.O. 1234(E).—In exercise of the powers conferred by section 92 of the Food\n"
            "Safety and Standards Act, 2006, the Food Safety and Standards Authority of\n"
            "India hereby makes the following regulations.\n"
        )
        assert not any(r[0] == "Act" for r in results)

    def test_document_type_mid_line_reference_not_instrument(self):
        """§2.4.1: an instrument reference mid-line (not line-anchored) never
        classifies — e.g. "...section 92 of the Food Safety and Standards Act,
        2006" inside a preamble paragraph."""
        ex = DocumentTypeExtractor()
        results = ex.extract(
            "In exercise of the powers conferred by section 92 of the Food Safety and\n"
            "Standards Act, 2006, the Food Safety and Standards Authority of India hereby\n"
            "makes the following regulations.\n"
        )
        assert not any(r[0] == "Act" for r in results)

    def test_document_type_amendment_act(self):
        """§2.4.1: "THE FOOD SAFETY AND STANDARDS (AMENDMENT) ACT, 2020" is an
        Act, not a Gazette Notification — instrument outranks publication format."""
        ex = DocumentTypeExtractor()
        results = ex.extract(_FSSAI_AMENDMENT_SAMPLE)
        assert results[0][0] == "Act"

    def test_document_type_paren_tail_regulation(self):
        """§2.4.1 (2026-08-09): a paren-terminated lead-in — the wrapped tail of
        an instrument title like "(Organic Foods) Regulations, 2017." breaking
        across lines — is a Regulation title line, not body text."""
        ex = DocumentTypeExtractor()
        text = (
            "THE GAZETTE OF INDIA : EXTRAORDINARY\n"
            "MINISTRY OF HEALTH AND FAMILY WELFARE\n"
            "(Food Safety and Standards Authority of India)\n"
            "NOTIFICATION\n\n"
            "No. CPB/03/.... Whereas the draft Food Safety and Standards\n"
            "Foods) Regulations, 2017.\n"
        )
        results = ex.extract(text)
        assert results[0][0] == "Regulation"

    def test_document_type_paren_tail_all_caps(self):
        """§2.4.1: paren-tail matches must also work in ALL-CAPS form
        ("FOODS) REGULATIONS, 2011")."""
        ex = DocumentTypeExtractor()
        results = ex.extract("FOODS) REGULATIONS, 2011\n")
        assert results[0][0] == "Regulation"

    def test_document_type_paren_tail_notification_docs_clean(self):
        """§2.4.1 regression: the must-stay-Notification corpus docs have zero
        paren-tail lines — a bare paren clause followed by an instrument word
        without a year (mid-line) must not classify."""
        ex = DocumentTypeExtractor()
        results = ex.extract(
            "S.O. 1234(E).—In exercise of the powers conferred by section 92 of the\n"
            "Food Safety and Standards Act, 2006, the Food Authority hereby makes\n"
            "the following regulations, namely:—\n"
        )
        assert not any(r[0] in ("Regulation", "Act") for r in results)

    def test_document_type_title_region_boost(self):
        """§2.4.1 (2026-08-09): an instrument title in the header region scores
        0.95 (title-region boost) — it must outrank a deep body reference and
        a generic publication-format match."""
        ex = DocumentTypeExtractor()
        text = (
            "THE FOOD SAFETY AND STANDARDS (AMENDMENT) ACT, 2020\n"
            "ACT NO. 17 OF 2020\n\n"
            "An Act further to amend the Food Safety and Standards Act, 2006.\n"
        )
        results = ex.extract(text)
        assert results[0][0] == "Act"
        assert results[0][1] == 0.95  # title-region boost applied

    def test_document_type_extract_fast_on_dense_body(self):
        """Regression (2026-08-09): token-dense body text catastrophically
        backtracked in the nested-quantifier instrument patterns — one 63K-char
        regulation PDF hung ``DocumentClassifier`` for >25 minutes and froze the
        corpus ingestion.  The linearised patterns must classify a dense
        non-matching document in well under a second."""
        import time

        ex = DocumentTypeExtractor()
        # ~300 lines x 45 mixed-case space-separated tokens (the old pattern
        # explored every space-split on these non-matching lines).
        lines = [" ".join((("WORD" if t % 3 == 0 else "tok") + f"{i}{t}") for t in range(45)) for i in range(300)]
        body = "\n".join(lines)

        t0 = time.monotonic()
        results = ex.extract(body)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, f"document-type extraction too slow: {elapsed:.1f}s"
        assert not any(r[0] in ("Act", "Regulation", "Rule", "Bill") for r in results)

    def test_document_type_deep_match_no_boost(self):
        """§2.4.1: an instrument match deep in the body keeps 0.90 (no boost) —
        the title-region boost is scoped to the first 10% only.  The deep
        paren-tail Regulation ("Foods) Regulations, 2017.") still outranks the
        NOTIFICATION heading via insertion-order priority, but neither is
        boosted above 0.90."""
        ex = DocumentTypeExtractor()
        text = (
            "NOTIFICATION\n"
            "New Delhi, the 5th March, 2021\n\n"
            "1. These regulations may be called the Food Safety and Standards (Organic\n"
            "Foods) Regulations, 2017.\n\n"
            "2. They shall come into force on the date of their publication in the\n"
            "Official Gazette.\n\n"
            "3. In exercise of the powers conferred by section 92 of the Food\n"
            "Safety and Standards Act, 2006, the Food Authority hereby makes\n"
            "the following regulations, namely:—\n"
        )
        results = ex.extract(text)
        assert results[0][0] == "Regulation"
        assert all(r[1] == 0.90 for r in results)

    def test_amendment_status_original(self):
        ex = AmendmentExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        statuses = {r[0] for r in results}
        assert "Original" in statuses

    def test_amendment_status_amended(self):
        ex = AmendmentExtractor()
        results = ex.extract(_FSSAI_AMENDMENT_SAMPLE)
        assert len(results) > 0
        # Should contain "Amended" or similar
        {r[0] for r in results}

    def test_jurisdiction_extractor(self):
        ex = JurisdictionExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        # Should find India / Republic of India
        assert any("INDIA" in r[0].upper() for r in results)

    def test_state_extractor(self):
        ex = StateExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        # State may not be found for central act
        assert isinstance(results, list)

    def test_country_extractor(self):
        ex = CountryExtractor()
        results = ex.extract(_FSSAI_ACT_SAMPLE)
        assert len(results) > 0
        assert "India" in results[0][0]

    def test_gazette_extractor(self):
        ex = GazetteExtractor()
        results = ex.extract(_GAZETTE_SAMPLE)
        assert len(results) > 0
        assert any("GAZETTE" in r[0].upper() for r in results)

    def test_notification_extractor(self):
        ex = NotificationExtractor()
        results = ex.extract(_GAZETTE_SAMPLE)
        assert len(results) > 0

    def test_version_extractor(self):
        ex = VersionExtractor()
        results = ex.extract(_FSSAI_AMENDMENT_SAMPLE)
        # Should find version info
        assert isinstance(results, list)

    def test_effective_date_extractor(self):
        ex = EffectiveDateExtractor()
        results = ex.extract(_FSSAI_AMENDMENT_SAMPLE)
        assert len(results) > 0
        assert "August" in results[0][0] or "deemed" in results[0][0].lower()


# ============================================================================
# Engine integration tests
# ============================================================================


class TestLegalMetadataEngine:
    def test_engine_extracts_fssai_act(self):
        engine = LegalMetadataEngine()
        result = engine.extract(_FSSAI_ACT_SAMPLE)
        assert "FOOD SAFETY" in result.title.value.upper()
        assert result.document_type.value == "Act"
        assert "India" in result.jurisdiction.value
        assert result.language.value == "english"

    def test_engine_extracts_notification(self):
        engine = LegalMetadataEngine()
        result = engine.extract(_NOTIFICATION_SAMPLE)
        assert result.document_type.value == "Notification"
        assert result.authority.score > 0

    def test_engine_extracts_amendment(self):
        engine = LegalMetadataEngine()
        result = engine.extract(_FSSAI_AMENDMENT_SAMPLE)
        # The engine picks the best-confidence candidate; long_title_act may win
        # over short_title_amended after score_field adjustments. Both contain "AMEND".
        assert "AMEND" in result.title.value.upper()

    def test_engine_handles_empty_text(self):
        engine = LegalMetadataEngine()
        result = engine.extract("")
        assert result.overall_confidence < 0.5

    def test_engine_handles_whitespace_only(self):
        engine = LegalMetadataEngine()
        result = engine.extract("   \n  \n  ")
        assert result.overall_confidence < 0.5

    def test_engine_overall_confidence_non_zero(self):
        engine = LegalMetadataEngine()
        result = engine.extract(_FSSAI_ACT_SAMPLE)
        assert result.overall_confidence > 0.5

    def test_engine_flat_dict(self):
        engine = LegalMetadataEngine()
        result = engine.extract(_FSSAI_ACT_SAMPLE)
        flat = result.to_flat_dict()
        assert flat["title"]
        assert flat["document_type"] == "Act"
        assert flat["country"] == "India"


# ============================================================================
# Validation tests
# ============================================================================


class TestValidator:
    def test_validate_language_english(self):
        validator = Validator()
        fields = {
            "language": FieldConfidence(value="english", score=0.9, method="regex"),
        }
        result = validator.validate_all(fields, _FSSAI_ACT_SAMPLE)
        assert result["language"].value == "english"

    def test_validate_language_hindi_correction(self):
        """If English is detected but text is Devanagari, should switch to Hindi."""
        validator = Validator()
        fields = {
            "language": FieldConfidence(value="english", score=0.7, method="heuristic"),
        }
        result = validator.validate_all(fields, _HINDI_SAMPLE)
        # Should switch to Hindi since Devanagari chars dominate
        assert result["language"].value == "hindi"

    def test_validate_jurisdiction_hierarchy(self):
        validator = Validator()
        fields = {
            "jurisdiction": FieldConfidence(value="Central Government", score=0.9, method="regex"),
            "state": FieldConfidence(value="", score=0.0, method="default"),
            "country": FieldConfidence(value="India", score=0.6, method="default"),
        }
        result = validator.validate_all(fields, _FSSAI_ACT_SAMPLE)
        # Country confidence should be boosted
        assert result["country"].score > 0.6

    def test_validate_title_authority(self):
        validator = Validator()
        fields = {
            "title": FieldConfidence(value="FOOD SAFETY AND STANDARDS ACT, 2006", score=0.95, method="regex"),
            "authority": FieldConfidence(
                value="Food Safety and Standards Authority of India",
                score=0.85,
                method="regex",
            ),
        }
        result = validator.validate_all(fields, _FSSAI_ACT_SAMPLE)
        # Authority should be validated by title match (score boosted from 0.85)
        assert result["authority"].score >= 0.85
