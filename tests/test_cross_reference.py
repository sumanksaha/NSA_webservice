"""Tests for the Phase 6 automatic cross-reference engine.

Covers:
1. Reference extraction (paragraph / annexure / section patterns)
2. Annexure metadata linking (DB-backed resolution by letter + index)
3. Renumbering passes (plain-text list markers, HTML `<ol start>` continuations,
   annexure letter reassignment)
4. Auto "List of Enclosures" generation + placeholder injection
5. PDF-pipeline wiring helpers in pdf_utils (defensive no-ops on failure)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

os.environ.setdefault("SKIP_ANNEXURE_OCR", "1")

from app.cross_reference import CrossReferenceEngine, ReferenceKind
from app.extensions import db
from app.models import FSO, Annexure, CaseFile, User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_client():
    """Test client with DB context, a case file, and a logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()

            user = User(
                username="xrefuser",
                password_hash="pbkdf2:sha256$test$dummy",  # noqa: S106
            )
            db.session.add(user)

            fso = FSO(fso_name="Test Officer")
            db.session.add(fso)

            case_file = CaseFile(
                case_number="XREF001",
                food_safety_officer_name="Test Officer",
                authorization_date=datetime(2026, 7, 3),
                inspection_date=datetime(2026, 7, 3),
                inspection_time="10:00",
                manufacturer_fssai="MFG123",
                manufacturer_name="Acme Foods Ltd",
                manufacturer_fbo_name="Acme FBO",
                manufacturer_address="123 Mfg St",
                retailer_fssai="RET456",
                retailer_name="Test Retailer",
                retailer_fbo_name="Retailer FBO",
                retailer_address="456 Retail St",
                product_name="Cotton Candy",
                batch_no="BATCH001",
                sample_quantity="1000g",
                packet_count=4,
                mfg_date=datetime(2026, 6, 1),
                expiry_date=datetime(2026, 8, 1),
                sample_code="XREF001",
                sample_submission_date=datetime(2026, 7, 2),
                Lab_Registration_No="WB/FOOD/2025/001",
                do_receipt_date=datetime(2026, 7, 4),
                is_misbranded=False,
                is_substandard=False,
                analyst_report_no="PK/378/2025-26",
                analyst_report_date=datetime(2026, 7, 5),
                directive_letter_no="H/FSSA/FSO/3054/2025-26",
                directive_letter_date=datetime(2026, 7, 6),
                retailer_report_receive_date=datetime(2026, 7, 7),
                manufacturer_report_receive_date=datetime(2026, 7, 8),
                applicable_regulation="Regulation No 5(9)",
                applicable_clause="Clause (zf) of section 3",
                applicable_sections="Sec 3",
            )
            db.session.add(case_file)
            db.session.commit()
            case_file_id = case_file.id

            yield client, case_file_id

            db.session.remove()
            db.drop_all()


def _add_annexure(case_id, caption, letter, page_count=None):
    ann = Annexure(
        case_id=case_id,
        caption=caption,
        date=datetime.now(UTC),
        file_hash=f"hash-{letter}-{caption}",
        page_count=page_count,
        filepath=f"/tmp/{caption}.txt",
        filename=f"{caption}.txt",
        annexure_letter=letter,
    )
    db.session.add(ann)
    db.session.commit()
    return ann


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_extracts_annexure_letters(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("The lab report is at Annexure A. See also Annexure B and Annexure-C.")
        annexures = [r for r in refs if r.kind is ReferenceKind.ANNEXURE]
        targets = [r.target for r in annexures]
        assert "A" in targets
        assert "B" in targets
        assert "C" in targets

    def test_extracts_numeric_annexure_refs(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("As shown in Annexure 1 and Annexure 3.")
        annexures = [r for r in refs if r.kind is ReferenceKind.ANNEXURE]
        assert sorted(r.target for r in annexures) == ["1", "3"]

    def test_extracts_section_runs(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("Liability under Section 55 and Sections 56, 58 and 64 of the Act.")
        sections = {r.target for r in refs if r.kind is ReferenceKind.SECTION}
        assert {"55", "56", "58", "64"}.issubset(sections)

    def test_extracts_subclause_sections(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("Contravention of Section 26(2)(ii) and u/s 63.")
        targets = {r.target for r in refs if r.kind is ReferenceKind.SECTION}
        assert any(t.startswith("26") for t in targets)
        assert "63" in targets

    def test_extracts_paragraph_word_refs(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("See paragraph 3 above; refer to clause 2 of the schedule.")
        paras = [r for r in refs if r.kind is ReferenceKind.PARAGRAPH]
        assert sorted(r.target for r in paras) == ["2", "3"]

    def test_extracts_numbered_list_markers(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("1. First ground\n(2) Second ground\n3. Third ground")
        paras = [r for r in refs if r.kind is ReferenceKind.PARAGRAPH]
        assert sorted(r.target for r in paras) == ["1", "2", "3"]

    def test_plural_annexures_not_matched(self):
        engine = CrossReferenceEngine()
        refs = engine.extract_references("All annexures are attached to this petition, see Annexure A.")
        targets = [r.target for r in refs if r.kind is ReferenceKind.ANNEXURE]
        assert targets == ["A"]

    def test_empty_text_returns_empty(self):
        engine = CrossReferenceEngine()
        assert engine.extract_references("") == []
        assert engine.extract_references(None) == []


# ---------------------------------------------------------------------------
# Linking (DB-backed)
# ---------------------------------------------------------------------------


class TestLinking:
    def test_resolves_annexure_refs_by_letter(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            _add_annexure(case_id, "Lab Report", "A", page_count=5)
            _add_annexure(case_id, "Inspection Note", "B", page_count=2)

            report = CrossReferenceEngine().link_references(
                "The details are in Annexure A and Annexure B.",
                case_id=case_id,
            )
            resolved = report["resolved"]
            annexure_letters = {r["resolved"]["annexure_letter"] for r in resolved if r["resolved"]}
            assert {"A", "B"}.issubset(annexure_letters)
            page_counts = {r["resolved"]["page_count"] for r in resolved if r["resolved"]}
            assert 5 in page_counts and 2 in page_counts

    def test_flags_unresolved_annexure_refs(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            _add_annexure(case_id, "Only One", "A")

            report = CrossReferenceEngine().link_references(
                "Refer to Annexure A and Annexure Z.",
                case_id=case_id,
            )
            unresolved_targets = [r["target"] for r in report["unresolved"]]
            assert "Z" in unresolved_targets
            assert "A" not in unresolved_targets

    def test_section_refs_marked_known(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            report = CrossReferenceEngine().link_references(
                "Penalty under Section 55 and an unknown Section 999.",
                case_id=case_id,
            )
            known = {r["target"]: r["resolved"]["known"] for r in report["resolved"] if r["kind"] == "section"}
            assert known.get("55") is True
            assert known.get("999") is False


# ---------------------------------------------------------------------------
# Renumbering
# ---------------------------------------------------------------------------


class TestRenumbering:
    def test_renumber_paragraph_dot_markers(self):
        engine = CrossReferenceEngine()
        # Insert a new item between 1 and 2 → renumber sequentially.
        edited = "1. First\nX. Inserted\n2. Second\n3. Third"
        result = engine.renumber_paragraphs(edited)
        assert result.startswith("1. First\n2. Inserted\n3. Second\n4. Third")

    def test_renumber_paragraph_paren_markers(self):
        engine = CrossReferenceEngine()
        edited = "(1) First\n(9) Inserted\n(2) Second"
        result = engine.renumber_paragraphs(edited)
        assert result.startswith("(1) First\n(2) Inserted\n(3) Second")

    def test_renumber_leaves_continuation_lists_alone(self):
        engine = CrossReferenceEngine()
        # First marker is 4 → a continuation list, must not be clobbered.
        text = "4. Fourth\n5. Fifth"
        assert engine.renumber_paragraphs(text) == text

    def test_renumber_html_continuation_start(self):
        engine = CrossReferenceEngine()
        html = (
            '<ol class="justify"><li>A</li><li>B</li><li>C</li></ol>'
            '<ol class="justify" start="4"><li>D</li><li>E</li></ol>'
        )
        result = engine.renumber_html_lists(html)
        assert 'start="4"' in result
        assert "D" in result

    def test_renumber_html_updates_stale_start_after_delete(self):
        engine = CrossReferenceEngine()
        # Original: first list had 3 items → continuation starts at 4.
        # After an item is deleted the continuation must start at 3.
        html = '<ol class="justify"><li>A</li><li>B</li></ol><ol class="justify" start="4"><li>C</li></ol>'
        result = engine.renumber_html_lists(html)
        assert 'start="3"' in result

    def test_renumber_html_leaves_plain_lists_untouched(self):
        engine = CrossReferenceEngine()
        html = '<ol class="justify"><li>A</li><li>B</li></ol>'
        assert engine.renumber_html_lists(html) == html

    def test_renumber_html_leaves_mid_sequence_doc_alone(self):
        engine = CrossReferenceEngine()
        # A regenerated fragment that legitimately starts mid-list must not be clobbered.
        html = '<ol class="justify" start="4"><li>D</li><li>E</li></ol>'
        assert engine.renumber_html_lists(html) == html

    def test_renumber_html_ignores_unrelated_lists(self):
        engine = CrossReferenceEngine()
        # A bare (non-justify) list between the sequence must not disturb the counter.
        html = (
            '<ol class="justify"><li>A</li><li>B</li></ol>'
            "<ol><li>Witness</li></ol>"
            '<ol class="justify" start="3"><li>C</li></ol>'
        )
        result = engine.renumber_html_lists(html)
        assert 'start="3"' in result

    def test_renumber_annexures_reassigns_letters(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            first = _add_annexure(case_id, "First", "A")
            second = _add_annexure(case_id, "Second", "C")  # B was deleted

            updates = CrossReferenceEngine().renumber_annexures(case_id=case_id)
            assert len(updates) == 1
            assert updates[0]["annexure_id"] == second.id
            assert updates[0]["annexure_letter"] == "B"

            db.session.refresh(second)
            assert second.annexure_letter == "B"
            db.session.refresh(first)
            assert first.annexure_letter == "A"


# ---------------------------------------------------------------------------
# Enclosures + annotation
# ---------------------------------------------------------------------------


class TestEnclosures:
    def test_build_enclosures_html(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            _add_annexure(case_id, "Lab Report", "A", page_count=5)
            _add_annexure(case_id, "Inspection Note", "B", page_count=1)

            html = CrossReferenceEngine().build_enclosures_html(case_id=case_id)
            assert "<ol" in html
            assert "Lab Report" in html
            assert "Annexure A" in html
            assert "5 pages" in html
            assert "1 page" in html

    def test_build_enclosures_empty_without_annexures(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            assert CrossReferenceEngine().build_enclosures_html(case_id=case_id) == ""

    def test_annotate_html_fills_placeholder(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            _add_annexure(case_id, "Lab Report", "A", page_count=3)

            html = '<h3>List of Enclosures</h3><ol data-cross-reference="enclosures"></ol>'
            result = CrossReferenceEngine().annotate_html(html, case_id=case_id)
            assert "Lab Report" in result
            assert "Annexure A" in result
            assert 'data-cross-reference="enclosures"' not in result

    def test_annotate_html_leaves_marker_when_no_annexures(self, test_client):
        client, case_id = test_client
        with client.application.app_context():
            html = '<ol data-cross-reference="enclosures"></ol>'
            result = CrossReferenceEngine().annotate_html(html, case_id=case_id)
            assert 'data-cross-reference="enclosures"' in result


# ---------------------------------------------------------------------------
# PDF-pipeline wiring helpers
# ---------------------------------------------------------------------------


class TestPdfWiring:
    def test_post_process_pdf_html_defensive_on_failure(self):
        from app.utils.pdf_utils import post_process_pdf_html

        with patch("app.cross_reference.engine.CrossReferenceEngine") as mock_engine:
            mock_engine.side_effect = RuntimeError("boom")
            result = post_process_pdf_html("<html><body>Hi</body></html>")
            assert "<html><body>Hi</body></html>" in result

    def test_post_process_pdf_html_renumbers_lists(self):
        from app.utils.pdf_utils import post_process_pdf_html

        html = '<ol class="justify"><li>A</li><li>B</li></ol><ol class="justify" start="4"><li>C</li></ol>'
        result = post_process_pdf_html(html)
        assert 'start="3"' in result

    def test_renumber_html_lists_helper(self):
        from app.utils.pdf_utils import renumber_html_lists

        html = '<ol class="justify"><li>A</li></ol><ol class="justify" start="9"><li>B</li></ol>'
        assert 'start="2"' in renumber_html_lists(html)
