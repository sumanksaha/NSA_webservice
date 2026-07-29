"""
Pydantic models for the Legal Document Loader output schema.

All models use ``frozen=True`` (immutable) for hashability and thread-safety,
and ``slots=True`` for reduced memory footprint at scale.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "DocumentResult",
    "FileMetadata",
    "PageResult",
]


class FileMetadata(BaseModel):
    """Metadata extracted from the source file."""

    model_config = ConfigDict(frozen=True, slots=True)

    file_size_bytes: int = Field(ge=0, description="File size in bytes")
    created_at: datetime | None = Field(None, description="File creation timestamp")
    modified_at: datetime | None = Field(None, description="File last-modified timestamp")
    encoding: str | None = Field(None, description="Detected text encoding (text files)")
    page_count: int | None = Field(None, ge=0, description="Total number of pages detected")


class PageResult(BaseModel):
    """A single page with its 1-based number and extracted text."""

    model_config = ConfigDict(frozen=True, slots=True)

    page: int = Field(ge=1, description="1-based page number")
    text: str = Field(default="", description="Extracted text content for this page")


class DocumentResult(BaseModel):
    """Unified output structure for any loaded legal document."""

    model_config = ConfigDict(frozen=True, slots=True)

    document_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique document identifier (hex UUID4)",
    )
    file_name: str = Field(description="Original file name with extension")
    file_type: str = Field(description="Normalised file type, e.g. 'pdf', 'docx', 'txt'")
    pages: list[PageResult] = Field(
        default_factory=list,
        description="Ordered list of pages with extracted text",
    )
    metadata: FileMetadata = Field(
        description="File-level metadata",
    )

    @field_validator("file_type")
    @classmethod
    def _normalise_file_type(cls, v: str) -> str:
        raw = v.strip().lower().lstrip(".")
        valid = {"pdf", "docx", "txt"}
        if raw not in valid:
            raise ValueError(f"Unsupported file type: '{raw}'. Must be one of {valid}")
        return raw

    @property
    def text(self) -> str:
        """Concatenated text of all pages separated by page breaks."""
        return "\n\n".join(p.text for p in self.pages)

    @property
    def total_pages(self) -> int:
        """Total number of pages extracted."""
        return len(self.pages)
