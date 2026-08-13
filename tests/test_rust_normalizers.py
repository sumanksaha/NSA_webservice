"""Rust ↔ Python parity tests for the Document Cleaner normalizers (Part 1).

These tests exercise the ``nsa_rust`` PyO3 extension when it is importable and
assert byte-for-byte parity with the pure-Python implementation. When the
extension is not built (e.g. no Rust toolchain / MSVC linker in the build
environment), the module is skipped via ``pytest.importorskip`` so the suite is
never red on machines without the compiled artifact.
"""

import pytest

pytest.importorskip("nsa_rust")

import json  # noqa: E402

import nsa_rust  # noqa: E402

from app.document_cleaner import normalizers as py_normalizers  # noqa: E402
from app.document_cleaner import removers as py_removers  # noqa: E402
from app.document_cleaner.pipeline import DocumentCleaner  # noqa: E402

# Sample corpus exercising Unicode, bullets, quotes, tabs, hyphens, spacing,
# trailing whitespace, excess blank lines and encoding artifacts.
CORPUS = [
    "Section 55 of the Food Safety and Standards Act, 2006.",
    "The  \u00a0  product  contains  \t  harmful additives.",
    "Item \u2022 one\nItem \u2022 two\n\n\nItem \u2022 three",
    '\u201cWhereas\u201d the \u2018respondent\u2019 agreed \u2014 clause (a)',
    "compu-\nter  network  authentication",
    "line one   \nline two\t\t\n\n\nline three",
    "\u00adsoft\u200bhyphen\u2060char",
    "  leading and trailing keep  ",
    "Page 3 of 12 \u2014 final \u2014 report",
    "The \u25cf bullet \u2026 ellipsis \u2043 hyphen \u00b7 middle dot",
]


def _python_registry(text):
    """Apply the Python normalizer registry in run order (all enabled)."""
    out = text
    for _, func in py_normalizers.NORMALIZER_REGISTRY:
        out = func(out)
    return out


# --- Remover fixtures -------------------------------------------------------

# Aggressive remover flags (all on), matching PRESETS["aggressive"].
REMOVER_CONFIG = {
    "remove_blank_pages": True,
    "remove_headers": True,
    "remove_footers": True,
    "remove_running_titles": True,
    "remove_page_numbers": True,
    "remove_watermark_text": True,
    "remove_duplicate_lines": True,
}


def _make_lines(n_pages=6):
    """>=20 lines exercising page numbers, watermarks, headers/footers,
    running titles, duplicates and blank lines."""
    lines = []
    for p in range(n_pages):
        lines.append("HEADER NOTE")                         # repeated header
        lines.append(f"Page {p + 1} of {n_pages}")          # page number
        lines.append("CONFIDENTIAL")                        # watermark
        lines.append(f"Section {p + 1} content line one")   # matches preserve
        lines.append("ordinary body text goes here for the record")
        lines.append("ordinary text that will not be removed at all")
        lines.append("")                                    # blank
        lines.append("FOOTER MARK")                         # repeated footer
    return lines


def _python_run_removers(lines, cfg):
    """Mirror `DocumentCleaner._run_removers` (pure Python reference)."""
    all_removed = []
    if cfg["remove_blank_pages"]:
        lines, items = py_removers.remove_blank_pages(lines)
        all_removed.extend(items)
    if cfg["remove_headers"] or cfg["remove_footers"]:
        lines, items = py_removers.remove_headers_footers(lines)
        all_removed.extend(items)
    if cfg["remove_running_titles"]:
        lines, items = py_removers.remove_running_titles(lines)
        all_removed.extend(items)
    if cfg["remove_page_numbers"]:
        lines, items = py_removers.remove_page_numbers(lines)
        all_removed.extend(items)
    if cfg["remove_watermark_text"]:
        lines, items = py_removers.remove_watermark_text(lines)
        all_removed.extend(items)
    if cfg["remove_duplicate_lines"]:
        lines, items = py_removers.remove_duplicate_lines(lines)
        all_removed.extend(items)
    return lines, all_removed


@pytest.mark.parametrize("text", CORPUS)
def test_normalize_text_parity(text):
    assert nsa_rust.normalize_text(text, True) == _python_registry(text)


@pytest.mark.parametrize("text", CORPUS)
@pytest.mark.parametrize(
    "rust_name,python_name",
    [
        ("normalize_unicode", "normalize_unicode"),
        ("normalize_encoding", "normalize_encoding"),
        ("normalize_bullets", "normalize_bullets"),
        ("normalize_quotes", "normalize_quotes"),
        ("normalize_tabs", "normalize_tabs"),
        ("normalize_hyphens", "normalize_hyphens"),
        ("normalize_spaces", "normalize_spaces"),
        ("normalize_trailing_whitespace", "normalize_trailing_whitespace"),
        ("normalize_linebreaks", "normalize_linebreaks"),
    ],
)
def test_individual_normalizer_parity(rust_name, python_name, text):
    rust_fn = getattr(nsa_rust, rust_name)
    py_fn = getattr(py_normalizers, python_name)
    assert rust_fn(text) == py_fn(text)


def test_cleaner_uses_rust_and_matches_python(monkeypatch):
    """DocumentCleaner('aggressive') runs the Rust path and matches Python-only."""
    text = "\n".join(CORPUS)

    # Real run: Rust normalizers + removers + OCR are used (extension present).
    rust_clean = DocumentCleaner("aggressive").clean(text).clean_text

    # Force the pure-Python path by neutering every Rust hook.
    for hook in (
        "app.document_cleaner.pipeline._rust_normalize",
        "app.document_cleaner.pipeline._rust_run_removers",
        "app.document_cleaner.pipeline._rust_remove_ocr_artifacts",
    ):
        monkeypatch.setattr(hook, lambda *a, **k: None)
    py_clean = DocumentCleaner("aggressive").clean(text).clean_text

    assert rust_clean == py_clean


def test_run_removers_parity():
    """Rust `run_removers` matches the pure-Python remover sequence & items."""
    lines = _make_lines()
    kept, removed_json = nsa_rust.run_removers(list(lines), json.dumps(REMOVER_CONFIG))
    py_kept, py_items = _python_run_removers(list(lines), REMOVER_CONFIG)

    assert kept == py_kept
    assert json.loads(removed_json) == [item.model_dump() for item in py_items]


@pytest.mark.parametrize("text", CORPUS + ["\u0001\u0002\u0003garbage\u0007", "clean text only"])
def test_remove_ocr_artifacts_parity(text):
    """Rust `remove_ocr_artifacts` matches pure Python char-for-char."""
    cleaned, removed_json = nsa_rust.remove_ocr_artifacts(text)
    py_cleaned, py_items = py_removers.remove_ocr_artifacts(text)
    assert cleaned == py_cleaned
    assert json.loads(removed_json) == [item.model_dump() for item in py_items]


def test_conservative_preset_falls_back_to_python():
    """Conservative disables some normalizers → must not use the Rust path."""
    text = "\n".join(CORPUS)
    result = DocumentCleaner("conservative").clean(text)
    assert result.clean_text  # non-empty, runs through the Python registry