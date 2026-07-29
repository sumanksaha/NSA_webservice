"""Image Preprocessing Pipeline for Legal Document OCR.

Applies a configurable sequence of OpenCV operations to improve OCR
accuracy on scanned or photographed legal documents.

Pipeline steps (in order):
1. Convert PDF page to high-DPI image
2. Grayscale conversion
3. Denoising (Non-local Means Denoising)
4. Deskew (correct rotation)
5. Adaptive thresholding / Binarization
6. Contrast enhancement (CLAHE)
7. Orientation detection & correction
8. Resolution enhancement (optional upscaling)
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# OpenCV — optional dependency; every method handles ImportError inline
# Default DPI for rendering PDF pages to images
_DEFAULT_DPI = 300

# Maximum dimension in pixels for OCR processing
_MAX_DIMENSION = 4000


class PreprocessingStep(StrEnum):
    """Identifiers for each preprocessing step — tracked in OCRResult."""

    GRAYSCALE = "grayscale"
    DENOISE = "denoise"
    DESKEW = "deskew"
    THRESHOLD = "adaptive_threshold"
    CONTRAST = "contrast_enhancement"
    ORIENTATION = "orientation_correction"
    RESOLUTION = "resolution_enhancement"


class ImagePreprocessor:
    """Applies a configurable sequence of image preprocessing operations.

    Usage::

        preprocessor = ImagePreprocessor()
        image = preprocessor.pdf_page_to_image(pdf_path, page_num=1)
        processed, steps = preprocessor.process(image)
        # processed is the cleaned image ready for OCR
    """

    def __init__(self, dpi: int = _DEFAULT_DPI) -> None:
        self._dpi = dpi

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def pdf_page_to_image(self, pdf_path: str | Path, page_number: int) -> np.ndarray | None:
        """Render a PDF page to a NumPy array (RGB format)."""
        try:
            import fitz
        except ImportError:
            logger.error("PyMuPDF (fitz) not available for PDF rendering")
            return None

        try:
            doc = fitz.open(str(pdf_path))
            if page_number < 1 or page_number > len(doc):
                doc.close()
                return None
            page = doc[page_number - 1]
            zoom = self._dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            doc.close()
            return img  # RGB format (not BGR)
        except Exception as exc:
            logger.error("Failed to render PDF page %d to image: %s", page_number, exc)
            return None

    # ------------------------------------------------------------------
    # Main processing pipeline
    # ------------------------------------------------------------------

    def process(
        self,
        image: np.ndarray,
        steps: list[PreprocessingStep] | None = None,
    ) -> tuple[np.ndarray, list[str]]:
        """Run the preprocessing pipeline on an image.

        Args:
            image: Input image as a NumPy array (RGB or grayscale).
            steps: List of preprocessing steps to apply. If None, all steps
                are applied in the default order.

        Returns:
            Tuple of ``(processed_image, applied_step_names)``.

        """
        try:
            import cv2
        except ImportError:
            logger.warning("OpenCV (cv2) not available — preprocessing disabled")
            return image, []

        if steps is None:
            steps = list(PreprocessingStep)

        applied: list[str] = []
        img = image.copy()

        for step in steps:
            try:
                if step == PreprocessingStep.GRAYSCALE:
                    if len(img.shape) == 3:
                        img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
                    applied.append(step.value)

                elif step == PreprocessingStep.DENOISE:
                    if len(img.shape) == 2:
                        img = cv2.fastNlMeansDenoising(img, h=10, templateWindowSize=7, searchWindowSize=21)
                    else:
                        img = cv2.fastNlMeansDenoisingColored(
                            img,
                            h=10,
                            hColor=10,
                            templateWindowSize=7,
                            searchWindowSize=21,
                        )
                    applied.append(step.value)

                elif step == PreprocessingStep.DESKEW:
                    img = self._deskew(img, cv2)
                    applied.append(step.value)

                elif step == PreprocessingStep.THRESHOLD:
                    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if len(img.shape) == 3 else img
                    img = cv2.adaptiveThreshold(
                        gray,
                        255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY,
                        blockSize=31,
                        C=2,
                    )
                    applied.append(step.value)

                elif step == PreprocessingStep.CONTRAST:
                    if len(img.shape) == 3:
                        lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
                        lightness, a, b = cv2.split(lab)
                        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                        lightness = clahe.apply(lightness)
                        lab = cv2.merge([lightness, a, b])
                        img = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
                    else:
                        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                        img = clahe.apply(img)
                    applied.append(step.value)

                elif step == PreprocessingStep.ORIENTATION:
                    img = self._correct_orientation(img)
                    applied.append(step.value)

                elif step == PreprocessingStep.RESOLUTION:
                    img = self._enhance_resolution(img, cv2)
                    applied.append(step.value)

            except Exception as exc:
                logger.warning("Preprocessing step '%s' failed: %s — skipping", step.value, exc)
                continue

        return img, applied

    # ------------------------------------------------------------------
    # Individual operations
    # ------------------------------------------------------------------

    @staticmethod
    def _deskew(image: np.ndarray, cv2) -> np.ndarray:
        """Correct skew/rotation in a document image."""
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if len(image.shape) == 3 else image

        gray = cv2.bitwise_not(gray)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

        coords = np.column_stack(np.where(binary > 0))
        if len(coords) < 10:
            return image

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle
        elif angle > 45:
            angle = angle - 90

        if abs(angle) < 0.5:
            return image

        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            image,
            matrix,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        logger.debug("Deskew applied: %.2f degrees", angle)
        return rotated

    @staticmethod
    def _correct_orientation(image: np.ndarray) -> np.ndarray:
        """Detect and correct page orientation (0/90/180/270 degrees)."""
        try:
            import cv2
            import pytesseract

            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if len(image.shape) == 3 else image

            osd = pytesseract.image_to_osd(gray, output_type=pytesseract.Output.DICT)
            angle = osd.get("rotate", 0)
            if angle != 0:
                h, w = image.shape[:2]
                center = (w // 2, h // 2)
                matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
                image = cv2.warpAffine(
                    image,
                    matrix,
                    (w, h),
                    flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REPLICATE,
                )
                logger.debug("Orientation corrected: %d degrees", angle)
        except Exception:
            pass
        return image

    @staticmethod
    def _enhance_resolution(image: np.ndarray, cv2) -> np.ndarray:
        """Upscale small images to improve OCR accuracy on low-res scans."""
        h, w = image.shape[:2]
        if max(h, w) >= _MAX_DIMENSION:
            return image

        largest_dim = max(h, w)
        if largest_dim < 2000:
            scale = min(2.0, _MAX_DIMENSION / largest_dim)
            new_size = (int(w * scale), int(h * scale))
            if scale > 1.1:
                image = cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)
                logger.debug("Resolution enhanced: %.1fx upscale", scale)
        return image
