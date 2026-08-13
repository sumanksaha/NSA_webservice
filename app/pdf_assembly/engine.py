"""PDF Assembly Engine — consolidated PDF generation pipeline.

This module is the single source of truth for all PDF operations that were
previously scattered across ``app/utils/pdf_utils.py`` (WeasyPrint guard,
bookmark CSS, post-processing orchestration, photo embedding) and the
Phase 8 ``PDFAssemblyEngine`` that lived in ``app/pdf_assembly/__init__.py``.

Public surface (canonical, preferred by all callers):

    :class:`PDFAssemblyEngine` — the assembled engine with four entry points:

        - ``generate_from_html(html)  -> (bytes|None, error|None)``
        - ``post_process(html, **kw)   -> str``  (Phase 6 + 7)
        - ``embed_photos(urls)         -> list[dict]``
        - ``assemble(html, **kw)       -> (bytes|None, error|None)``

Backward-compatibility: ``app/utils/pdf_utils.py`` now re-exports thin
shims that delegate to a module-level ``PDFAssemblyEngine`` instance, so
existing callers (adjudication routes, document viewer renderer, etc.) need
no import-site changes.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import requests

from app.cross_reference.engine import CrossReferenceEngine
from app.toc_generator.engine import TocGeneratorEngine

logger = logging.getLogger(__name__)

PDF_GENERATION_ENABLED = os.environ.get("DISABLE_PDF_GENERATION", "false").lower() != "true"

_PDF_USE_DIRECT_URLS = os.environ.get("PDF_USE_DIRECT_URLS", "false").lower() == "true"

_BOOKMARK_CSS = """\
h1 { bookmark-level: 1; }
h2 { bookmark-level: 2; }
h3 { bookmark-level: 3; }
h4 { bookmark-level: 4; }
h5 { bookmark-level: 5; }
h6 { bookmark-level: 6; }
.toc-annexure a { color: #1e40af; }
.toc-annexure-badge {
  display: inline-block;
  font-size: 0.65em;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #1e40af;
  background: #dbeafe;
  border-radius: 3px;
  padding: 1px 5px;
  vertical-align: 0.08em;
  margin-right: 4px;
}
"""

_HEADING_TAG_RE = re.compile(r"<(h[1-6])\b", re.IGNORECASE)

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

_INTERNAL_HREF_RE = re.compile(r'href="#([^"]+)"')

_BARE_URL_RE = re.compile(
    r"(?<!['\"])(https?://[^\s<>\"')]+)(?<![.\"])",
    re.IGNORECASE,
)


def import_weasyprint():
    """Import WeasyPrint with graceful error handling.

    Returns the ``HTML`` class from WeasyPrint, or ``None`` when WeasyPrint
    cannot be imported (missing system libraries, or when
    ``DISABLE_PDF_GENERATION=1`` is set).
    """
    if not PDF_GENERATION_ENABLED:
        return None
    try:
        from weasyprint import HTML

        return HTML
    except (ImportError, OSError) as exc:
        logger.warning("WeasyPrint import failed: %s", exc)
        logger.warning("PDF generation will be disabled.")
        return None


class PDFAssemblyEngine:
    """Consolidated PDF assembly engine (Phase 6-8 + standalone utilities).

    Four canonical entry points:

    - :meth:`generate_from_html` — HTML → PDF bytes (guarded WeasyPrint).
    - :meth:`post_process` — Phase 6 cross-reference + Phase 7 TOC/bookmark pipeline.
    - :meth:`embed_photos` — Remote URL / local path → base64 data URIs.
    - :meth:`assemble` — ``post_process`` → ``generate_from_html`` end-to-end.

    All methods are defensive: failures are logged and swallowed so PDF
    generation is never blocked by a single sub-system failure.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._setup_environment_config()
        self._import_qrcode_if_available()

    # ------------------------------------------------------------------ #
    # Canonical interface (D3 deepening target)
    # ------------------------------------------------------------------ #

    def generate_from_html(self, html_content: str) -> tuple[bytes | None, str | None]:
        """Generate PDF from HTML string using WeasyPrint.

        Returns ``(pdf_bytes, error_message)``.  When WeasyPrint is
        unavailable or fails, ``pdf_bytes`` is ``None`` and ``error_message``
        explains why.
        """
        html_cls = import_weasyprint()
        if html_cls is None:
            return None, "PDF generation disabled or WeasyPrint not available"
        try:
            pdf_buffer = io.BytesIO()
            html_cls(string=html_content).write_pdf(pdf_buffer)
            pdf_buffer.seek(0)
            return pdf_buffer.getvalue(), None
        except Exception as exc:
            self.logger.error("PDF generation failed: %s", exc)
            return None, f"PDF generation failed: {exc}"

    def post_process(
        self,
        html_content: str,
        case_id: int | None = None,
        adjudication_id: int | None = None,
    ) -> str:
        """Phase 6 + Phase 7 post-processing pass over rendered HTML.

        Phase 6 (cross-references): list renumbering + annexure enclosures.
        Phase 7 (dynamic TOC): TOC injection, heading ids, bookmark CSS.
        """
        try:
            html_content = CrossReferenceEngine().annotate_html(
                html_content, case_id=case_id, adjudication_id=adjudication_id
            )
        except Exception as exc:
            self.logger.warning("Cross-reference post-processing skipped: %s", exc)

        try:
            html_content = TocGeneratorEngine().annotate_html(html_content)
        except Exception as exc:
            self.logger.warning("TOC post-processing skipped: %s", exc)

        try:
            html_content = self._inject_bookmark_css(html_content)
        except Exception as exc:
            self.logger.warning("Bookmark CSS injection skipped: %s", exc)

        return html_content

    def embed_photos(self, photo_urls: list[str]) -> list[dict]:
        """Fetch photo images from URLs/paths and return base64 data URIs.

        Supports remote ``http(s)://`` URLs (HTTP GET) and local filesystem
        paths.  Failed fetches return ``{"url": ..., "error": ...}`` entries.
        """
        results: list[dict] = []
        for path in photo_urls:
            if not path:
                results.append({"url": path, "error": "empty path"})
                continue
            if _PDF_USE_DIRECT_URLS:
                results.append({"url": path, "data_uri": path})
                continue
            try:
                if path.startswith(("http://", "https://")):
                    resp = requests.get(path, timeout=10)
                    resp.raise_for_status()
                    content_type = resp.headers.get("Content-Type", "image/jpeg")
                    raw_bytes = resp.content
                else:
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"Local file not found: {path}")
                    with open(path, "rb") as f:
                        raw_bytes = f.read()
                    ext = Path(path).suffix.lower()
                    content_type = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".webp": "image/webp",
                    }.get(ext, "image/jpeg")

                b64 = base64.b64encode(raw_bytes).decode("ascii")
                results.append({
                    "url": path,
                    "data_uri": f"data:{content_type};base64,{b64}",
                })
            except Exception as exc:
                logger.warning("Failed to embed photo for PDF: %s — %s", path, exc)
                results.append({"url": path, "error": str(exc)})
        return results

    def assemble(
        self,
        html_content: str,
        case_id: int | None = None,
        adjudication_id: int | None = None,
        photo_urls: list[str] | None = None,
    ) -> tuple[bytes | None, str | None]:
        """End-to-end assembly: post-process HTML then generate PDF.

        ``photo_urls`` is accepted for API completeness; photo embedding is
        typically performed at the template-rendering layer (the data URIs
        are injected into the HTML before it reaches this method).
        """
        processed = self.post_process(html_content, case_id=case_id, adjudication_id=adjudication_id)
        return self.generate_from_html(processed)

    def renumber_html_lists(self, html_content: str) -> str:
        """Renumbering pass for ``<ol start="N">`` continuation lists (Phase 6).

        Delegates to :class:`CrossReferenceEngine`. Never raises.
        """
        try:
            return CrossReferenceEngine().renumber_html_lists(html_content)
        except Exception as exc:
            self.logger.warning("HTML renumbering pass skipped: %s", exc)
            return html_content

    def _inject_bookmark_css(self, html_content: str) -> str:
        """Inject WeasyPrint bookmark CSS for h1-h6 headings.

        WeasyPrint turns elements with ``bookmark-level`` into PDF outline
        entries. The block also styles the ``.toc-annexure-badge`` chip that
        the TOC engine emits on annexure markers.
        """
        if not html_content or not _HEADING_TAG_RE.search(html_content):
            return html_content
        style_block = f"<style>\n{_BOOKMARK_CSS}</style>"
        head_match = re.search(r"<head[^>]*>", html_content, re.IGNORECASE)
        if head_match:
            split_at = head_match.end()
            return f"{html_content[:split_at]}{style_block}{html_content[split_at:]}"
        html_match = re.search(r"<html[^>]*>", html_content, re.IGNORECASE)
        if html_match:
            split_at = html_match.end()
            return f"{html_content[:split_at]}{style_block}{html_content[split_at:]}"
        return style_block + html_content

    # ------------------------------------------------------------------ #
    # Environment / dependency setup
    # ------------------------------------------------------------------ #

    def _setup_environment_config(self) -> None:
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
        return (
            '<div class="pdf-footer" style="width: 100%; border-top: 1px '
            "solid #999; padding-top: 4px; margin-top: 14px; font-size: 9px; "
            'color: #777; text-align: center;">'
            "{{ case_tracking_info }} &mdash; Page {{ page_number }}"
            "</div>"
        )

    def _import_qrcode_if_available(self) -> None:
        try:
            import base64 as _b64
            from io import BytesIO

            import qrcode

            self.qrcode = qrcode
            self._BytesIO = BytesIO
            self._b64 = _b64
        except ImportError:
            self.logger.warning("qrcode library not available, QR code generation disabled")
            self.qrcode = None

    # ------------------------------------------------------------------ #
    # Phase 8 — complete case-file assembly
    # ------------------------------------------------------------------ #

    def assemble_complete_case_pdf(
        self,
        case_id: int,
        case_data: dict,
        annexures: list[dict] | None = None,
    ) -> tuple[bytes | None, str | None]:
        """Assemble a complete case-file PDF with all Phase 8 features.

        Generates petition + permission letter (existing), index page,
        annexure pages, and evidence photo pages, then merges them.
        """
        try:
            petition_pdf_bytes = self._generate_main_document_pdfs(case_id, case_data)
            if petition_pdf_bytes is None:
                return None, "Failed to generate main document PDFs"

            index_pdf_bytes = self._generate_index_page_pdf(case_id, case_data)
            annexure_pdf_bytes_list = self._generate_annexure_pdfs(annexures or [])
            evidence_pdf_bytes_list = self._generate_evidence_pages(case_id, case_data)

            final_pdf_bytes = self._assemble_all_pdfs(
                petition_pdf_bytes,
                index_pdf_bytes,
                annexure_pdf_bytes_list,
                evidence_pdf_bytes_list,
            )
            return final_pdf_bytes, None
        except Exception as exc:
            self.logger.error("PDF assembly failed for case %s: %s", case_id, exc)
            return None, f"PDF assembly failed: {exc}"

    def _generate_main_document_pdfs(self, case_id: int, case_data: dict) -> bytes | None:
        """Generate Petition and Permission Letter PDFs using existing logic."""
        try:
            from flask import render_template

            petition_html = render_template("case_file_generator/petition.html", **case_data)
            permission_html = render_template("case_file_generator/permission_letter.html", **case_data)

            petition_html = self._apply_complete_post_processing(petition_html, case_id, case_data)
            permission_html = self._apply_complete_post_processing(permission_html, case_id, case_data)

            petition_pdf, petition_error = self.generate_from_html(petition_html)
            if petition_error:
                self.logger.error("Petition PDF generation failed: %s", petition_error)
                return None

            permission_pdf, permission_error = self.generate_from_html(permission_html)
            if permission_error:
                self.logger.error("Permission Letter PDF generation failed: %s", permission_error)
                return None

            return self._merge_two_pdfs(petition_pdf, permission_pdf)
        except Exception as exc:
            self.logger.error("Main document PDF generation failed: %s", exc)
            return None

    def _generate_index_page_pdf(self, case_id: int, case_data: dict) -> bytes | None:
        """Generate comprehensive index page PDF for the case."""
        try:
            index_html = self._create_index_page_html(case_id, case_data)
            index_pdf, error = self.generate_from_html(index_html)
            if error:
                self.logger.warning("Index page PDF generation failed (optional): %s", error)
                return None
            return index_pdf
        except Exception as exc:
            self.logger.warning("Index page generation failed (optional): %s", exc)
            return None

    def _generate_annexure_pdfs(self, annexures: list[dict]) -> list[bytes]:
        """Generate PDF for each annexure."""
        annexure_pdfs: list[bytes] = []
        for annexure in annexures:
            try:
                annexure_id = annexure.get("id")
                annexure_html = self._create_annexure_page_html(annexure)
                annexure_pdf, error = self.generate_from_html(annexure_html)
                if error:
                    self.logger.warning("Annexure %s PDF generation failed: %s", annexure_id, error)
                    continue
                annexure_pdfs.append(annexure_pdf)
            except Exception as exc:
                self.logger.warning("Failed to generate PDF for annexure %s: %s", annexure.get("id"), exc)
        return annexure_pdfs

    def _generate_evidence_pages(self, case_id: int, case_data: dict) -> list[bytes]:
        """Generate PDF pages for evidence photos."""
        evidence_pdfs: list[bytes] = []
        photo_urls = case_data.get("photo_urls", [])
        if not photo_urls:
            return evidence_pdfs

        embedded_photos = self.embed_photos(photo_urls)

        for photo_info in embedded_photos:
            try:
                if "data_uri" in photo_info:
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
            merger.append(fileobj=main_pdf)
            if index_pdf:
                merger.append(fileobj=index_pdf)
            for annexure_pdf in annexure_pdfs:
                merger.append(fileobj=annexure_pdf)
            for evidence_pdf in evidence_pdfs:
                merger.append(fileobj=evidence_pdf)

            output = io.BytesIO()
            merger.write(output)
            merger.close()
            return output.getvalue()
        except ImportError:
            self.logger.warning("PyPDF2 not available, returning main document only")
            return main_pdf

    def _apply_complete_post_processing(self, html_content: str, case_id: int, case_data: dict) -> str:
        """Apply complete Phase 6 + 7 + 8 post-processing to HTML."""
        html_content = self.post_process(html_content, case_id=case_id)
        html_content = self._apply_headers_footers(html_content, case_data)
        html_content = self._add_qr_codes(html_content, case_id)
        html_content = self._add_signature_placeholders(html_content)
        html_content = self._add_pdf_bookmarks(html_content, case_id)
        html_content = self._apply_page_numbers(html_content)
        html_content = self._apply_hyperlinks(html_content)
        return html_content

    def _apply_headers_footers(self, html_content: str, case_data: dict) -> str:
        """Apply the configured header and footer templates to the HTML."""
        try:
            data = self._prepare_header_footer_template_data(case_data)
            header_html = self.config.get("header_template") or ""
            footer_html = self.config.get("footer_template") or ""
            for key, value in data.items():
                token = "{{ " + key + " }}"
                header_html = header_html.replace(token, str(value))
                footer_html = footer_html.replace(token, str(value))

            header_block = header_html.strip()
            footer_block = footer_html.strip()

            if footer_block:
                body_close = html_content.rfind("</body>")
                if body_close != -1:
                    html_content = f"{html_content[:body_close]}{footer_block}{html_content[body_close:]}"
                else:
                    html_content += footer_block

            if header_block:
                body_match = re.search(r"<body[^>]*>", html_content, re.IGNORECASE)
                if body_match:
                    split_at = body_match.end()
                    html_content = f"{html_content[:split_at]}{header_block}{html_content[split_at:]}"
                else:
                    html_content = header_block + html_content

            return html_content
        except Exception as exc:
            self.logger.warning("Failed to apply header/footer: %s", exc)
            return html_content

    def _apply_page_numbers(self, html_content: str) -> str:
        """Apply sequential page numbers using WeasyPrint's ``{{ page_number }}``."""
        page_number_css = """
        @page {
            @bottom-center {
                content: "Page {{ page_number }}";
                font-size: 10px;
                color: #666666;
            }
        }
        """
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

        html_content = html_content.replace(
            '<table class="footer-table">', f'{signature_html}<table class="footer-table">'
        )
        return html_content

    def _apply_hyperlinks(self, html_content: str) -> str:
        """Phase 8: make PDF hyperlinks visible and clickable."""
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
        """Inject link-styling CSS so anchors render visibly clickable."""
        if not html_content:
            return html_content
        if "<style>" in html_content:
            return html_content.replace("<style>", f"<style>{_HYPERLINK_CSS}")
        return f"<style>{_HYPERLINK_CSS}</style>\n{html_content}"

    def _ensure_internal_link_targets(self, html_content: str) -> str:
        """Ensure every internal ``#anchor`` href has a matching target id."""
        try:
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
        """Wrap bare http(s) URLs in ``<a>`` tags (external PDF links)."""
        if not html_content:
            return html_content
        return _BARE_URL_RE.sub(r'<a href="\1">\1</a>', html_content)

    def _add_pdf_bookmarks(self, html_content: str, case_id: int) -> str:
        """Add PDF navigation bookmarks if enabled."""
        if not self.config["enable_bookmarks"]:
            return html_content

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

        body_close = html_content.rfind("</body>")
        if body_close != -1:
            html_content = f"{html_content[:body_close]}{bookmarks_html}{html_content[body_close:]}"
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
        img_byte_arr = self._BytesIO()
        img.save(img_byte_arr, format="PNG")
        img_byte_arr = img_byte_arr.getvalue()
        return self._b64.b64encode(img_byte_arr).decode()

    def _prepare_header_footer_template_data(self, case_data: dict) -> dict[str, str]:
        return {
            "case_number": case_data.get("case_number", ""),
            "fbo_name": case_data.get("manufacturer_name", ""),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "page_number": "{{ page_number }}",
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
            self.logger.warning("PyPDF2 not available, returning first PDF only")
            return pdf1

    def _create_index_page_html(self, case_id: int, case_data: dict) -> str:
        """Create HTML for comprehensive case index page.

        Delegates to the ``pdf_assembly/index_page.html`` Jinja2 template,
        eliminating the 155-line f-string that previously lived inline.
        """
        from flask import render_template
        from datetime import timezone

        now = datetime.now(timezone.utc)
        return render_template(
            "pdf_assembly/index_page.html",
            case_id=case_id,
            case_data=case_data,
            now_date=now.strftime("%Y-%m-%d"),
            now_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _create_annexure_page_html(self, annexure: dict) -> str:
        """Create HTML for a standalone annexure page.

        Delegates to the ``pdf_assembly/annexure_page.html`` Jinja2 template.
        """
        from flask import render_template

        annexure_id = annexure.get("id", 0)
        annexure_letter = chr(64 + (annexure_id % 26)) if annexure_id <= 26 else str(annexure_id)
        return render_template(
            "pdf_assembly/annexure_page.html",
            annexure=annexure,
            annexure_id=annexure_id,
            annexure_letter=annexure_letter,
            title=annexure.get("title", f"Annexure {annexure_letter}"),
            annexure_type=annexure.get("type", "Document"),
            content=annexure.get("content", ""),
        )

    def _create_evidence_photo_page(self, image_data: str, image_url: str) -> bytes:
        """Create PDF page for a single evidence photo.

        Delegates to the ``pdf_assembly/evidence_photo_page.html`` Jinja2
        template, eliminating the 50-line inline f-string.
        """
        from flask import render_template

        try:
            photo_html = render_template(
                "pdf_assembly/evidence_photo_page.html",
                image_data=image_data,
                image_url=image_url,
            )
            pdf_bytes, error = self.generate_from_html(photo_html)
            if error:
                self.logger.warning("Failed to generate evidence photo PDF: %s", error)
                return b""
            return pdf_bytes
        except Exception as exc:
            self.logger.warning("Failed to create evidence photo page: %s", exc)
            return b""

    # ------------------------------------------------------------------ #
    # Backward-compatible module-level function
    # ------------------------------------------------------------------ #


def assemble_complete_case_pdf(
    case_id: int,
    case_data: dict,
    annexures: list[dict] | None = None,
) -> tuple[bytes | None, str | None]:
    """Module-level wrapper preserved for backward compatibility."""
    engine = PDFAssemblyEngine()
    return engine.assemble_complete_case_pdf(case_id, case_data, annexures)


__all__ = ["PDFAssemblyEngine", "assemble_complete_case_pdf", "import_weasyprint"]
