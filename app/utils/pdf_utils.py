"""PDF generation utilities with graceful WeasyPrint handling."""

import base64
import io
import logging
import os
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Environment variable to disable PDF generation (useful for local development without GTK)
PDF_GENERATION_ENABLED = os.environ.get("DISABLE_PDF_GENERATION", "false").lower() != "true"

# If R2_PUBLIC_BASE_URL or R2_ENDPOINT are set we assume public URLs;
# set PDF_USE_DIRECT_URLS=true to skip base64 embedding entirely.
_PDF_USE_DIRECT_URLS = os.environ.get("PDF_USE_DIRECT_URLS", "false").lower() == "true"

# Phase 7: WeasyPrint PDF CSS. Heading elements become PDF outline
# (bookmark) entries, giving generated documents a navigable outline that
# mirrors the injected Table of Contents. Annexure markers (detected by the
# TOC engine) get a distinct badge inside the injected TOC nav so annexures
# are easy to spot both in the PDF outline and the printed table of contents.
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


def _inject_bookmark_css(html_content: str) -> str:
    """Inject WeasyPrint bookmark + TOC CSS for h1-h6 headings.

    WeasyPrint turns elements carrying ``bookmark-level`` into PDF outline
    entries, so this gives generated documents a navigable outline aligned
    with the Phase 7 TOC. The block also styles the ``.toc-annexure-badge``
    chip that the TOC engine emits on annexure/appendix markers. The
    ``<style>`` block is placed inside ``<head>`` (falling back to right after
    ``<html>``, or prepended for bare HTML fragments) so WeasyPrint applies it
    during PDF layout. Skipped entirely when the document has no heading
    tags; never raises.
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


def import_weasyprint():
    """Import WeasyPrint with graceful error handling.
    Returns None if WeasyPrint cannot be imported (e.g., missing system dependencies).
    """
    if not PDF_GENERATION_ENABLED:
        return None

    try:
        from weasyprint import HTML

        return HTML
    except (ImportError, OSError) as e:
        logger.warning(f"WeasyPrint import failed: {e}")
        logger.warning("PDF generation will be disabled. This is expected on systems without GTK libraries.")
        return None


def generate_pdf_from_html(html_content):
    """Generate PDF from HTML string using WeasyPrint.
    Returns (pdf_bytes, error_message) tuple.
    """
    html_cls = import_weasyprint()
    if html_cls is None:
        return None, "PDF generation disabled or WeasyPrint not available"

    try:
        pdf_buffer = io.BytesIO()
        html_cls(string=html_content).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        return pdf_buffer.getvalue(), None
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        return None, f"PDF generation failed: {e}"


def renumber_html_lists(html_content: str) -> str:
    """Renumbering pass for ``<ol start="N">`` continuation lists (Phase 6).

    Recomputes ``start`` attributes on ordered lists that explicitly carry
    one, so paragraph numbering stays correct after insert/delete edits in
    the document editor. Lists without a ``start`` attribute are untouched.

    Delegates to the cross-reference engine; never raises (returns the input
    unchanged on failure).
    """
    try:
        from app.cross_reference.engine import CrossReferenceEngine

        return CrossReferenceEngine().renumber_html_lists(html_content)
    except Exception as exc:
        logger.warning("HTML renumbering pass skipped: %s", exc)
        return html_content


def post_process_pdf_html(html_content: str, case_id: int | None = None, adjudication_id: int | None = None) -> str:
    """Phase 6 + Phase 7 post-processing pass over rendered HTML before PDF compilation.

    Phase 6 (cross-references):
    - Applies the list-renumbering pass.
    - Fills an ``<ol data-cross-reference="enclosures">`` placeholder with
      the auto-generated annexure enclosures list for the case.

    Phase 7 (dynamic TOC):
    - Injects a Table of Contents into ``<div data-toc></div>`` placeholders.
    - Adds ``id`` attributes to headings for anchor navigation.
    - Injects WeasyPrint ``bookmark-level`` CSS so h1-h6 headings become
      a navigable PDF outline.

    Defensive: returns the input unchanged on any failure so PDF generation
    is never blocked.
    """
    try:
        from app.cross_reference.engine import CrossReferenceEngine

        html_content = CrossReferenceEngine().annotate_html(
            html_content, case_id=case_id, adjudication_id=adjudication_id
        )
    except Exception as exc:
        logger.warning("Cross-reference post-processing skipped: %s", exc)

    # Phase 7: Dynamic TOC injection
    try:
        from app.toc_generator.engine import TocGeneratorEngine

        html_content = TocGeneratorEngine().annotate_html(html_content)
    except Exception as exc:
        logger.warning("TOC post-processing skipped: %s", exc)

    # Phase 7: WeasyPrint PDF bookmarks (navigable outline)
    try:
        html_content = _inject_bookmark_css(html_content)
    except Exception as exc:
        logger.warning("Bookmark CSS injection skipped: %s", exc)

    return html_content


def embed_photos_as_base64(photo_urls):
    """Fetch photo images from storage URLs and return base64 data URIs
    that WeasyPrint can embed in PDFs, even when URLs are not publicly
    accessible (e.g. signed R2 URLs behind a firewall).

    If ``PDF_USE_DIRECT_URLS`` is set, the original URL is returned
    unchanged (assumes public bucket / custom domain).

    Failed fetches are logged and skipped — never block the whole PDF.

    Returns:
        list[dict]: each entry is ``{"url": str, "data_uri": str}`` on
        success, or ``{"url": str, "error": str}`` on failure.

    """
    results = []

    for path in photo_urls:
        if not path:
            results.append({"url": path, "error": "empty path"})
            continue

        if _PDF_USE_DIRECT_URLS:
            results.append({"url": path, "data_uri": path})
            continue

        try:
            if path.startswith(("http://", "https://")):
                # Remote URL — fetch over HTTP
                resp = requests.get(path, timeout=10)
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                raw_bytes = resp.content
            else:
                # Local filesystem path — read directly
                if not os.path.exists(path):
                    raise FileNotFoundError(f"Local file not found: {path}")
                with open(path, "rb") as f:
                    raw_bytes = f.read()
                # Guess content type from extension
                ext = Path(path).suffix.lower()
                content_type = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                }.get(ext, "image/jpeg")

            b64 = base64.b64encode(raw_bytes).decode("ascii")
            results.append(
                {
                    "url": path,
                    "data_uri": f"data:{content_type};base64,{b64}",
                }
            )
        except Exception as exc:
            logger.warning("Failed to embed photo for PDF: %s — %s", path, exc)
            results.append({"url": path, "error": str(exc)})

    return results
