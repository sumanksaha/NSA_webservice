"""Pipeline orchestrator for the Legal Document Cleaning Pipeline.

Chains together removal and normalization operations in a config-driven
sequence. Produces a ``CleanedDocument`` with cleaned text and report.
"""

from __future__ import annotations

import json
import logging

from app.document_cleaner.config import PRESETS
from app.document_cleaner.models import CleanedDocument, CleaningConfig, CleaningReport, RemovedItem
from app.document_cleaner.normalizers import NORMALIZER_REGISTRY, normalize_linebreaks
from app.document_cleaner.removers import (
    remove_blank_pages,
    remove_duplicate_lines,
    remove_headers_footers,
    remove_ocr_artifacts,
    remove_page_numbers,
    remove_running_titles,
    remove_watermark_text,
)

logger = logging.getLogger(__name__)


def _rust_normalize(text: str, apply_hyphens: bool = True) -> str | None:
    """Run the normalizer pipeline through the ``nsa_rust`` extension if present.

    Returns the native-Rust-normalized string, or ``None`` when the compiled
    extension is not importable (graceful degradation — the caller falls back
    to the pure-Python registry).
    """
    try:
        from nsa_rust import normalize_text as _rust_normalize_text
    except ImportError:  # pragma: no cover - depends on build environment
        return None
    return _rust_normalize_text(text, apply_hyphens)


def _rust_run_removers(
    lines: list[str], config
) -> tuple[list[str], list[RemovedItem]] | None:
    """Run the config-driven remover sequence through ``nsa_rust.run_removers``.

    Returns ``(kept_lines, removed_items)``, or ``None`` when the compiled
    extension is unavailable or errors (graceful degradation to pure Python).
    """
    try:
        from nsa_rust import run_removers as _rust_run_removers
    except ImportError:  # pragma: no cover - depends on build environment
        return None

    cfg = {
        "remove_blank_pages": bool(getattr(config, "remove_blank_pages", True)),
        "remove_headers": bool(getattr(config, "remove_headers", True)),
        "remove_footers": bool(getattr(config, "remove_footers", True)),
        "remove_running_titles": bool(getattr(config, "remove_running_titles", True)),
        "remove_page_numbers": bool(getattr(config, "remove_page_numbers", True)),
        "remove_watermark_text": bool(getattr(config, "remove_watermark_text", True)),
        "remove_duplicate_lines": bool(getattr(config, "remove_duplicate_lines", True)),
    }
    try:
        kept_lines, removed_json = _rust_run_removers(list(lines), json.dumps(cfg))
    except Exception:
        return None
    try:
        removed = [RemovedItem(**item) for item in json.loads(removed_json)]
    except Exception:
        removed = []
    return kept_lines, removed


def _rust_remove_ocr_artifacts(text: str) -> tuple[str, list[RemovedItem]] | None:
    """Strip OCR-garbage characters through ``nsa_rust.remove_ocr_artifacts``.

    Returns ``(cleaned_text, removed_items)``, or ``None`` when the compiled
    extension is unavailable or errors (graceful degradation to pure Python).
    """
    try:
        from nsa_rust import remove_ocr_artifacts as _rust_remove_ocr_artifacts
    except ImportError:  # pragma: no cover - depends on build environment
        return None
    try:
        cleaned, removed_json = _rust_remove_ocr_artifacts(text)
    except Exception:
        return None
    try:
        removed = [RemovedItem(**item) for item in json.loads(removed_json)]
    except Exception:
        removed = []
    return cleaned, removed



class DocumentCleaner:
    """High-performance legal document text cleaner.

    Usage::

        cleaner = DocumentCleaner()
        result = cleaner.clean(raw_text)
        print(result.clean_text[:500])

    Thread-safe — one ``DocumentCleaner`` instance can be shared across threads.
    All instance state is read-only after construction.
    """

    def __init__(self, config: CleaningConfig | str | None = None) -> None:
        """Initialise the cleaner with an optional config dict or preset name.

        Args:
            config: A ``CleaningConfig`` instance, a preset string name
                (``"aggressive"``, ``"conservative"``, ``"ocr"``), or ``None``
                to use the aggressive preset.

        """
        if config is None:
            self.config = PRESETS["aggressive"]
        elif isinstance(config, str):
            preset: CleaningConfig | None = PRESETS.get(config.lower())
            if preset is None:
                msg = f"Unknown preset: {config}. Available: {list(PRESETS)}"
                raise ValueError(msg)
            self.config = preset
        else:
            self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clean(self, text: str) -> CleanedDocument:
        """Run the full cleaning pipeline on a document text.

        Args:
            text: Raw document text to clean.

        Returns:
            A ``CleanedDocument`` with the cleaned text and a cleaning report.

        """
        if not text:
            return CleanedDocument(
                clean_text="",
                report=CleaningReport(original_length=0, clean_length=0, total_chars_removed=0, total_items_removed=0),
            )

        original_length = len(text)
        all_removed: list[RemovedItem] = []
        lines = text.splitlines()

        # --- Phase 1: Line-level removal operations ---
        lines, items = self._run_removers(lines)
        all_removed.extend(items)

        # --- Phase 2: Rejoin lines and remove full-text OCR artifacts first ---
        cleaned = "\n".join(lines)
        if self.config.remove_ocr_artifacts:
            rust_ocr = _rust_remove_ocr_artifacts(cleaned)
            if rust_ocr is not None:
                cleaned, items = rust_ocr
            else:
                cleaned, items = remove_ocr_artifacts(cleaned)
            all_removed.extend(items)

        # --- Phase 3: Normalization (runs after OCR cleanup) ---
        cleaned = self._run_normalizers(cleaned)

        # --- Phase 4: Final line-break collapse ---
        if self.config.normalize_linebreaks:
            cleaned = normalize_linebreaks(cleaned)

        # Build report
        total_chars_removed = original_length - len(cleaned)
        total_items = sum(item.count for item in all_removed)

        report = CleaningReport(
            original_length=original_length,
            clean_length=len(cleaned),
            total_chars_removed=total_chars_removed,
            total_items_removed=total_items,
            removed_items=all_removed,
        )

        return CleanedDocument(clean_text=cleaned, report=report)

    # ------------------------------------------------------------------
    # Internal pipeline execution
    # ------------------------------------------------------------------

    def _run_removers(self, lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
        """Run all configured removal operations on the line list."""
        # Native-Rust path (mirrors the exact remover sequence below). It is
        # config-driven and safe for every preset, so it is preferred whenever
        # the compiled extension is available; otherwise fall back to Python.
        rust = _rust_run_removers(lines, self.config)
        if rust is not None:
            return rust

        all_removed: list[RemovedItem] = []

        # Order matters: blank removal first simplifies frequency analysis
        if self.config.remove_blank_pages:
            lines, items = remove_blank_pages(lines)
            all_removed.extend(items)

        if self.config.remove_headers or self.config.remove_footers:
            lines, items = remove_headers_footers(lines)
            all_removed.extend(items)

        if self.config.remove_running_titles:
            lines, items = remove_running_titles(lines)
            all_removed.extend(items)

        if self.config.remove_page_numbers:
            lines, items = remove_page_numbers(lines)
            all_removed.extend(items)

        if self.config.remove_watermark_text:
            lines, items = remove_watermark_text(lines)
            all_removed.extend(items)

        if self.config.remove_duplicate_lines:
            lines, items = remove_duplicate_lines(lines)
            all_removed.extend(items)

        return lines, all_removed

    def _run_normalizers(self, text: str) -> str:
        """Run all configured normalization operations on the text.

        Normalizer function names in ``NORMALIZER_REGISTRY`` use the naming
        convention ``normalize_<field_name>`` so they match ``CleaningConfig``
        field names automatically (e.g., ``normalize_spaces`` ↔ ``normalize_spaces``).
        """
        cfg = self.config
        # The Rust path reproduces the registry order (unicode, encoding,
        # bullets, quotes, tabs, hyphens, spaces, trailing_whitespace,
        # linebreaks) and is only valid when *every* normalizer is enabled —
        # i.e. the "aggressive"/"ocr" presets. Otherwise fall back to Python.
        all_normalizers_on = (
            getattr(cfg, "normalize_unicode", True)
            and getattr(cfg, "normalize_encoding", True)
            and getattr(cfg, "normalize_bullets", True)
            and getattr(cfg, "normalize_quotes", True)
            and getattr(cfg, "normalize_tabs", True)
            and getattr(cfg, "normalize_hyphens", True)
            and getattr(cfg, "normalize_spaces", True)
            and getattr(cfg, "normalize_linebreaks", True)
        )
        if all_normalizers_on:
            rust_out = _rust_normalize(text, apply_hyphens=True)
            if rust_out is not None:
                return rust_out

        for name, func in NORMALIZER_REGISTRY:
            # The config field name matches the normalizer function name
            # (e.g., normalize_unicode -> config.normalize_unicode)
            config_field = f"normalize_{name}" if not name.startswith("normalize_") else name
            # Special cases: trailing_whitespace shares config with linebreaks
            if name == "trailing_whitespace":
                config_field = "normalize_linebreaks"
            if getattr(self.config, config_field, True):
                try:
                    text = func(text)
                except Exception as exc:
                    logger.warning("Normalizer '%s' failed: %s", name, exc)
        return text
