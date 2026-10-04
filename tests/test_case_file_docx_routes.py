"""TDD tests for case-file (sample adjudication) DOCX download.

Seam: routes.py download endpoints (/docx/petition, /docx/permission, /docx/zip).

Mirrors tests/test_adjudication_docx_routes.py: the sample-track routes must
render the ADR-001 .adoc templates (not the old boilerplate word_converter)
and must not leak unsubstituted placeholders.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

import pytest

from app import create_app
from app.models import CaseFile


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with app.app_context():
            from werkzeug.security import generate_password_hash

            from app.extensions import db
            from app.models.auth import User

            existing = User.query.filter_by(username="tdduser").first()
            if existing:
                db.session.delete(existing)
                db.session.commit()

            user = User(username="tdduser", password_hash=generate_password_hash("tddpass"), is_admin=True)
            db.session.add(user)
            db.session.commit()
            c.post("/auth/login", data={"username": "tdduser", "password": "tddpass"}, follow_redirects=True)
            yield c


@pytest.fixture
def case_file(client):
    """Create and return a test CaseFile (sample track) record."""
    from app.extensions import db

    case = CaseFile(
        case_number="TDD-SAMPLE-001",
        food_safety_officer_name="TDD Officer",
        authorization_date=date(2023, 4, 1),
        inspection_date=date(2023, 3, 1),
        inspection_time="12:40",
        manufacturer_fssai="10012345678901",
        manufacturer_name="TDD Manufacturer",
        manufacturer_fbo_name="TDD Mfg FBO",
        manufacturer_address="TDD Mfg Address",
        retailer_fssai="20012345678901",
        retailer_name="TDD Retailer",
        retailer_fbo_name="TDD Ret FBO",
        retailer_address="TDD Ret Address",
        product_name="TDD Product",
        batch_no="TDD-BATCH-1",
        sample_quantity="1000g",
        packet_count=4,
        mfg_date=date(2023, 1, 1),
        expiry_date=date(2024, 1, 1),
        other_food_articles="Biscuits, Chips",
        total_cost="250",
        cost_in_words="Two Hundred Fifty Rupees Only",
        sample_code="TDD-SL-001",
        sample_submission_date=date(2023, 3, 2),
        Lab_Registration_No="WB/FOOD/2023/001",
        do_receipt_date=date(2023, 3, 4),
        is_misbranded=True,
        is_substandard=False,
        analyst_report_no="TDD/AR/1",
        analyst_report_date=date(2023, 3, 10),
        directive_letter_no="TDD/DL/1",
        directive_letter_date=date(2023, 3, 12),
        retailer_report_receive_date=date(2023, 3, 14),
        manufacturer_report_receive_date=date(2023, 3, 15),
    )
    db.session.add(case)
    db.session.commit()
    yield case
    db.session.delete(case)
    db.session.commit()


# ── Helpers ──────────────────────────────────────────────────────────────


def _extract_docx_text(docx_bytes: bytes) -> str:
    """Plain text from DOCX bytes (strip XML, collapse whitespace)."""
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf, zf.open("word/document.xml") as f:
        xml = f.read().decode("utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _brace_placeholders(text: str) -> list[str]:
    """Return all {word} or {{word}} tokens in text."""
    return re.findall(r"\{[\w\s]+\}|\{\{[\w\s]+\}\}", text)


# ── Petition DOCX ────────────────────────────────────────────────────────


class TestPetitionDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/petition"""

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml in DOCX. Contents: {names}"

    def test_docx_contains_case_number(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD-SAMPLE-001" in text, "Petition DOCX must contain the case_number value"

    def test_docx_contains_officer_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Petition DOCX must contain the food_safety_officer_name value"

    def test_docx_contains_product_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "TDD Product" in text, "Petition DOCX must contain the product_name value"

    def test_docx_contains_repaired_sections(self, client, case_file):
        """REGRESSION: petition.adoc was truncated — GROUNDS/PRAYER must render."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        assert "GROUNDS" in text, "Petition DOCX must contain the GROUNDS section"
        assert "PRAYER" in text, "Petition DOCX must contain the PRAYER section"

    def test_docx_no_literal_brace_placeholders(self, client, case_file):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Petition DOCX contains unsubstituted placeholders: {placeholders}"


# ── Permission Letter DOCX ────────────────────────────────────────────────


class TestPermissionDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/permission"""

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml in DOCX. Contents: {names}"

    def test_docx_contains_case_number(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        text = _extract_docx_text(response.data)
        assert "TDD-SAMPLE-001" in text, "Permission DOCX must contain the case_number value"

    def test_docx_contains_officer_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Permission DOCX must contain the food_safety_officer_name value"

    def test_docx_no_literal_brace_placeholders(self, client, case_file):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Permission DOCX contains unsubstituted placeholders: {placeholders}"


# ── ZIP with both DOCX ───────────────────────────────────────────────────


class TestBothDocxZip:
    """Seam: GET /case_file_generator/case/<id>/docx/zip"""

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_zip_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        assert response.content_type == "application/zip", f"Expected application/zip, got {response.content_type}"

    def test_zip_contains_petition_docx(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        petition_files = [n for n in names if "Petition" in n and n.endswith(".docx")]
        assert petition_files, f"No Petition .docx in ZIP. Contents: {names}"

    def test_zip_contains_permission_docx(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        permission_files = [n for n in names if "Permission" in n and n.endswith(".docx")]
        assert permission_files, f"No Permission .docx in ZIP. Contents: {names}"

    def test_zip_petition_docx_no_placeholders(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = [n for n in zf.namelist() if "Petition" in n and n.endswith(".docx")]
            assert names, "No petition docx found"
            with zf.open(names[0]) as f:
                docx_bytes = f.read()
        text = _extract_docx_text(docx_bytes)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"ZIP petition DOCX has unsubstituted placeholders: {placeholders}"

    def test_zip_permission_docx_no_placeholders(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = [n for n in zf.namelist() if "Permission" in n and n.endswith(".docx")]
            assert names, "No permission docx found"
            with zf.open(names[0]) as f:
                docx_bytes = f.read()
        text = _extract_docx_text(docx_bytes)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"ZIP permission DOCX has unsubstituted placeholders: {placeholders}"


# ── Unsafe File DOCX (prohibition order) ─────────────────────────────────


class TestUnsafeFileDocxDownload:
    """Seam: GET /case_file_generator/case/<id>/docx/unsafe_file

    The Unsafe_file.adoc template is the prohibition-order document for a sample
    found UNSAFE. It is wired into the sample-adjudication UI as a new per-case
    option and must render from CaseFile data entered via that UI (with the
    fso_name / sample_name aliases resolved by the route).
    """

    def test_returns_200(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_returns_docx_mimetype(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        assert response.content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document", (
            f"Expected .docx mimetype, got {response.content_type}"
        )

    def test_docx_is_valid_zip(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        assert response.data[:4] == b"PK\x03\x04", "DOCX must be a valid ZIP archive"

    def test_docx_has_word_document_xml(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            names = zf.namelist()
        assert "word/document.xml" in names, f"Missing word/document.xml: {names}"

    def test_docx_contains_sample_code(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "TDD-SL-001" in text, "Unsafe File DOCX must contain the sample_code value"

    def test_docx_contains_officer_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "TDD Officer" in text, "Unsafe File DOCX must contain the fso_name (food_safety_officer_name)"

    def test_docx_contains_product_name(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "TDD Product" in text, "Unsafe File DOCX must contain the sample_name (product_name)"

    def test_docx_contains_prohibition_reference(self, client, case_file):
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "36(3)(b)" in text, "Unsafe File DOCX must reference Section 36(3)(b)"

    def test_docx_no_literal_brace_placeholders(self, client, case_file):
        """REGRESSION: docx must not contain unsubstituted {field_name} tokens."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        placeholders = _brace_placeholders(text)
        assert not placeholders, f"Unsafe File DOCX contains unsubstituted placeholders: {placeholders}"

    def test_unknown_case_returns_404(self, client):
        # Mirrors the sibling petition/permission DOCX routes, which use
        # CaseFile.query.get_or_404 -> an HTML 404 page (these routes are only
        # reached from the per-case UI button, so unknown IDs are not a real
        # path; the JSON-404 handler is only attached to the /pdf/ API route).
        response = client.get("/case_file_generator/case/999999/docx/unsafe_file")
        assert response.status_code == 404

    def test_docx_renders_real_word_table(self, client, case_file):
        """The ADR-001 fallback must emit real Word grid tables (<w:tbl>),
        not cells flattened into joined paragraphs — so page tables render as
        true tables even without pandoc."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        assert "<w:tbl" in xml, "Unsafe File DOCX must contain a real Word <w:tbl> table"
        assert xml.count("<w:tr") >= 2, "Unsafe File DOCX must contain table rows"

    def test_docx_has_no_unsubstituted_placeholders(self, client, case_file):
        """No leaked Jinja placeholders in the generated Unsafe File DOCX —
        every {{ case_number }} / {{ product_name }} etc. must be substituted."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        with zipfile.ZipFile(io.BytesIO(response.data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        assert "{{" not in xml and "}}" not in xml, (
            "Unsafe File DOCX must not contain unsubstituted Jinja {{ }} placeholders"
        )

    def test_docx_contains_14_column_table_on_page_1(self, client, case_file):
        """Page 1 has 14-column table with offender/sample details, no header table."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "SI. no" in text, "14-column table SI. no column must render"
        assert "Name of Offenders" in text, "14-column table Name of Offenders must render"
        assert "FSSAI license no" in text, "14-column table FSSAI license no must render"
        assert "Result of analysis" in text, "14-column table Result of analysis must render"
        assert "Remarks" in text, "14-column table Remarks must render"
        # Verify no letterhead table on page 1
        assert "OFFICE OF THE DESIGNATED OFFICER" not in text, "letterhead table must not be on page 1"
        assert "PROHIBITION ORDER UNDER SEC 36(3)(b)" not in text, "prohibition order subject must not be on page 1 (moved to body)"

    def test_docx_14_column_table_fill_rules(self, client, case_file):
        """Fill rules: Packed state (separate FBOs), report dates, Sec 46(4)
        preference No, referral columns blank, fixed remarks."""
        from app.extensions import db

        case_file.is_unsafe = True
        db.session.commit()

        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        # Separate retailer + manufacturer → sample state "Packed", two offender rows
        assert "Packed" in text, "sample state must be Packed for separate FBOs"
        assert "TDD Retailer" in text, "retailer row must render"
        assert "TDD Manufacturer" in text, "manufacturer row must render"
        # Report-communication column carries only the receive dates from
        # case data (retailer_report_receive_date, manufacturer_report_receive_date)
        assert "14-03-2023; 15-03-2023" in text, (
            "report-communication cell must list only the two receive dates"
        )
        # No hardcoded Yes prefix
        assert "Yes (Retailer" not in text, "no hardcoded Yes/Retailer labels"
        # Sec 46(4) preference always No, referral columns blank → No is
        # immediately followed by the remarks cell. ("FS&S" is XML-escaped as
        # "FS&amp;S" in the extracted text, so assert up to "FS".)
        assert "No seeking permission u/s 42(3) of FS" in text, (
            "preference must be No with blank referral cells before remarks"
        )

    def test_docx_letter_body_contains_sample_data(self, client, case_file):
        """Page 2 letter body carries sample data inline (no duplicate sample table —
        page 1's 14-column table is the only sample table)."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        for label, value in [
            ("Sample", "TDD Product"),
            ("Batch No", "TDD-BATCH-1"),
            ("Sample Code", "TDD-SL-001"),
            ("Retailer", "TDD Retailer"),
            ("Manufacturer", "TDD Manufacturer"),
        ]:
            assert label in text, f"letter body missing label '{label}'"
            assert value in text, f"letter body missing value '{value}'"

    def test_docx_contains_case_number(self, client, case_file):
        """The prohibition order must reference the case number (letter body)."""
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "TDD-SAMPLE-001" in text, "case_number must render in the unsafe file"

    def test_no_framepage_leak(self, client, case_file):
        """The old <framepage> passthrough must not leak as visible text.

        Pandoc swallows only the first ++++ block; a second one is emitted
        into the document body.
        """
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        text = _extract_docx_text(response.data)
        assert "framepage" not in text, "framepage XML leaked into the document"
        assert "simple-page-settings" not in text, "page-settings XML leaked into the document"

    def test_first_table_rows_have_14_columns(self, client, case_file):
        """Page-1 table: header + offender rows each hold exactly 14 cells.

        Asserted per-row on the real Word table so intentionally blank cells
        (referral columns) cannot silently shift values into wrong columns.
        """
        from docx import Document

        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        doc = Document(io.BytesIO(response.data))
        assert doc.tables, "page-1 offender table must render as a Word table"
        table = doc.tables[0]
        # Header + retailer + manufacturer (separate FBOs in the fixture).
        assert len(table.rows) == 3, "header + 2 offender rows expected"
        for index, row in enumerate(table.rows):
            assert len(row.cells) == 14, f"row {index} has {len(row.cells)} cells, want 14"

    def test_page1_landscape_rest_portrait(self, client, case_file):
        """Section 1 (page 1) must be landscape; the remainder portrait."""
        from docx import Document

        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/unsafe_file")
        doc = Document(io.BytesIO(response.data))
        sections = doc.sections
        assert len(sections) == 2, f"expected 2 sections, got {len(sections)}"
        first = sections[0]
        assert first.page_width > first.page_height, "page 1 must be landscape"
        last = sections[-1]
        assert last.page_height > last.page_width, "pages after page 1 must be portrait"


class TestUnsafeCaseDocumentGating:
    """Unsafe cases serve only the prohibition order.

    Petition, permission, both-ZIP and petition-PDF endpoints must 403, and
    the case list must stop offering those buttons — none of these guards can
    run on the shared non-unsafe fixture, so each gets its own case.
    """

    @staticmethod
    def _mark_unsafe(case_file):
        from app.extensions import db

        case_file.is_unsafe = True
        db.session.commit()

    def test_petition_docx_forbidden(self, client, case_file):
        self._mark_unsafe(case_file)
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/petition")
        assert response.status_code == 403
        assert b"not available for unsafe cases" in response.data

    def test_permission_docx_forbidden(self, client, case_file):
        self._mark_unsafe(case_file)
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/permission")
        assert response.status_code == 403
        assert b"not available for unsafe cases" in response.data

    def test_both_zip_forbidden(self, client, case_file):
        self._mark_unsafe(case_file)
        response = client.get(f"/case_file_generator/case/{case_file.id}/docx/zip")
        assert response.status_code == 403
        assert b"not available for unsafe cases" in response.data

    def test_petition_pdf_forbidden(self, client, case_file):
        self._mark_unsafe(case_file)
        response = client.get(f"/case_file_generator/case/{case_file.id}/pdf/petition")
        assert response.status_code == 403
        assert b"not available for unsafe cases" in response.data

    def test_index_hides_petition_permission_buttons(self, client, case_file):
        self._mark_unsafe(case_file)
        response = client.get("/case_file_generator/")
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert "/docx/petition" not in html, "Petition button must hide for unsafe cases"
        assert "/docx/permission" not in html, "Permission button must hide for unsafe cases"
        assert "/pdf/petition" not in html, "Petition PDF button must hide for unsafe cases"
        assert "/docx/unsafe_file" in html, "Unsafe File button must show for unsafe cases"


class TestUnsafeFileButtonGating:
    """The "Unsafe" verdict toggle gates the Unsafe-File download option.

    Mirrors the user's requirement: the Unsafe File is generated once the
    unsafe option is toggled — i.e. the option exists in the adjudication UI
    and the per-case "Unsafe File" button only appears when ``is_unsafe``
    is set on the case.

    Note: By default (``CASE_FILE_UNSAFE_OPTION_ENABLED = True``) the
    'Unsafe' verdict toggle is shown on the create/edit forms. It is an
    opt-out switch: only an explicit ``false`` hides it.
    """

    def test_create_form_exposes_unsafe_option(self, client, case_file, monkeypatch):
        """The sample-adjudication create form gates the Unsafe checkbox on the setting.

        The setting is opt-out (``CASE_FILE_UNSAFE_OPTION_ENABLED = True``), so
        ``name="is_unsafe"`` renders by default; setting the flag to ``false``
        hides it.
        """
        response = client.get("/case_file_generator/")
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert 'name="is_unsafe"' in html, (
            "Unsafe checkbox must render by default (opt-out setting)"
        )

        monkeypatch.setitem(
            client.application.config, "CASE_FILE_UNSAFE_OPTION_ENABLED", False
        )
        response = client.get("/case_file_generator/")
        html = response.data.decode("utf-8", errors="replace")
        assert 'name="is_unsafe"' not in html, (
            "Unsafe checkbox must hide once the setting is explicitly false"
        )

    def test_unsafe_file_button_hidden_until_toggled(self, client, case_file):
        """No Unsafe-File button until the case is marked unsafe.

        The case IS listed (TDD-SAMPLE-001 appears) — we only prove the
        button is gated off while ``is_unsafe`` is False.
        """
        response = client.get("/case_file_generator/")
        html = response.data.decode("utf-8", errors="replace")
        assert "TDD-SAMPLE-001" in html, "case row must be listed"
        # The per-case Unsafe File button is gated on the case's own is_unsafe
        # flag, not on the settings-page toggle.
        assert "/docx/unsafe_file" not in html, (
            "Unsafe File button must be hidden until the case is marked unsafe"
        )

    def test_unsafe_file_button_shown_when_unsafe(self, client, case_file):
        """Toggling the Unsafe option makes the Unsafe-File button appear.

        ``url_for('...download_unsafe_file_docx')`` renders as the
        ``/docx/unsafe_file`` path, so we assert on that path (not the
        endpoint name, which never appears in the markup).
        """
        from app.extensions import db

        case_file.is_unsafe = True
        db.session.commit()
        response = client.get("/case_file_generator/")
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert "TDD-SAMPLE-001" in html, "case row must be listed"
        assert "/docx/unsafe_file" in html, (
            "Unsafe File button must appear once is_unsafe is toggled on"
        )

    def test_edit_form_gates_unsafe_checkbox(self, client, case_file, monkeypatch):
        """The edit form shows the Unsafe checkbox unless the setting is false."""
        url = f"/case_file_generator/case/{case_file.id}/edit"
        response = client.get(url)
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert 'name="is_unsafe"' in html, (
            "edit form must show the Unsafe checkbox by default (opt-out setting)"
        )

        monkeypatch.setitem(
            client.application.config, "CASE_FILE_UNSAFE_OPTION_ENABLED", False
        )
        response = client.get(url)
        assert response.status_code == 200
        html = response.data.decode("utf-8", errors="replace")
        assert 'name="is_unsafe"' not in html, (
            "edit form must hide the Unsafe checkbox once set to false"
        )


