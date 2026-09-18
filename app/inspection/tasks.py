"""OCR tasks for the Inspection blueprint.

Provides a plain-function entry point that performs zonal OCR on scanned
documents and photos attached to inspection records. Dispatched via QStash
with synchronous inline fallback.
"""

from __future__ import annotations


def run_ocr_extraction(file_path: str, zones: dict | None = None) -> dict:
    """Perform zonal OCR on a scanned PDF or image file.

    Converts PDF pages to images (via ``pdf2image``) or processes image
    files directly, then uses ``pytesseract`` to extract text.  If
    ``zones`` is provided, OCR is restricted to named rectangular regions
    ``{name: (x, y, w, h)}``; otherwise a full-page extraction is
    performed on each page/frame.

    Parameters
    ----------
    file_path : str
        Absolute or relative path to the PDF or image file.
    zones : dict, optional
        Mapping of field names to bounding boxes in pixels:
        ``{"field_name": (left, top, width, height)}``.

    Returns
    -------
    dict
        Keys are page/zone identifiers, values are the extracted text.

    Raises
    ------
    RuntimeError
        For transient errors (file I/O, resource contention) — the
        QStash dispatcher redelivers on failure.
    ValueError
        For non-transient errors (unsupported format, missing file).

    """
    # --- lazy imports so the module can be loaded without heavy deps ---
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)

    # Validate file exists before doing any work
    if not Path(file_path).is_file():
        raise ValueError(f"File not found: {file_path}")

    # Determine file type by extension
    ext = Path(file_path).suffix.lower()

    is_pdf = ext == ".pdf"
    supported_images = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp"}

    if not is_pdf and ext not in supported_images:
        # Non-transient — don't retry
        raise ValueError(f"Unsupported file format '{ext}'. Supported: PDF, {', '.join(sorted(supported_images))}")

    pages = []  # list of PIL Image objects

    try:
        if is_pdf:
            from pdf2image import convert_from_path

            pages = convert_from_path(file_path, dpi=300)
            if not pages:
                raise ValueError(f"PDF yielded zero pages: {file_path}")
        else:
            from PIL import Image

            pages = [Image.open(file_path)]
    except OSError as exc:
        # File I/O errors are typically transient (e.g. NFS glitch)
        logger.warning("Transient I/O error opening %s: %s", file_path, exc)
        raise
    except ValueError:
        # Non-transient — let it propagate
        raise
    except Exception as exc:
        # Transient: network mount, lock contention, etc.
        logger.warning("Transient error opening %s: %s", file_path, exc)
        raise

    import pytesseract

    results: dict = {}
    ocr_error_count = 0

    for page_num, image in enumerate(pages, start=1):
        try:
            if zones:
                for zone_name, (x, y, w, h) in zones.items():
                    key = f"p{page_num}_{zone_name}"
                    cropped = image.crop((x, y, x + w, y + h))
                    text = pytesseract.image_to_string(cropped, lang="eng")
                    results[key] = text.strip()
            else:
                text = pytesseract.image_to_string(image, lang="eng")
                results[f"p{page_num}"] = text.strip()
        except Exception as exc:
            ocr_error_count += 1
            err_str = str(exc).lower()
            # Retry only for recognised transient conditions
            if any(term in err_str for term in ("timeout", "temporary", "eagain")):
                logger.warning("Transient OCR error on page %d: %s", page_num, exc)
                raise
            # Otherwise record the failure and continue with remaining pages
            logger.error("Non-transient OCR error on page %d: %s", page_num, exc)
            results[f"p{page_num}_error"] = str(exc)

    results["_pages_processed"] = len(pages)
    results["_ocr_errors"] = ocr_error_count

    return results


__all__ = ["run_ocr_extraction"]
