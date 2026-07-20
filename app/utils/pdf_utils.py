"""
PDF generation utilities with graceful WeasyPrint handling.
"""

import os

# Environment variable to disable PDF generation (useful for local development without GTK)
PDF_GENERATION_ENABLED = os.environ.get('DISABLE_PDF_GENERATION', 'false').lower() != 'true'


def import_weasyprint():
    """
    Import WeasyPrint with graceful error handling.
    Returns None if WeasyPrint cannot be imported (e.g., missing system dependencies).
    """
    if not PDF_GENERATION_ENABLED:
        return None
    
    try:
        from weasyprint import HTML
        return HTML
    except (ImportError, OSError) as e:
        # Log the error but don't crash the application
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"WeasyPrint import failed: {e}")
        logger.warning("PDF generation will be disabled. This is expected on systems without GTK libraries.")
        return None


def generate_pdf_from_html(html_content):
    """
    Generate PDF from HTML string using WeasyPrint.
    Returns (pdf_bytes, error_message) tuple.
    """
    HTML = import_weasyprint()
    if HTML is None:
        return None, "PDF generation disabled or WeasyPrint not available"
    
    try:
        pdf_buffer = bytes()
        HTML(string=html_content).write_pdf(pdf_buffer)
        return pdf_buffer, None
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"PDF generation failed: {e}")
        return None, f"PDF generation failed: {e}"