"""Batch statistics aggregator for the Legal Document Cleaning Pipeline.

Uses pandas to collect, aggregate, and export cleaning statistics
across large batches of documents.
"""

from __future__ import annotations

import logging

import pandas as pd

from app.document_cleaner.models import CleanedDocument

logger = logging.getLogger(__name__)


class CleaningStats:
    """Aggregate cleaning statistics across multiple documents using pandas.

    Usage::

        stats = CleaningStats()
        for doc in documents:
            result = cleaner.clean(doc)
            stats.add_result(result)

        # Get summary DataFrame
        df = stats.to_dataframe()
        print(df.describe())

        # Export to CSV
        stats.to_csv("cleaning_report.csv")
    """

    def __init__(self) -> None:
        self._records: list[dict] = []

    def add_result(self, result: CleanedDocument) -> None:
        """Record a single cleaning result."""
        record = {
            "original_length": result.report.original_length,
            "clean_length": result.report.clean_length,
            "chars_removed": result.report.total_chars_removed,
            "items_removed": result.report.total_items_removed,
            "compression_ratio": result.report.compression_ratio,
            "pct_removed": (
                (result.report.total_chars_removed / result.report.original_length * 100)
                if result.report.original_length > 0
                else 0.0
            ),
        }
        # Add per-category breakdown
        for item in result.report.removed_items:
            record[f"removed_{item.category}"] = item.count
            record[f"saved_{item.category}"] = item.chars_saved

        self._records.append(record)

    def to_dataframe(self) -> pd.DataFrame:
        """Return cleaning statistics as a pandas DataFrame.

        Each row is one document. Columns include aggregate stats
        and per-category breakdowns.
        """
        return pd.DataFrame(self._records)

    def describe(self) -> str:
        """Return a text summary of the batch cleaning statistics."""
        if not self._records:
            return "No documents processed."

        df = self.to_dataframe()
        df.describe()

        lines = [
            "═══ Batch Cleaning Statistics ═══",
            f"  Documents processed: {len(self._records)}",
            "",
            "  Size statistics:",
            f"    Original length:  mean={df['original_length'].mean():.0f}, total={df['original_length'].sum():,}",
            f"    Cleaned length:   mean={df['clean_length'].mean():.0f}, total={df['clean_length'].sum():,}",
            f"    Chars removed:    mean={df['chars_removed'].mean():.0f}, total={df['chars_removed'].sum():,}",
            f"    Avg compression:  {df['compression_ratio'].mean():.4f}x",
            f"    Avg items removed: {df['items_removed'].mean():.1f}",
            "",
            "  Distribution:",
            f"    Max compression:   {df['compression_ratio'].max():.4f}x",
            f"    Min compression:   {df['compression_ratio'].min():.4f}x",
        ]
        return "\n".join(lines)

    def to_csv(self, path: str) -> None:
        """Export the cleaning statistics DataFrame to a CSV file."""
        df = self.to_dataframe()
        df.to_csv(path, index=False)
        logger.info("Cleaning statistics exported to %s (%d rows)", path, len(df))

    def reset(self) -> None:
        """Clear all accumulated records."""
        self._records.clear()
