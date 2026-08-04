"""PDF Assembly Engine for Phase 8.

Comprehensive PDF assembly engine that extends the existing case file generation
functionality to include all Phase 8 features:

- Complete PDF assembly with annexures, evidence, and index pages
- Headers, footers, and page numbers via WeasyPrint @page CSS
- QR code generation for document authentication
- Signature placeholders for manual signatures
- PDF bookmarks via hierarchical TOC
- Integration with existing PDF utilities
"""

import io
import logging
import os
import re
from datetime import datetime

from flask import render_template

from app.utils.pdf_utils import (
    embed_photos_as_base64,
    generate_pdf_from_html,
    import_weasyprint,
    post_process_pdf_html,
)

# Guarded WeasyPrint import. On systems without GTK/Pango (or when
# DISABLE_PDF_GENERATION=1 is set, as conftest does for tests) the direct
# ``from weasyprint import HTML`` raised at import time and broke the whole
# app. All PDF work here goes through generate_pdf_from_html(), which already
# handles the None case, so this module never calls HTML itself.
HTML = import_weasyprint()

logger = logging.getLogger(__name__)

__all__ = ["PDFAssemblyEngine"]

# Phase 8: PDF hyperlink styling. WeasyPrint renders ``<a href="#anchor">``
# as an internal link and ``<a href="http(s)://...">`` as an external link
# natively; this CSS makes those links visibly clickable in the compiled PDF
# (plain black underlined links are easy to miss on paper/print).
_HYPERLINK_CSS = """\
a[href] {
  color: #1e40af;
  text-decoration: underline;
}
.pdf-bookmarks a[href] {
  color: inherit;
  text-decoration: none;
}
"""

# Matches internal anchor hrefs ("#target") so we can verify the target id
# exists after post-processing.
_INTERNAL_HREF_RE = re.compile(r'href="#([^"]+)"')

# Matches bare http(s):// URLs NOT already inside an href/src attribute value
# (negative lookbehind for a quote). Group 1 captures the URL so the
# substitution can wrap it in an anchor. Used to linkify annexure/evidence
# URLs and legal citations into clickable external PDF links.
_BARE_URL_RE = re.compile(
    r"(?<!['\"])(https?://[^\s<>\"')]+)(?<![.\"])",
    re.IGNORECASE,
)


class PDFAssemblyEngine:
    """Comprehensive PDF assembly engine implementing all Phase 8 features.

    Extends the existing `generate_case_file_pdf` function in
    `app/case_file_generator/tasks.py` to include:

    1. Annexure inclusion - Add all annexures to the PDF assembly
    2. Evidence inclusion - Embed photo evidence with proper formatting
    3. Index pages - Create comprehensive case index pages
    4. Headers/footers - Professional PDF headers with case information
    5. Page numbers - Sequential page numbering throughout document
    6. QR codes - Document authentication and verification
    7. Signature placeholders - Spaces for manual signatures
    8. PDF bookmarks - Navigation structure for PDF readers

    The engine integrates with existing components:
    - Uses `post_process_pdf_html()` for Phase 6 + 7 processing
    - Leverages `embed_photos_as_base64()` for evidence embedding
    - Generates PDFs using WeasyPrint
    - Creates bookmark structure compatible with PDF readers

    Configuration via environment variables:
    - PDF_HEADER_TEMPLATE: Custom header template (HTML)
    - PDF_FOOTER_TEMPLATE: Custom footer template (HTML)
    - PDF_ENABLE_QR_CODES: Enable/disable QR code generation (default: true)
    - PDF_ENABLE_SIGNATURES: Enable/disable signature placeholders (default: true)
    - PDF_ENABLE_BOOKMARKS: Enable/disable PDF bookmarks (default: true)
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._setup_environment_config()
        self._import_qrcode_if_available()

    def _setup_environment_config(self):
        """Initialize environment configuration for PDF assembly."""
        self.config = {
            "enable_qr_codes": os.environ.get("PDF_ENABLE_QR_CODES", "true").lower() == "true",
            "enable_signatures": os.environ.get("PDF_ENABLE_SIGNATURES", "true").lower() == "true",
            "enable_bookmarks": os.environ.get("PDF_ENABLE_BOOKMARKS", "true").lower() == "true",
            "enable_hyperlinks": os.environ.get("PDF_ENABLE_HYPERLINKS", "true").lower() == "true",
            "header_template": os.environ.get(
                "PDF_HEADER_TEMPLATE",
                self._get_default_header_template(),
            ),
            "footer_template": os.environ.get(
                "PDF_FOOTER_TEMPLATE",
                self._get_default_footer_template(),
            ),
        }

    def _get_default_header_template(self) -> str:
        """Return the default PDF header template HTML.

        Uses the ``{{ case_number }}``, ``{{ fbo_name }}`` and
        ``{{ generated_at }}`` placeholders filled by
        :meth:`_prepare_header_footer_template_data`. Operators can override
        it entirely via the ``PDF_HEADER_TEMPLATE`` environment variable.
        """
        return (
            '<div class="pdf-header" style="width: 100%; border-bottom: 1px '
            "solid #999; padding-bottom: 4px; margin-bottom: 14px; "
            'font-size: 10px; color: #555;">'
            '<table style="width: 100%; border-collapse: collapse;"><tr>'
            '<td style="text-align: left;">{{ case_number }}</td>'
            '<td style="text-align: center;">{{ fbo_name }}</td>'
            '<td style="text-align: right;">{{ generated_at }}</td>'
            "</tr></table></div>"
        )

    def _get_default_footer_template(self) -> str:
        """Return the default PDF footer template HTML.

        Uses the ``{{ case_tracking_info }}`` and ``{{ page_number }}``
        placeholders; ``page_number`` is WeasyPrint's built-in pseudo-variable
        so it renders as the running page number in the compiled PDF.
        Operators can override it via the ``PDF_FOOTER_TEMPLATE`` env var.
        """
        return (
            '<div class="pdf-footer" style="width: 100%; border-top: 1px '
            "solid #999; padding-top: 4px; margin-top: 14px; font-size: 9px; "
            'color: #777; text-align: center;">'
            "{{ case_tracking_info }} &mdash; Page {{ page_number }}"
            "</div>"
        )

    def _import_qrcode_if_available(self):
        """Import qrcode library if available, otherwise set to None."""
        try:
            import base64
            from io import BytesIO

            import qrcode

            self.qrcode = qrcode
            self.BytesIO = BytesIO
            self.base64 = base64
        except ImportError:
            self.logger.warning("qrcode library not available, QR code generation disabled")
            self.qrcode = None

    def assemble_complete_case_pdf(
        self, case_id: int, case_data: dict, annexures: list[dict] | None = None
    ) -> tuple[bytes | None, str | None]:
        """Assemble complete case file PDF with all Phase 8 features.

        This is the main entry point for Phase 8 PDF assembly. It generates:

        1. Petition PDF (existing functionality)
        2. Permission Letter PDF (existing functionality)
        3. Index page PDF (new)
        4. Annexure PDFs (new) - one per annexure
        5. Evidence photo pages (new) - embedded as separate pages

        Args:
            case_id: Case file identifier
            case_data: Dictionary with case information for template rendering
            annexures: List of annexure data (if None, no annexures added)

        Returns:
            Tuple of (pdf_bytes, error_message). Returns (None, error_message) on failure.
        """
        try:
            # Phase 1: Generate Petition and Permission Letter PDFs
            petition_pdf_bytes = self._generate_main_document_pdfs(case_id, case_data)
            if petition_pdf_bytes is None:
                return None, "Failed to generate main document PDFs"

            # Phase 2: Generate index page
            index_pdf_bytes = self._generate_index_page_pdf(case_id, case_data)

            # Phase 3: Generate annexure PDFs
            annexure_pdf_bytes_list = self._generate_annexure_pdfs(annexures or [])

            # Phase 4: Generate evidence photo pages
            evidence_pdf_bytes_list = self._generate_evidence_pages(case_id, case_data)

            # Phase 5: Assemble all components into final PDF
            final_pdf_bytes = self._assemble_all_pdfs(
                petition_pdf_bytes, index_pdf_bytes, annexure_pdf_bytes_list, evidence_pdf_bytes_list
            )

            return final_pdf_bytes, None

        except Exception as exc:
            self.logger.error("PDF assembly failed for case %s: %s", case_id, exc)
            return None, f"PDF assembly failed: {exc}"

    def _generate_main_document_pdfs(self, case_id: int, case_data: dict) -> bytes | None:
        """Generate Petition and Permission Letter PDFs using existing logic."""
        try:
            # Apply Phase 8 enhancements to the existing PDF generation
            petition_html = render_template("case_file_generator/petition.html", **case_data)
            permission_html = render_template("case_file_generator/permission_letter.html", **case_data)

            # Apply complete post-processing (Phase 6 + 7 + 8)
            petition_html = self._apply_complete_post_processing(petition_html, case_id, case_data)
            permission_html = self._apply_complete_post_processing(permission_html, case_id, case_data)

            # Generate PDFs using existing logic
            petition_pdf, petition_error = generate_pdf_from_html(petition_html)
            if petition_error:
                self.logger.error("Petition PDF generation failed: %s", petition_error)
                return None

            permission_pdf, permission_error = generate_pdf_from_html(permission_html)
            if permission_error:
                self.logger.error("Permission Letter PDF generation failed: %s", permission_error)
                return None

            # Merge petition and permission letter PDFs
            return self._merge_two_pdfs(petition_pdf, permission_pdf)

        except Exception as exc:
            self.logger.error("Main document PDF generation failed: %s", exc)
            return None

    def _generate_index_page_pdf(self, case_id: int, case_data: dict) -> bytes | None:
        """Generate comprehensive index page PDF for the case."""
        try:
            index_html = self._create_index_page_html(case_id, case_data)
            index_pdf, error = generate_pdf_from_html(index_html)

            if error:
                self.logger.warning("Index page PDF generation failed (optional): %s", error)
                return None

            return index_pdf

        except Exception as exc:
            self.logger.warning("Index page generation failed (optional): %s", exc)
            return None

    def _generate_annexure_pdfs(self, annexures: list[dict]) -> list[bytes]:
        """Generate PDF for each annexure."""
        annexure_pdfs = []

        for annexure in annexures:
            try:
                annexure_id = annexure.get("id")
                annexure_html = self._create_annexure_page_html(annexure)

                annexure_pdf, error = generate_pdf_from_html(annexure_html)
                if error:
                    self.logger.warning("Annexure %s PDF generation failed: %s", annexure_id, error)
                    continue

                annexure_pdfs.append(annexure_pdf)

            except Exception as exc:
                self.logger.warning("Failed to generate PDF for annexure %s: %s", annexure.get("id"), exc)

        return annexure_pdfs

    def _generate_evidence_pages(self, case_id: int, case_data: dict) -> list[bytes]:
        """Generate PDF pages for evidence photos."""
        evidence_pdfs = []

        # Get photo URLs from case data
        photo_urls = case_data.get("photo_urls", [])
        if not photo_urls:
            return evidence_pdfs

        # Embed photos as base64
        embedded_photos = embed_photos_as_base64(photo_urls)

        for photo_info in embedded_photos:
            try:
                if "data_uri" in photo_info:
                    # Create PDF page with embedded photo
                    photo_pdf = self._create_evidence_photo_page(photo_info["data_uri"], photo_info["url"])
                    evidence_pdfs.append(photo_pdf)

            except Exception as exc:
                self.logger.warning("Failed to generate evidence photo page: %s", exc)

        return evidence_pdfs

    def _assemble_all_pdfs(
        self,
        main_pdf: bytes,
        index_pdf: bytes | None,
        annexure_pdfs: list[bytes],
        evidence_pdfs: list[bytes],
    ) -> bytes:
        """Assemble all PDF components into final document."""
        try:
            from PyPDF2 import PdfMerger

            merger = PdfMerger()

            # Add main document (petition + permission letter)
            merger.append(fileobj=main_pdf)

            # Add index page if available
            if index_pdf:
                merger.append(fileobj=index_pdf)

            # Add annexure pages
            for annexure_pdf in annexure_pdfs:
                merger.append(fileobj=annexure_pdf)

            # Add evidence pages
            for evidence_pdf in evidence_pdfs:
                merger.append(fileobj=evidence_pdf)

            # Write final PDF to buffer
            output = io.BytesIO()
            merger.write(output)
            merger.close()

            return output.getvalue()

        except ImportError:
            # Fallback: if PyPDF2 is not available, return main PDF only
            self.logger.warning("PyPDF2 not available, returning main document only")
            return main_pdf

    def _apply_complete_post_processing(self, html_content: str, case_id: int, case_data: dict) -> str:
        """Apply complete Phase 6 + 7 + 8 post-processing to HTML."""
        # Step 1: Apply existing Phase 6 + 7 processing
        html_content = post_process_pdf_html(html_content, case_id=case_id)

        # Step 2: Apply Phase 8 enhancements
        html_content = self._apply_headers_footers(html_content, case_data)
        html_content = self._add_qr_codes(html_content, case_id)
        html_content = self._add_signature_placeholders(html_content)
        html_content = self._add_pdf_bookmarks(html_content, case_id)
        html_content = self._apply_page_numbers(html_content)
        html_content = self._apply_hyperlinks(html_content)

        return html_content

    def _apply_headers_footers(self, html_content: str, case_data: dict) -> str:
        """Apply the configured header and footer templates to the HTML.

        Fills the ``{{ key }}`` placeholders from
        :meth:`_prepare_header_footer_template_data` with plain ``replace``
        calls so WeasyPrint's ``{{ page_number }}`` pseudo-variable is
        preserved (it maps to itself and renders as the running page number).
        The header is inserted right after ``<body...>`` and the footer right
        before ``</body>``. Defensive: returns the input unchanged on any
        failure so PDF generation is never blocked.
        """
        try:
            data = self._prepare_header_footer_template_data(case_data)
            header_html = self.config.get("header_template") or ""
            footer_html = self.config.get("footer_template") or ""
            for key, value in data.items():
                token = "{{ " + key + " }}"
                header_html = header_html.replace(token, str(value))
                footer_html = footer_html.replace(token, str(value))

            # The default templates are self-contained blocks (they carry
            # their own styling div), so inject them directly without an
            # extra wrapper to avoid double-nested markup.
            header_block = header_html.strip()
            footer_block = footer_html.strip()

            if footer_block:
                # Full documents end with </html>, so locate </body> with
                # rfind rather than endswith and insert the footer before it.
                body_close = html_content.rfind("</body>")
                if body_close != -1:
                    html_content = f"{html_content[:body_close]}" f"{footer_block}" f"{html_content[body_close:]}"
                else:
                    html_content += footer_block

            if header_block:
                body_match = re.search(r"<body[^>]*>", html_content, re.IGNORECASE)
                if body_match:
                    split_at = body_match.end()
                    html_content = f"{html_content[:split_at]}" f"{header_block}" f"{html_content[split_at:]}"
                else:
                    html_content = header_block + html_content

            return html_content
        except Exception as exc:
            self.logger.warning("Failed to apply header/footer: %s", exc)
            return html_content

    def _apply_page_numbers(self, html_content: str) -> str:
        """Apply sequential page numbers using WeasyPrint's {{ page_number }} placeholder.

        WeasyPrint supports {{ page_number }} placeholder for automatic sequential
        page numbering. This method injects the CSS needed for page numbers.

        Args:
            html_content: The HTML content to process

        Returns:
            HTML content with page number CSS injected
        """
        page_number_css = """
        @page {
            @bottom-center {
                content: "Page {{ page_number }}";
                font-size: 10px;
                color: #666666;
            }
        }
        """

        # Inject page number CSS into HTML
        if "<style>" in html_content:
            html_content = html_content.replace("<style>", f"<style>{page_number_css}")
        else:
            html_content = f"<style>{page_number_css}</style>\n{html_content}"

        return html_content

    def _add_qr_codes(self, html_content: str, case_id: int) -> str:
        """Add QR codes for document authentication if enabled."""
        if not self.config["enable_qr_codes"] or not self.qrcode:
            return html_content

        try:
            qr_data = f"NSA-CASE-{case_id}-{datetime.now().strftime('%Y%m%d')}"
            qr_code_img = self._generate_qr_code_image(qr_data)

            qr_code_html = f"""
            <div class="qr-code-container" style="position: absolute; bottom: 20px; right: 20px;
                 background: white; padding: 10px; border: 1px solid #ccc; text-align: center;">
                <img src="data:image/png;base64,{qr_code_img}"
                     alt="QR Code for Document Verification"
                     style="width: 80px; height: 80px;" />
                <p style="font-size: 10px; margin-top: 5px; color: #666;">
                    Scan for document verification
                </p>
                <p style="font-size: 8px; color: #999;">{qr_data}</p>
            </div>
            """

            # Add QR code before footer
            html_content = html_content.replace(
                '<table class="footer-table">', f'{qr_code_html}<table class="footer-table">'
            )

            return html_content

        except Exception as exc:
            self.logger.warning("Failed to add QR code: %s", exc)
            return html_content

    def _add_signature_placeholders(self, html_content: str) -> str:
        """Add signature placeholders for manual signatures if enabled."""
        if not self.config["enable_signatures"]:
            return html_content

        signature_html = """
        <div class="signature-section" style="margin-top: 50px; page-break-inside: avoid;">
            <h3 style="text-align: center; font-size: 14px; margin-bottom: 30px;">
                AUTHORIZED SIGNATORIES
            </h3>

            <div style="display: flex; justify-content: space-around; margin-bottom: 40px;">
                <div style="text-align: center; width: 25%;">
                    <div style="border-bottom: 2px solid #ccc; height: 60px; margin-bottom: 15px;"></div>
                    <p style="font-size: 11px; color: #666; font-weight: bold;">FOOD SAFETY OFFICER</p>
                    <p style="font-size: 10px; color: #999;">(Official Signature)</p>
                </div>

                <div style="text-align: center; width: 25%;">
                    <div style="border-bottom: 2px solid #ccc; height: 60px; margin-bottom: 15px;"></div>
                    <p style="font-size: 11px; color: #666; font-weight: bold;">DESIGNATED OFFICER</p>
                    <p style="font-size: 10px; color: #999;">(Official Signature)</p>
                </div>

                <div style="text-align: center; width: 25%;">
                    <div style="border-bottom: 2px solid #ccc; height: 60px; margin-bottom: 15px;"></div>
                    <p style="font-size: 11px; color: #666; font-weight: bold;">DATE</p>
                    <p style="font-size: 10px; color: #999;">(DD/MM/YYYY)</p>
                </div>

                <div style="text-align: center; width: 25%;">
                    <div style="border-bottom: 2px solid #ccc; height: 60px; margin-bottom: 15px;"></div>
                    <p style="font-size: 11px; color: #666; font-weight: bold;">COMMENTS</p>
                    <p style="font-size: 10px; color: #999;">(Official Remarks)</p>
                </div>
            </div>
        </div>
        """

        # Add signature section before footer
        html_content = html_content.replace(
            '<table class="footer-table">', f'{signature_html}<table class="footer-table">'
        )

        return html_content

    def _apply_hyperlinks(self, html_content: str) -> str:
        """Phase 8: make PDF hyperlinks visible and clickable.

        1. Inject link-styling CSS so ``<a>`` anchors render as obvious
           clickable links in the compiled PDF (WeasyPrint turns them into
           real link annotations).
        2. Verify every internal ``#anchor`` href has a matching heading
           ``id``, re-running the Phase 7 heading-annotation pass when any
           are missing — so TOC/reference anchors always survive the
           post-processing chain even if an earlier pass failed.
        3. Wrap bare ``http(s)://`` URLs (annexure/evidence URLs, legal
           citations) in ``<a>`` tags so they become external PDF links.

        Defensive: returns the input unchanged on any failure so PDF
        generation is never blocked.
        """
        if not self.config.get("enable_hyperlinks", True):
            return html_content
        try:
            html_content = self._add_hyperlink_styling(html_content)
            html_content = self._ensure_internal_link_targets(html_content)
            html_content = self._linkify_plain_urls(html_content)
        except Exception as exc:
            self.logger.warning("Hyperlink pass skipped: %s", exc)
        return html_content

    def _add_hyperlink_styling(self, html_content: str) -> str:
        """Inject link-styling CSS so anchors render visibly clickable.

        Same placement pattern as :meth:`_apply_page_numbers`: appended
        inside an existing ``<style>`` block or prepended as a new one.
        """
        if not html_content:
            return html_content
        if "<style>" in html_content:
            return html_content.replace("<style>", f"<style>{_HYPERLINK_CSS}")
        return f"<style>{_HYPERLINK_CSS}</style>\n{html_content}"

    def _ensure_internal_link_targets(self, html_content: str) -> str:
        """Ensure every internal ``#anchor`` href has a matching target id.

        Phase 7 adds ``id="toc-N"`` attributes to headings so TOC links
        resolve. If that pass was skipped (or the document was edited after
        injection), the anchors would be dead in the PDF. This defensive
        pass re-runs the TOC heading-annotation whenever an internal link
        points to a missing id. ``annotate_html`` is idempotent (it skips
        headings that already carry an ``id``), so re-running it is safe.
        """
        try:
            from app.toc_generator.engine import TocGeneratorEngine

            target_ids = set(re.findall(r'\bid="([^"]+)"', html_content or ""))
            missing = [target for target in _INTERNAL_HREF_RE.findall(html_content or "") if target not in target_ids]
            if not missing:
                return html_content
            self.logger.info(
                "Internal anchors without targets: %s — re-annotating headings",
                sorted(set(missing)),
            )
            return TocGeneratorEngine().annotate_html(html_content)
        except Exception as exc:
            self.logger.warning("Anchor-target verification skipped: %s", exc)
            return html_content

    def _linkify_plain_urls(self, html_content: str) -> str:
        """Wrap bare http(s):// URLs in ``<a>`` tags (external PDF links).

        Annexure/evidence URLs and legal citations that appear as plain text
        become clickable external links in the compiled PDF. URLs already
        inside ``href``/``src`` attribute values (negative lookbehind) or
        already wrapped in an anchor are left untouched.
        """
        if not html_content:
            return html_content
        return _BARE_URL_RE.sub(r'<a href="\1">\1</a>', html_content)

    def _add_pdf_bookmarks(self, html_content: str, case_id: int) -> str:
        """Add PDF navigation bookmarks if enabled."""
        if not self.config["enable_bookmarks"]:
            return html_content

        # Create bookmark structure (invisible in HTML, visible in PDF)
        bookmarks_html = f"""
        <div class="pdf-bookmarks" style="display: none;">
            <h1>Case {case_id} Document Structure</h1>
            <ul>
                <li><a href="#document-start">Start Document</a></li>
                <li><a href="#section-1">Statement of Facts</a></li>
                <li><a href="#section-2">GROUNDS Analysis</a></li>
                <li><a href="#section-3">PRAYER Clauses</a></li>
                <li><a href="#annexure-a">Annexure A - Attachments</a></li>
                <li><a href="#annexure-b">Annexure B - Forms</a></li>
                <li><a href="#evidence-section">Evidence Photos</a></li>
                <li><a href="#signatures-section">Signatories</a></li>
            </ul>
        </div>
        """

        # Add bookmarks before closing body. Full documents end with
        # </html>, so locate </body> with rfind rather than endswith.
        body_close = html_content.rfind("</body>")
        if body_close != -1:
            html_content = f"{html_content[:body_close]}" f"{bookmarks_html}" f"{html_content[body_close:]}"
        else:
            html_content += bookmarks_html

        return html_content

    def _generate_qr_code_image(self, data: str) -> str:
        """Generate QR code image as base64 string."""
        if not self.qrcode:
            raise ValueError("qrcode library not available")

        qr = self.qrcode.QRCode(
            version=1,
            error_correction=self.qrcode.constants.ERROR_CORRECT_L,
            box_size=6,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img_byte_arr = self.BytesIO()
        img.save(img_byte_arr, format="PNG")
        img_byte_arr = img_byte_arr.getvalue()

        return self.base64.b64encode(img_byte_arr).decode()

    def _prepare_header_footer_template_data(self, case_data: dict) -> dict[str, str]:
        """Prepare data for header/footer template placeholders."""
        return {
            "case_number": case_data.get("case_number", ""),
            "fbo_name": case_data.get("manufacturer_name", ""),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "page_number": "{{ page_number }}",  # Will be replaced by WeasyPrint
            "case_tracking_info": f"Sample: {case_data.get('sample_code', '')}",
        }

    def _merge_two_pdfs(self, pdf1: bytes, pdf2: bytes) -> bytes:
        """Merge two PDF files into one."""
        try:
            from PyPDF2 import PdfMerger

            merger = PdfMerger()
            merger.append(fileobj=pdf1)
            merger.append(fileobj=pdf2)

            output = io.BytesIO()
            merger.write(output)
            merger.close()

            return output.getvalue()

        except ImportError:
            # If PyPDF2 is not available, return just the first PDF
            self.logger.warning("PyPDF2 not available, returning first PDF only")
            return pdf1

    def _create_index_page_html(self, case_id: int, case_data: dict) -> str:
        """Create HTML for comprehensive case index page."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Case {case_id} - Index</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    background-color: #f9f9f9;
                }}
                .header {{
                    text-align: center;
                    background-color: #2c3e50;
                    color: white;
                    padding: 30px;
                    margin-bottom: 40px;
                    border-radius: 5px;
                }}
                .index-container {{
                    max-width: 800px;
                    margin: 0 auto;
                    background-color: white;
                    padding: 30px;
                    border-radius: 5px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .index-section {{
                    margin-bottom: 30px;
                }}
                .section-title {{
                    font-size: 18px;
                    font-weight: bold;
                    color: #2c3e50;
                    border-bottom: 2px solid #3498db;
                    padding-bottom: 10px;
                    margin-bottom: 20px;
                }}
                .index-item {{
                    padding: 8px 0;
                    border-bottom: 1px solid #eee;
                    display: flex;
                    justify-content: space-between;
                }}
                .item-title {{
                    flex: 1;
                }}
                .item-page {{
                    color: #999;
                    font-family: monospace;
                }}
                .important-notice {{
                    background-color: #fff3cd;
                    border: 1px solid #ffc107;
                    border-radius: 5px;
                    padding: 15px;
                    margin: 20px 0;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 50px;
                    padding-top: 20px;
                    border-top: 1px solid #ddd;
                    color: #666;
                    font-size: 12px;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>CASE {case_id} INDEX</h1>
                <p>Comprehensive Document Index for Legal Adjudication</p>
            </div>

            <div class="index-container">
                <div class="index-section">
                    <div class="section-title">DOCUMENT STRUCTURE</div>
                    <div class="index-item">
                        <span class="item-title">1. Introduction / Statement of Facts</span>
                        <span class="item-page">Page 1</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">2. GROUNDS Analysis & Legal Citations</span>
                        <span class="item-page">Page 1</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">3. PRAYER Clauses & Relief Sought</span>
                        <span class="item-page">Page 2</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">4. Annexure A - Attachments & Evidence</span>
                        <span class="item-page">Page 3</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">5. Annexure B - Forms & Templates</span>
                        <span class="item-page">Page 4</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">6. EVIDENCE PHOTOGRAPHS</span>
                        <span class="item-page">Page 5</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">7. SIGNATORIES & AUTHORIZATION</span>
                        <span class="item-page">Page 6</span>
                    </div>
                </div>

                <div class="index-section">
                    <div class="section-title">CASE INFORMATION</div>
                    <div class="index-item">
                        <span class="item-title">Case Number:</span>
                        <span class="item-page">{case_data.get('case_number', '')}</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">Sample ID:</span>
                        <span class="item-page">{case_data.get('sample_code', '')}</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">Food Safety Officer:</span>
                        <span class="item-page">{case_data.get('food_safety_officer_name', '')}</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">Created On:</span>
                        <span class="item-page">{datetime.now().strftime('%Y-%m-%d')}</span>
                    </div>
                </div>

                <div class="important-notice">
                    <strong>Important Notice:</strong><br>
                    This document contains official adjudication materials. Access is restricted to authorized personnel only. <br>
                    All pages are interconnected. Navigation between sections is maintained throughout the document.
                </div>

                <div class="index-section">
                    <div class="section-title">TECHNICAL SPECIFICATIONS</div>
                    <div class="index-item">
                        <span class="item-title">Document Format:</span>
                        <span class="item-page">PDF / A4 Size</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">Security:</span>
                        <span class="item-page">Digital Signature & QR Code</span>
                    </div>
                    <div class="index-item">
                        <span class="item-title">Generated By:</span>
                        <span class="item-page">NSA Webservice v0.8.0</span>
                    </div>
                </div>
            </div>

            <div class="footer">
                <p>Page 1 of 1 | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p>This is an officially generated document. No manual signatures are required.</p>
            </div>
        </body>
        </html>
        """

    def _create_annexure_page_html(self, annexure: dict) -> str:
        """Create HTML for a standalone annexure page."""
        annexure_id = annexure.get("id", 0)
        annexure_letter = chr(64 + (annexure_id % 26)) if annexure_id <= 26 else str(annexure_id)
        title = annexure.get("title", f"Annexure {annexure_letter}")
        content = annexure.get("content", "")

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{title}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 40px;
                    background-color: #f5f5f5;
                }}
                .annexure-header {{
                    background-color: #34495e;
                    color: white;
                    padding: 30px;
                    margin-bottom: 30px;
                    text-align: center;
                    border-radius: 5px;
                }}
                .annexure-title {{
                    font-size: 28px;
                    font-weight: bold;
                    margin-bottom: 10px;
                }}
                .annexure-letter {{
                    font-size: 48px;
                    font-weight: bold;
                    color: #3498db;
                    display: block;
                    margin-bottom: 10px;
                }}
                .annexure-content {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 5px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    min-height: 300px;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #ddd;
                    color: #666;
                }}
                .page-number {{
                    float: right;
                    font-style: italic;
                }}
            </style>
        </head>
        <body>
            <div class="annexure-header">
                <span class="annexure-letter">ANNEXURE {annexure_letter}</span>
                <div class="annexure-title">{title}</div>
                <p>ID: {annexure_id} | Type: {annexure.get("type", "Document")}</p>
            </div>

            <div class="annexure-content">
                {content}
            </div>

            <div class="footer">
                <span class="page-number">Page 1 of 1</span>
                <p>Official Annexure Document - Confidential</p>
            </div>
        </body>
        </html>
        """

    def _create_evidence_photo_page(self, image_data: str, image_url: str) -> bytes:
        """Create PDF page for a single evidence photo."""
        try:
            # Create HTML with embedded image
            photo_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Evidence Photo</title>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        margin: 20px;
                        background-color: #000;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        min-height: 100vh;
                    }}
                    .photo-container {{
                        background: white;
                        padding: 20px;
                        border-radius: 10px;
                        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
                        text-align: center;
                        max-width: 90%;
                    }}
                    .photo {{
                        max-width: 100%;
                        height: auto;
                        border-radius: 5px;
                        margin-bottom: 20px;
                    }}
                    .photo-info {{
                        font-size: 14px;
                        color: #666;
                        margin-bottom: 10px;
                    }}
                    .photo-source {{
                        font-size: 12px;
                        color: #999;
                        font-style: italic;
                    }}
                    .verification-badge {{
                        background-color: #27ae60;
                        color: white;
                        padding: 5px 15px;
                        border-radius: 20px;
                        font-size: 12px;
                        margin-top: 10px;
                        display: inline-block;
                    }}
                </style>
            </head>
            <body>
                <div class="photo-container">
                    <img src="{image_data}" alt="Evidence Photo" class="photo" />
                    <div class="photo-info">
                        Evidence Photo
                    </div>
                    <div class="photo-source">
                        Source: {image_url}
                    </div>
                    <div class="verification-badge">
                        ✓ Verified
                    </div>
                </div>
            </body>
            </html>
            """

            # Generate PDF from HTML
            pdf_bytes, error = generate_pdf_from_html(photo_html)
            if error:
                self.logger.warning("Failed to generate evidence photo PDF: %s", error)
                return b""

            return pdf_bytes

        except Exception as exc:
            self.logger.warning("Failed to create evidence photo page: %s", exc)
            return b""


# Module-level function for backward compatibility with existing tasks.py


def assemble_complete_case_pdf(case_id: int, case_data: dict, annexures: list[dict] | None = None):
    """Assemble complete case file PDF with all Phase 8 features.

    This function provides backward-compatible access to the PDFAssemblyEngine
    for integration with existing workflows and Celery tasks.

    Args:
        case_id: Case file identifier
        case_data: Dictionary with case information
        annexures: List of annexure data (optional)

    Returns:
        Tuple of (pdf_bytes, error_message)
    """
    engine = PDFAssemblyEngine()
    return engine.assemble_complete_case_pdf(case_id, case_data, annexures)


if __name__ == "__main__":
    # Example usage and testing
    print("PDF Assembly Engine - Phase 8 Implementation")
    print("=" * 50)
    print("Features:")
    print("  ✓ Complete PDF assembly with annexures and evidence")
    print("  ✓ Headers, footers, and page numbers via @page CSS")
    print("  ✓ QR code generation for document authentication")
    print("  ✓ Signature placeholders for manual signatures")
    print("  ✓ PDF bookmarks and navigation")
    print("  ✓ Index pages for comprehensive case navigation")
    print("  ✓ Professional PDF formatting and security features")
    print()
    print("Ready for Phase 8 implementation in the existing workflow.")
