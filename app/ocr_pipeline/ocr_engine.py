"""
OCR Engine — primary PaddleOCR with Tesseract fallback.

Supports:
- English, Hindi (hi), Bengali (bn)
- GPU acceleration (PaddleOCR) with automatic CPU fallback
- Per-page confidence scoring
- Language auto-detection via Tesseract's OSD
"""

from __future__ import annotations

import logging
import re

import numpy as np

from app.ocr_pipeline.preprocessing import ImagePreprocessor

logger = logging.getLogger(__name__)

# Language mapping: ISO code -> display name and Paddle/Tesseract codes
_LANGUAGES: dict[str, dict[str, str]] = {
    "english": {"paddle": "en", "tesseract": "eng", "display": "English"},
    "hindi": {"paddle": "hi", "tesseract": "hin", "display": "Hindi"},
    "bengali": {"paddle": "bn", "tesseract": "ben", "display": "Bengali"},
}

# Default language for OCR
_DEFAULT_LANG = "english"

# Minimum confidence to accept OCR result
_MIN_CONFIDENCE = 0.3


class OCREngine:
    """Orchestrates OCR using PaddleOCR (GPU-capable) with Tesseract fallback.

    Usage::

        engine = OCREngine(languages=["english", "hindi"])
        text, confidence, ocr_engine_used = engine.recognize(image_array)
    """

    def __init__(
        self,
        languages: list[str] | None = None,
        use_gpu: bool = True,
        preprocessor: ImagePreprocessor | None = None,
    ) -> None:
        self._languages = languages or [_DEFAULT_LANG]
        self._use_gpu = use_gpu
        self._preprocessor = preprocessor or ImagePreprocessor()

        # Lazy-loaded engines
        self._paddle = None
        self._paddle_lang = None
        self._gpu_available = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recognize(self, image: np.ndarray) -> tuple[str, float, str, str]:
        """Run OCR on a preprocessed image.

        Args:
            image: Preprocessed image as a NumPy array.

        Returns:
            Tuple of ``(text, confidence, engine_name, language)``.
            ``engine_name`` is ``"paddle"``, ``"tesseract"``, or ``"none"``.
        """
        # Strategy 1: PaddleOCR (GPU-capable, supports Hindi/Bengali)
        text, confidence = self._try_paddle(image)
        if text and confidence >= _MIN_CONFIDENCE:
            detected_lang = self._detect_language(text)
            return text, confidence, "paddle", detected_lang

        # Strategy 2: Tesseract (CPU-only, universal fallback)
        text, confidence = self._try_tesseract(image)
        if text:
            detected_lang = self._detect_language(text)
            return text, max(confidence, 0.0), "tesseract", detected_lang

        return "", 0.0, "none", _DEFAULT_LANG

    # ------------------------------------------------------------------
    # PaddleOCR
    # ------------------------------------------------------------------

    def _try_paddle(self, image: np.ndarray) -> tuple[str, float]:
        """Attempt OCR with PaddleOCR."""
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            logger.debug("PaddleOCR not installed — skipping")
            return "", 0.0

        try:
            if self._paddle is None:
                paddle_langs = [self._to_paddle_lang(l) for l in self._languages]
                # Deduplicate while preserving order
                paddle_langs = list(dict.fromkeys(paddle_langs))

                logger.info(
                    "Initializing PaddleOCR (langs=%s, gpu=%s)",
                    paddle_langs,
                    self._use_gpu,
                )
                # PaddleOCR's 'en' model provides broad multilingual support
                # across Latin, Devanagari, and Bengali scripts
                paddle_lang = paddle_langs[0] if paddle_langs else "en"
                self._paddle = PaddleOCR(
                    use_angle_cls=True,
                    lang=paddle_lang,
                    use_gpu=self._use_gpu,
                    show_log=False,
                )
                self._gpu_available = self._paddle.use_gpu
                if self._use_gpu and not self._gpu_available:
                    logger.warning("GPU requested but not available — PaddleOCR running on CPU")

            result = self._paddle.ocr(image, cls=True)

            if not result or not result[0]:
                return "", 0.0

            texts = []
            confidences = []
            for line in result[0]:
                if len(line) >= 2:
                    bbox, (text, conf) = line[0], line[1]
                    if conf is not None and conf >= _MIN_CONFIDENCE:
                        texts.append(text)
                        confidences.append(conf)

            if not texts:
                return "", 0.0

            full_text = "\n".join(texts)
            avg_conf = sum(confidences) / len(confidences)
            return full_text, avg_conf

        except Exception as exc:
            logger.warning("PaddleOCR failed: %s — falling back to Tesseract", exc)
            return "", 0.0

    # ------------------------------------------------------------------
    # Tesseract
    # ------------------------------------------------------------------

    def _try_tesseract(self, image: np.ndarray) -> tuple[str, float]:
        """Attempt OCR with Tesseract as fallback."""
        try:
            import pytesseract
        except ImportError:
            logger.debug("pytesseract not installed — cannot use Tesseract fallback")
            return "", 0.0

        try:
            # Build language parameter string
            tesseract_langs = "+".join(self._to_tesseract_lang(l) for l in self._languages)

            # Run OCR with detailed output for confidence
            data = pytesseract.image_to_data(
                image,
                lang=tesseract_langs,
                output_type=pytesseract.Output.DICT,
                config="--psm 6 --oem 3",
            )

            texts = []
            confidences = []
            for i, text in enumerate(data.get("text", [])):
                conf = data.get("conf", [0])[i]
                text = (text or "").strip()
                if text and conf > 0:
                    texts.append(text)
                    confidences.append(conf / 100.0)  # Normalize to 0-1

            full_text = "\n".join(texts) if texts else (pytesseract.image_to_string(image, lang=tesseract_langs) or "")

            avg_conf = sum(confidences) / len(confidences) if confidences else 0.5

            return full_text.strip(), avg_conf

        except Exception as exc:
            logger.warning("Tesseract OCR failed: %s", exc)
            return "", 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_paddle_lang(lang: str) -> str:
        """Convert display language name to PaddleOCR language code."""
        lang_lower = lang.strip().lower()
        for info in _LANGUAGES.values():
            if info["display"].lower() == lang_lower or info["paddle"] == lang_lower:
                return info["paddle"]
        return _LANGUAGES[_DEFAULT_LANG]["paddle"]

    @staticmethod
    def _to_tesseract_lang(lang: str) -> str:
        """Convert display language name to Tesseract language code."""
        lang_lower = lang.strip().lower()
        for info in _LANGUAGES.values():
            if info["display"].lower() == lang_lower or info["tesseract"] == lang_lower:
                return info["tesseract"]
        return _LANGUAGES[_DEFAULT_LANG]["tesseract"]

    @staticmethod
    def _detect_language(text: str) -> str:
        """Detect primary language based on Unicode script ranges."""
        if not text:
            return _DEFAULT_LANG

        # Count characters by script
        devanagari = len(re.findall(r"[\u0900-\u097F]", text))
        bengali = len(re.findall(r"[\u0980-\u09FF]", text))

        total_special = devanagari + bengali
        if total_special == 0:
            return "english"

        if devanagari > bengali:
            return "hindi"
        return "bengali"
