"""Removal operations for the Legal Document Cleaning Pipeline.

Each function takes ``(lines: list[str]) -> tuple[list[str], list[RemovedItem]]``
and returns the cleaned lines plus a list of removal records.
"""

from __future__ import annotations

import logging
import re

from app.document_cleaner.models import RemovedItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled patterns (module-level for speed)
# ---------------------------------------------------------------------------

# Page number patterns
_PAGE_NUM_PATTERNS = re.compile(
    r"^\s*(?:Page\s+\d+|-\s*\d+\s*-|\d+\s*of\s*\d+|\d+\s*/\s*\d+)\s*$",
    re.IGNORECASE,
)

# Common watermark / disclaimer lines
_WATERMARK_PATTERNS = re.compile(
    r"^\s*("
    r"CONFIDENTIAL|DRAFT|DO\s+NOT\s+COPY|PRIVILEGED\s+AND\s+CONFIDENTIAL"
    r"|PROTECTED|ATTORNEY\s+WORK\s+PRODUCT|PREPARED\s+BY"
    r"|UNAUTHORIZED\s+(?:USE|REPRODUCTION|DISTRIBUTION)"
    r"|DOCUMENT\s+CLASSIFIED|LEGAL\s+DISCLAIMER"
    r"|THIS\s+IS\s+A\s+SYSTEM\-?GENERATED\s+DOCUMENT"
    r"|INTERNAL\s+USE\s+ONLY|PRINTED\s+ON\s+\d+|GENERATED\s+ON\s+\d+"
    r")\s*$",
    re.IGNORECASE,
)

# OCR artifact patterns — keep all printable ASCII + Indian scripts + common unicode punctuation
# Build the allowed character set explicitly to avoid escaping issues.
_ALLOWED_OCR = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;:!?()[]{}'\"-\\/\\@#$%&*+<=>^_`|~\n\u2013\u2014",
)
# Add Indian script Unicode ranges
_OCR_ALLOWED_RANGES = [
    (0x0900, 0x097F),  # Devanagari
    (0x0980, 0x09FF),  # Bengali
    (0x0A00, 0x0A7F),  # Gurmukhi
    (0x0B00, 0x0B7F),  # Oriya
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0C80, 0x0CFF),  # Kannada
    (0x0D00, 0x0D7F),  # Malayalam
    (0x2010, 0x2030),  # Dashes, smart quotes, per mille
    (0x2032, 0x2034),  # Primes
    (0x00A1, 0x00BF),  # Spanish punctuation
]
for lo, hi in _OCR_ALLOWED_RANGES:
    for cp in range(lo, hi + 1):
        _ALLOWED_OCR.add(chr(cp))

# Build the negated character class regex dynamically
_OCR_GARBAGE_PATTERN = "[^" + "".join(sorted(_ALLOWED_OCR)) + "]"
_OCR_GARBAGE = re.compile(_OCR_GARBAGE_PATTERN)

# Lines that look like page boundary markers
_PAGE_BOUNDARY = re.compile(r"^\s*[-\u2013\u2014]+\s*$")

# Common header/footer pattern: repeated short line appearing at same position
_HEADER_FOOTER_SHORT = re.compile(r"^.{3,80}$")

# Running title pattern: short uppercase line
_RUNNING_TITLE = re.compile(r"^[A-Z][A-Z\s&,.]{3,60}$")

# Preservation patterns (lines we should NOT remove even if they match)
_PRESERVE_PATTERNS = [
    re.compile(r"Section\s+\d+[A-Za-z]?", re.IGNORECASE),
    re.compile(r"^\d+\.\s"),  # numbered list
    re.compile(r"^\(\w\)\s"),  # sub-clause
    re.compile(r"^Clause\s+\d+", re.IGNORECASE),
    re.compile(r"Schedule\s+[IVXLCDM\d]", re.IGNORECASE),
    # Tables: lines with pipe/bar separators or clearly tabular structure
    re.compile(r"^\s*\|.*\|\s*$"),  # markdown-style table rows
    re.compile(r"^\s*[+|=-]{5,}\s*$"),  # table separators (horizontal rules)
    # Tabular data: 4+ short columns with minimum double-spacing
    re.compile(r"^\s*\S{1,15}(?:\s{2,}\S{1,15}){3,}\s*$"),
    re.compile(r"^\s*\d+\.?\s+\S{1,20}\s+\S{1,20}\s+\S{1,20}\s*"),  # numbered table rows
    # Citations (Indian legal citations)
    re.compile(r"\(\d{4}\)\s+\d+\s+SCC\s+\d+"),  # (2020) 12 SCC 345
    re.compile(r"AIR\s+\d{4}\s+(?:SC|\w+)\s+\d+"),  # AIR 2020 SC 1234
    re.compile(r"\b\d{4}\s+\(\d+\)\s+\w+"),  # generic law report citation
    re.compile(r"(?:JT|SCR|CrLJ|PLJR)\s+\(?\d+\)?"),  # journal citations
    # References / cross-references
    re.compile(r"(?:See|Refer|Vide|Cf\.|Supra|Infra|Ibid|Ante|Post)\b", re.IGNORECASE),
    re.compile(r"(?:as\s+referred\s+to|hereinafter|thereinabove|aforesaid)", re.IGNORECASE),
]


def remove_page_numbers(lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
    """Remove standalone page number lines."""
    removed: list[str] = []
    kept: list[str] = []
    for line in lines:
        if line and _PAGE_NUM_PATTERNS.match(line) and not _should_preserve(line):
            removed.append(line)
        else:
            kept.append(line)
    if removed:
        return kept, [
            RemovedItem(
                category="page_number",
                snippet=removed[0][:120],
                count=len(removed),
                chars_saved=sum(len(line) + 1 for line in removed),
            ),
        ]
    return kept, []


def remove_watermark_text(lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
    """Remove watermark/disclaimer lines."""
    removed: list[str] = []
    kept: list[str] = []
    for line in lines:
        if line and _WATERMARK_PATTERNS.match(line) and not _should_preserve(line):
            removed.append(line)
        else:
            kept.append(line)
    if removed:
        return kept, [
            RemovedItem(
                category="watermark_text",
                snippet=removed[0][:120],
                count=len(removed),
                chars_saved=sum(len(line) + 1 for line in removed),
            ),
        ]
    return kept, []


def remove_blank_pages(lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
    """Remove blank lines / pages (consecutive whitespace-only lines)."""
    non_blank: list[str] = []
    removed_count = 0
    for line in lines:
        if line and line.strip():
            non_blank.append(line)
        else:
            removed_count += 1
    if removed_count:
        return non_blank, [
            RemovedItem(
                category="blank_page",
                snippet="<blank lines>",
                count=removed_count,
                chars_saved=removed_count,
            ),
        ]
    return non_blank, []


def remove_duplicate_lines(lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
    """Remove consecutive duplicate lines."""
    kept: list[str] = []
    removed: list[str] = []
    prev = None
    for line in lines:
        stripped = line.strip()
        if prev is not None and stripped == prev and not _should_preserve(line):
            removed.append(line)
        else:
            kept.append(line)
        prev = stripped
    if removed:
        return kept, [
            RemovedItem(
                category="duplicate_line",
                snippet=removed[0][:120],
                count=len(removed),
                chars_saved=sum(len(line) + 1 for line in removed),
            ),
        ]
    return kept, []


def remove_ocr_artifacts(text: str) -> tuple[str, list[RemovedItem]]:
    """Remove OCR artifact characters from text (operates on full text, not lines)."""
    original_len = len(text)
    cleaned = _OCR_GARBAGE.sub("", text)
    chars_removed = original_len - len(cleaned)
    if chars_removed:
        return cleaned, [
            RemovedItem(
                category="ocr_artifact",
                snippet="<non-printable/garbage chars>",
                count=chars_removed,
                chars_saved=chars_removed,
            ),
        ]
    return cleaned, []


def remove_headers_footers(
    lines: list[str],
    min_repeat: int = 3,
) -> tuple[list[str], list[RemovedItem]]:
    """Remove repeated header/footer lines using frequency analysis.

    A line appearing at the same relative position across enough pages
    is flagged as a header or footer.
    """
    if len(lines) < 20:
        return lines, []  # too few lines for reliable detection

    # Count line occurrences by position buckets
    bucket_size = max(1, len(lines) // 40)  # assume ~40 lines per page
    position_counts: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) < 5:
            continue
        if not _HEADER_FOOTER_SHORT.match(stripped):
            continue
        bucket = i // bucket_size
        key = f"{bucket}:{stripped}"
        position_counts[key] = position_counts.get(key, 0) + 1

    # Find lines that repeat in same position bucket often enough
    repeat_threshold = max(3, len(lines) // (bucket_size * 4))
    to_remove: set = set()
    for key, count in position_counts.items():
        if count >= repeat_threshold and count >= min_repeat:
            _, line_text = key.split(":", 1)
            to_remove.add(line_text)

    if not to_remove:
        return lines, []

    kept: list[str] = []
    removed: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in to_remove and not _should_preserve(line):
            removed.append(line)
        else:
            kept.append(line)

    if removed:
        return kept, [
            RemovedItem(
                category="header_footer",
                snippet=removed[0][:120],
                count=len(removed),
                chars_saved=sum(len(line) + 1 for line in removed),
            ),
        ]
    return kept, []


def remove_running_titles(lines: list[str]) -> tuple[list[str], list[RemovedItem]]:
    """Remove running titles (short uppercase lines that appear frequently)."""
    kept: list[str] = []
    removed: list[str] = []
    seen: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if stripped and _RUNNING_TITLE.match(stripped):
            seen[stripped] = seen.get(stripped, 0) + 1

    # Flag lines that appear more than once as running titles
    repeated = {line for line, cnt in seen.items() if cnt > 1}
    if not repeated:
        return lines, []

    for line in lines:
        stripped = line.strip()
        if stripped in repeated and not _should_preserve(line):
            removed.append(line)
        else:
            kept.append(line)

    if removed:
        return kept, [
            RemovedItem(
                category="running_title",
                snippet=removed[0][:120],
                count=len(removed),
                chars_saved=sum(len(line) + 1 for line in removed),
            ),
        ]
    return kept, []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _should_preserve(line: str) -> bool:
    """Check if a line matches any preservation pattern."""
    stripped = line.strip()
    return any(pat.search(stripped) for pat in _PRESERVE_PATTERNS)
