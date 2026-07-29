"""
Pydantic models for the Legal Document Cleaning Pipeline.

Includes:
- ``CleaningConfig`` — config-driven rule flags
- ``RemovedItem`` — single item removed during cleaning
- ``CleaningReport`` — summary of all cleaning actions
- ``CleanedDocument`` — final output with cleaned text and report
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CleaningConfig(BaseModel):
    """Configuration flags for each cleaning operation.

    All flags default to True — the pipeline performs aggressive cleaning
    by default. Set any flag to False to skip that operation.
    """

    model_config = ConfigDict(frozen=True)

    # --- Removal operations ---
    remove_headers: bool = Field(True, description="Remove repeated header lines")
    remove_footers: bool = Field(True, description="Remove repeated footer lines")
    remove_page_numbers: bool = Field(True, description="Remove standalone page numbers")
    remove_blank_pages: bool = Field(True, description="Remove pages with only whitespace")
    remove_watermark_text: bool = Field(True, description="Remove watermark/common disclaimer text")
    remove_duplicate_lines: bool = Field(True, description="Remove consecutive duplicate lines")
    remove_running_titles: bool = Field(True, description="Remove running titles repeated per page")
    remove_ocr_artifacts: bool = Field(True, description="Remove common OCR artifacts and garbage")

    # --- Normalization operations ---
    normalize_unicode: bool = Field(True, description="NFKC Unicode normalization")
    normalize_spaces: bool = Field(True, description="Collapse multiple spaces to one")
    normalize_tabs: bool = Field(True, description="Replace tabs with spaces")
    normalize_linebreaks: bool = Field(True, description="Collapse excessive blank lines")
    normalize_hyphens: bool = Field(True, description="Rejoin hyphenated words split across lines")
    normalize_quotes: bool = Field(True, description="Normalize curly/smart quotes to straight")
    normalize_bullets: bool = Field(True, description="Normalize bullet characters to standard *")
    normalize_encoding: bool = Field(True, description="Fix common encoding artifacts")

    # --- Preservation (regex patterns kept during cleaning) ---
    preserve_section_numbers: bool = Field(True, description="Keep section/sub-section numbering")
    preserve_legal_numbering: bool = Field(True, description="Keep Act/Rule/Regulation numbering")
    preserve_tables: bool = Field(True, description="Keep table-like structures (rows/cols)")
    preserve_clauses: bool = Field(True, description="Keep clause/sub-clause formatting")
    preserve_citations: bool = Field(True, description="Keep legal citations (case names, statutes)")
    preserve_references: bool = Field(True, description="Keep cross-references and footnotes")


class RemovedItem(BaseModel):
    """Record of a single item removed during cleaning."""

    model_config = ConfigDict(frozen=True)

    category: str = Field(description="Category: header, footer, page_number, etc.")
    snippet: str = Field(description="First 120 characters of removed text")
    count: int = Field(ge=1, description="Number of occurrences removed")
    chars_saved: int = Field(ge=0, description="Total characters removed")


class CleaningReport(BaseModel):
    """Summary report of all cleaning operations performed."""

    model_config = ConfigDict(frozen=True)

    original_length: int = Field(ge=0, description="Character count before cleaning")
    clean_length: int = Field(ge=0, description="Character count after cleaning")
    total_chars_removed: int = Field(ge=0, description="Total characters removed")
    total_items_removed: int = Field(ge=0, description="Total items removed across all categories")
    removed_items: list[RemovedItem] = Field(
        default_factory=list,
        description="Per-category breakdown of removed items",
    )

    @property
    def compression_ratio(self) -> float:
        """Ratio of original to clean length (1.0 = no change)."""
        if self.clean_length == 0:
            return 1.0
        return round(self.original_length / self.clean_length, 4)


class CleanedDocument(BaseModel):
    """Final output: cleaned text along with cleaning report."""

    model_config = ConfigDict(frozen=True)

    clean_text: str = Field(description="The fully cleaned document text")
    report: CleaningReport = Field(description="Cleaning operation summary report")
