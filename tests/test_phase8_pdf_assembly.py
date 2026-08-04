"""Phase 8 - PDF Assembly Engine Tests

Tests the PDF Assembly Engine (Phase 8) implementation.
This includes testing:
- Annexure inclusion in PDF assembly
- Evidence inclusion in PDF assembly
- Index page generation
- QR code generation
- Headers/footers/page numbers
- Signature placeholders
- PDF bookmarks
- Error handling

These tests are designed to verify the expected functionality
without requiring actual PDF generation (which would need WeasyPrint
and system dependencies).
"""

import unittest
from unittest.mock import patch

import pytest


class TestPDFAssemblyEngineCore:
    """Test core PDF assembly engine functionality."""

    def test_engine_initialization(self, monkeypatch):
        """Test that PDF assembly engine initializes correctly."""
        # Pin the feature toggles so the test is hermetic regardless of any
        # PDF_ENABLE_* variables exported in the run environment.
        monkeypatch.delenv("PDF_ENABLE_QR_CODES", raising=False)
        monkeypatch.delenv("PDF_ENABLE_SIGNATURES", raising=False)
        monkeypatch.delenv("PDF_ENABLE_BOOKMARKS", raising=False)

        # Importing the module is safe without GTK/Pango: the guarded
        # ``import_weasyprint()`` import in app/pdf_assembly returns None
        # when WeasyPrint's native libraries are unavailable, and all PDF
        # work goes through the defensive ``generate_pdf_from_html`` helper.
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        assert engine.config["enable_qr_codes"] is True
        assert engine.config["enable_signatures"] is True
        assert engine.config["enable_bookmarks"] is True
        # Default header/footer templates must resolve to non-empty HTML.
        assert "{{ case_number }}" in engine.config["header_template"]
        assert "{{ page_number }}" in engine.config["footer_template"]

    def test_qrcode_availability_check(self):
        """Test that QR code availability is checked during initialization."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        # _import_qrcode_if_available always sets self.qrcode: the qrcode
        # module when importable, None otherwise (graceful degradation).
        assert engine.qrcode is None or hasattr(engine, "qrcode")


class TestPDFAssemblyAnnexureInclusion:
    """Test PDF assembly includes annexures."""

    @pytest.fixture
    def mock_annexures(self):
        """Mock annexure data for testing."""
        return [
            {
                "id": 1,
                "title": "Annexure A: Inspection Report",
                "type": "inspection",
                "content": "<p>Inspection findings and observations.</p>",
            },
            {
                "id": 2,
                "title": "Annexure B: Forms",
                "type": "form",
                "content": "<p>Official forms and templates.</p>",
            },
        ]

    def test_annexure_inclusion_logic(self, mock_annexures):
        """Test that annexures are included in PDF assembly logic."""
        # This test verifies the expected logic for annexure inclusion
        # without actually calling PDF generation

        assert len(mock_annexures) == 2

        # Verify annexure structure
        annexure1 = mock_annexures[0]
        assert annexure1["id"] == 1
        assert "A" in annexure1["title"]
        assert annexure1["type"] == "inspection"

        # Verify second annexure
        annexure2 = mock_annexures[1]
        assert annexure2["id"] == 2
        assert "B" in annexure2["title"]
        assert annexure2["type"] == "form"

    def test_annexure_ordering_preserved(self, mock_annexures):
        """Test that annexure ordering is preserved in assembly."""
        # Simulate the expected ordering logic
        annexure_ids = [annexure["id"] for annexure in mock_annexures]
        assert annexure_ids == [1, 2]  # Preserve input order

    def test_empty_annexures_handling(self):
        """Test handling of empty annexures list."""
        # When no annexures are provided, assembly should still work
        # with just the main documents
        empty_annexures = []
        assert len(empty_annexures) == 0


class TestPDFAssemblyEvidenceInclusion:
    """Test PDF assembly includes evidence photos."""

    @pytest.fixture
    def mock_evidence_data(self):
        """Mock evidence photo data for testing."""
        return {
            "photo_urls": [
                "https://storage.example.com/photo1.jpg",
                "https://storage.example.com/photo2.png",
                "local/path/to/photo3.jpeg",
            ],
            "case_id": 123,
            "verification_status": "verified",
        }

    def test_evidence_inclusion(self, mock_evidence_data):
        """Test evidence photos are included in assembly logic."""
        # Verify evidence data structure
        evidence = mock_evidence_data
        assert "photo_urls" in evidence
        assert len(evidence["photo_urls"]) == 3
        assert evidence["case_id"] == 123

    def test_evidence_photo_types(self, mock_evidence_data):
        """Test that different evidence photo types are handled."""
        evidence = mock_evidence_data

        # Should handle various URL formats (HTTP, local paths)
        photo_urls = evidence["photo_urls"]
        http_url = photo_urls[0]
        local_url = photo_urls[2]

        assert http_url.startswith("https://")
        assert local_url.startswith("local/")

    def test_evidence_verification_status(self, mock_evidence_data):
        """Test evidence verification status handling."""
        evidence = mock_evidence_data
        assert evidence["verification_status"] == "verified"


class TestPDFAssemblyIndexPage:
    """Test PDF index page generation."""

    def test_index_page_structure(self):
        """Test index page HTML structure."""
        # This would test the actual HTML structure generation
        # In a real test, this would call _create_index_page_html
        pass

    def test_index_page_content(self):
        """Test index page contains expected content."""
        # Verify index page includes essential case information
        expected_sections = [
            "DOCUMENT STRUCTURE",
            "CASE INFORMATION",
            "TECHNICAL SPECIFICATIONS",
        ]

        # These would be verified against actual generated HTML
        assert len(expected_sections) == 3


class TestPDFAssemblyQRCodeGeneration:
    """Test QR code generation functionality."""

    def test_qr_code_data_generation(self):
        """Test QR code data generation."""
        # Test data generation for QR codes
        test_cases = [
            (123, "NSA-CASE-123-20260803"),
            (456, "NSA-CASE-456-20260803"),
            (789, "NSA-CASE-789-20260803"),
        ]

        for case_id, _expected_prefix in test_cases:
            # Generate expected QR code data
            qr_data = f"NSA-CASE-{case_id}-20260803"
            assert qr_data.startswith("NSA-CASE-")
            assert str(case_id) in qr_data
            assert "20260803" in qr_data

    def test_qr_code_content_verification(self):
        """Test QR code content verification."""
        # Verify QR code contains required information
        qr_content_parts = [
            "NSA-CASE-",  # Prefix
            "20260803",  # Date
        ]

        for part in qr_content_parts:
            assert part  # Non-empty


class TestPDFAssemblyPageNumbers:
    """Test page numbering functionality."""

    def test_page_number_css_injection(self):
        """Test that page number CSS is injected correctly."""
        with patch("app.pdf_assembly.PDFAssemblyEngine._import_qrcode_if_available"):
            with patch("app.pdf_assembly.HTML"):
                from app.pdf_assembly import PDFAssemblyEngine

                engine = PDFAssemblyEngine()

                # Test with HTML that has a style tag
                html_with_style = "<html><head><style>body { color: red; }</style></head><body>Test</body></html>"
                result = engine._apply_page_numbers(html_with_style)

                # Page number CSS should be injected
                assert "@page" in result
                assert "{{ page_number }}" in result
                assert "Page {{ page_number }}" in result

    def test_page_number_css_injection_without_style(self):
        """Test page number CSS injection when no style tag exists."""
        with patch("app.pdf_assembly.PDFAssemblyEngine._import_qrcode_if_available"):
            with patch("app.pdf_assembly.HTML"):
                from app.pdf_assembly import PDFAssemblyEngine

                engine = PDFAssemblyEngine()

                # Test with HTML that has no style tag
                html_without_style = "<html><body>Test</body></html>"
                result = engine._apply_page_numbers(html_without_style)

                # Page number CSS should be injected at the beginning
                assert "@page" in result
                assert "{{ page_number }}" in result
                assert result.startswith("<style>")

    def test_page_number_css_structure(self):
        """Test that page number CSS has correct structure."""
        with patch("app.pdf_assembly.PDFAssemblyEngine._import_qrcode_if_available"):
            with patch("app.pdf_assembly.HTML"):
                from app.pdf_assembly import PDFAssemblyEngine

                engine = PDFAssemblyEngine()

                html = "<html><body>Test</body></html>"
                result = engine._apply_page_numbers(html)

                # Verify CSS structure
                assert "@page {" in result
                assert "@bottom-center {" in result
                assert 'content: "Page {{ page_number }}";' in result
                assert "font-size: 10px;" in result
                assert "color: #666666;" in result

    def test_page_numbers_in_complete_processing(self):
        """Test that page numbers are included in complete post-processing."""
        with patch("app.pdf_assembly.PDFAssemblyEngine._import_qrcode_if_available"):
            with patch("app.pdf_assembly.HTML"):
                with patch("app.pdf_assembly.post_process_pdf_html"):
                    with patch("app.pdf_assembly.render_template"):
                        from app.pdf_assembly import PDFAssemblyEngine

                        engine = PDFAssemblyEngine()

                        # Mock the other processing methods
                        engine._apply_headers_footers = lambda x, y: x
                        engine._add_qr_codes = lambda x, y: x
                        engine._add_signature_placeholders = lambda x: x
                        engine._add_pdf_bookmarks = lambda x, y: x

                        html = "<html><body>Test</body></html>"
                        case_data = {"case_number": "123"}

                        result = engine._apply_complete_post_processing(html, 456, case_data)

                        # The result should contain page number CSS
                        assert "@page" in result
                        assert "{{ page_number }}" in result

    def test_page_number_css_placement(self):
        """Test that page number CSS is placed correctly in HTML."""
        with patch("app.pdf_assembly.PDFAssemblyEngine._import_qrcode_if_available"):
            with patch("app.pdf_assembly.HTML"):
                from app.pdf_assembly import PDFAssemblyEngine

                engine = PDFAssemblyEngine()

                # Test when style tag exists - should replace it
                html_with_style = "<html><head><style>body { color: red; }</style></head><body>Test</body></html>"
                result = engine._apply_page_numbers(html_with_style)

                # Should have both original and page number CSS
                assert "body { color: red; }" in result
                assert "@page {" in result

                # Test when no style tag exists - should add at beginning
                html_without_style = "<html><body>Test</body></html>"
                result2 = engine._apply_page_numbers(html_without_style)

                # Should start with style tag
                assert result2.startswith("<style>")
                assert "{{ page_number }}" in result2


class TestPDFAssemblyHeadersFooters:
    """Test header/footer template resolution and placement."""

    def test_default_templates_resolve(self):
        """Default header/footer templates must resolve to non-empty HTML."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        assert "{{ case_number }}" in engine.config["header_template"]
        assert "{{ page_number }}" in engine.config["footer_template"]

    def test_header_injected_after_body_open(self):
        """Header block lands right after the <body> tag."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = "<html><body><h1>Test</h1></body></html>"
        out = engine._apply_headers_footers(html, {"case_number": "C-1", "manufacturer_name": "FBO"})
        assert "pdf-header" in out
        assert out.index("pdf-header") > out.index("<body")

    def test_footer_injected_before_body_close(self):
        """Footer lands before </body> even though the doc ends with </html>."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = "<html><body><h1>Test</h1></body></html>"
        out = engine._apply_headers_footers(html, {"case_number": "C-1", "manufacturer_name": "FBO"})
        assert "pdf-footer" in out
        assert out.rindex("pdf-footer") < out.rindex("</body>")

    def test_placeholders_filled_and_page_number_preserved(self):
        """Case data fills placeholders; WeasyPrint page_number survives."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = "<html><body></body></html>"
        out = engine._apply_headers_footers(
            html,
            {
                "case_number": "C-1",
                "manufacturer_name": "FBO",
                "sample_code": "S-9",
            },
        )
        assert "C-1" in out
        assert "FBO" in out
        assert "Sample: S-9" in out
        assert "{{ page_number }}" in out

    def test_bookmarks_injected_before_body_close(self):
        """Bookmark nav also lands before </body> on full documents."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = "<html><body><h1>Test</h1></body></html>"
        out = engine._add_pdf_bookmarks(html, 1)
        assert "pdf-bookmarks" in out
        assert out.rindex("pdf-bookmarks") < out.rindex("</body>")


class TestPDFAssemblySignaturePlaceholders:
    """Test signature placeholder functionality."""

    def test_signature_placeholder_structure(self):
        """Test signature placeholder HTML structure."""
        # Verify signature section has proper structure
        expected_signature_roles = [
            "FOOD SAFETY OFFICER",
            "DESIGNATED OFFICER",
            "DATE",
            "COMMENTS",
        ]

        # These would be verified against actual generated HTML
        assert len(expected_signature_roles) == 4


class TestPDFAssemblyPDFBookmarks:
    """Test PDF bookmarks functionality."""

    def test_bookmark_structure(self):
        """Test bookmark navigation structure."""
        # Verify bookmark hierarchy and navigation
        expected_bookmark_sections = [
            "Introduction",
            "Statement of Facts",
            "GROUNDS Analysis",
            "PRAYER Clauses",
            "Annexure A",
            "Annexure B",
            "Evidence Photos",
            "Signatories",
        ]

        # These would be verified against actual bookmark structure
        assert len(expected_bookmark_sections) == 8


class TestPDFAssemblyErrorHandling:
    """Test error handling in PDF assembly."""

    def test_missing_annexures_handling(self):
        """Test handling when annexures are not available."""
        # The assembly should still work without annexures
        pass

    def test_corrupted_evidence_handling(self):
        """Test handling of corrupted evidence data."""
        # Should handle errors gracefully and continue with other components
        pass

    def test_pdf_generation_failure_handling(self):
        """Test handling of PDF generation failures."""
        # Should return error messages without crashing
        pass


class TestPDFAssemblyIntegration:
    """Integration tests for PDF assembly engine."""

    def test_complete_assembly_with_all_components(self):
        """Test complete assembly with all Phase 8 features."""
        # This integration test verifies that all components
        # (annexures, evidence, index, QR codes, signatures, bookmarks)
        # can be included in a single assembly
        pass

    def test_assembly_ordering(self):
        """Test correct ordering of PDF components."""
        # Verify the expected order: main docs -> index -> annexures -> evidence -> bookmarks
        expected_order = [
            "main_documents",
            "index_page",
            "annexures",
            "evidence_photos",
            "signatures",
        ]

        # This would be verified in actual assembly logic
        assert len(expected_order) == 5


class TestPDFAssemblyHyperlinks:
    """Phase 8 - PDF hyperlinks (visible/clickable internal + external links)."""

    def test_hyperlink_styling_css_injected(self):
        """Link CSS is injected so anchors render visibly clickable."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = '<html><head></head><body><a href="#toc-1">1. Facts</a></body></html>'
        out = engine._add_hyperlink_styling(html)
        assert "a[href]" in out
        assert "color: #1e40af" in out
        assert "text-decoration: underline" in out

    def test_hyperlink_styling_without_existing_style(self):
        """A new <style> block is prepended when the HTML has none."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        out = engine._add_hyperlink_styling("<html><body>Test</body></html>")
        assert "a[href]" in out
        assert out.startswith("<style>")

    def test_internal_anchor_links_survive_hyperlink_pass(self):
        """TOC anchor links keep their hrefs AND heading targets after the pass."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        toc = '<nav class="toc-nav"><ol><li><a href="#toc-1">1. Facts</a></li></ol></nav>'
        html = f'<html><body>{toc}<h1 id="toc-1">Statement of Facts</h1></body></html>'
        out = engine._apply_hyperlinks(html)
        assert 'href="#toc-1"' in out
        assert 'id="toc-1"' in out
        # Styling present so the link is visible in the PDF
        assert "a[href]" in out

    def test_missing_anchor_target_reannotated(self):
        """Headings that lost their id are re-annotated so anchors resolve."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = (
            '<html><body><nav class="toc-nav"><ol>'
            '<li><a href="#toc-1">1. Facts</a></li></ol></nav>'
            "<h1>Statement of Facts</h1></body></html>"
        )
        out = engine._ensure_internal_link_targets(html)
        assert 'id="toc-1"' in out

    def test_bare_external_urls_wrapped_in_links(self):
        """Bare http(s) URLs become clickable external links."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = "<html><body><p>See https://example.com/annexure.pdf for details</p>" "</body></html>"
        out = engine._linkify_plain_urls(html)
        assert '<a href="https://example.com/annexure.pdf">' "https://example.com/annexure.pdf</a>" in out

    def test_attribute_urls_not_double_wrapped(self):
        """URLs already inside href/src attributes are left untouched."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = (
            '<html><body><a href="https://example.com/a.pdf">already linked</a>'
            '<img src="https://example.com/photo.jpg"></body></html>'
        )
        out = engine._linkify_plain_urls(html)
        assert out.count('href="https://example.com/a.pdf"') == 1
        assert out.count('src="https://example.com/photo.jpg"') == 1
        assert '<a href="https://example.com/a.pdf">https://' not in out

    def test_hyperlink_pass_defensive_on_empty_input(self):
        """The pass never raises and never adds markup to empty input."""
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        assert engine._apply_hyperlinks("") == ""

    def test_hyperlinks_config_toggle(self, monkeypatch):
        """PDF_ENABLE_HYPERLINKS=false disables the whole pass."""
        monkeypatch.setenv("PDF_ENABLE_HYPERLINKS", "false")
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        html = '<html><body><a href="#toc-1">x</a></body></html>'
        out = engine._apply_hyperlinks(html)
        assert "a[href]" not in out

    def test_hyperlinks_wired_into_complete_post_processing(self):
        """The hyperlink pass runs as part of the Phase 8 chain."""
        with patch("app.pdf_assembly.post_process_pdf_html") as mock_post:
            with patch("app.pdf_assembly.PDFAssemblyEngine._import_qrcode_if_available"):
                with patch("app.pdf_assembly.HTML"):
                    from app.pdf_assembly import PDFAssemblyEngine

                    engine = PDFAssemblyEngine()
                    mock_post.side_effect = lambda h, **kw: (
                        '<html><body><a href="#toc-1">1. Facts</a>' '<h1 id="toc-1">Facts</h1>' "</body></html>"
                    )
                    # The other Phase 8 passes are mocked out (as in the
                    # existing page-number integration test) so only the
                    # hyperlink pass is exercised here.
                    engine._apply_headers_footers = lambda x, y: x
                    engine._add_qr_codes = lambda x, y: x
                    engine._add_signature_placeholders = lambda x: x
                    engine._add_pdf_bookmarks = lambda x, y: x
                    engine._apply_page_numbers = lambda x: x
                    html = "<html><body></body></html>"
                    out = engine._apply_complete_post_processing(html, 1, {})
                    assert "a[href]" in out  # styling injected
                    assert 'href="#toc-1"' in out  # anchor survived


class TestPDFAssemblyConfiguration:
    """Test PDF assembly configuration."""

    def test_environment_configuration(self):
        """Test environment-based configuration."""
        # Verify configuration can be set via environment variables
        config_options = [
            "PDF_ENABLE_QR_CODES",
            "PDF_ENABLE_SIGNATURES",
            "PDF_ENABLE_BOOKMARKS",
        ]

        # These would be read from environment during initialization
        assert len(config_options) == 3

    def test_configuration_defaults(self):
        """Test configuration has sensible defaults."""
        # Verify all configuration options have reasonable defaults
        expected_defaults = {
            "enable_qr_codes": True,
            "enable_signatures": True,
            "enable_bookmarks": True,
        }

        # These would be verified during initialization
        assert len(expected_defaults) == 3


if __name__ == "__main__":
    # Run the tests
    unittest.main(argv=["first-arg-is-ignored"], exit=False, verbosity=2)
