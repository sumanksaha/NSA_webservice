"""
Before/after differ module for the Legal Document Cleaning Pipeline.

Produces a structured diff report showing what lines were removed or changed
during the cleaning process.
"""

from __future__ import annotations

import difflib
import logging

logger = logging.getLogger(__name__)

DiffLine = tuple[str, int, str]  # (operation, line_num, text) where operation is '+', '-', or ' '


class DocumentDiffer:
    """Generates structured before/after comparison reports.

    Uses Python's ``difflib.SequenceMatcher`` for line-level diffing.
    """

    def diff(self, original: str, cleaned: str) -> dict:
        """Produce a structured diff between original and cleaned text.

        Args:
            original: Original raw document text.
            cleaned: Cleaned document text.

        Returns:
            A dict with keys:
            - ``original_lines``: total lines in original
            - ``cleaned_lines``: total lines in cleaned
            - ``lines_removed``: number of lines entirely removed
            - ``lines_changed``: number of lines with modifications
            - ``lines_unchanged``: number of lines identical in both
            - ``diff_blocks``: list of diff hunk dicts for display
        """
        orig_lines = original.splitlines()
        clean_lines = cleaned.splitlines()

        matcher = difflib.SequenceMatcher(None, orig_lines, clean_lines)
        diff_blocks: list[dict] = []

        removed_count = 0
        changed_count = 0
        unchanged_count = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            block: dict = {
                "tag": tag,
                "original_start": i1,
                "original_end": i2,
                "cleaned_start": j1,
                "cleaned_end": j2,
                "original_lines": orig_lines[i1:i2],
                "cleaned_lines": clean_lines[j1:j2],
            }
            diff_blocks.append(block)

            if tag == "equal":
                unchanged_count += i2 - i1
            elif tag == "delete":
                removed_count += i2 - i1
            elif tag == "replace":
                changed_count += max(i2 - i1, j2 - j1)
            # insert is added content, not really "changed" from original

        return {
            "original_lines": len(orig_lines),
            "cleaned_lines": len(clean_lines),
            "lines_removed": removed_count,
            "lines_changed": changed_count,
            "lines_unchanged": unchanged_count,
            "diff_blocks": diff_blocks,
        }

    def summary_text(self, diff_result: dict) -> str:
        """Return a human-readable summary of the diff."""
        total = diff_result["original_lines"]
        removed = diff_result["lines_removed"]
        changed = diff_result["lines_changed"]
        unchanged = diff_result["lines_unchanged"]

        pct_removed = (removed / total * 100) if total else 0
        pct_unchanged = (unchanged / total * 100) if total else 0

        lines = [
            "── Cleaning Diff Summary ──",
            f"  Original lines:  {total}",
            f"  Cleaned lines:   {diff_result['cleaned_lines']}",
            f"  Lines removed:   {removed} ({pct_removed:.1f}%)",
            f"  Lines changed:   {changed}",
            f"  Lines unchanged: {unchanged} ({pct_unchanged:.1f}%)",
        ]
        return "\n".join(lines)

    def unified_diff(self, original: str, cleaned: str, n: int = 3) -> str:
        """Return a unified-diff style string similar to ``diff -u``."""
        orig_lines = original.splitlines(keepends=True)
        clean_lines = cleaned.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(orig_lines, clean_lines, n=n),
        )
        return "".join(diff_lines)
