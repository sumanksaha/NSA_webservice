"""
Pydantic models for the OCR Pipeline output schema.

Each page produces an ``OCRResult`` that includes the extracted text,
confidence scores, language detection, and detected objects (tables,
stamps, signatures, watermarks).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ObjectType(str, Enum):
    """Types of objects that can be detected on a page."""

    TABLE = "table"
    STAMP = "stamp"
    SIGNATURE = "signature"
    WATERMARK = "watermark"


class DetectedObject(BaseModel):
    """An object detected on a document page (table, stamp, signature, watermark)."""

    model_config = ConfigDict(frozen=True, slots=True)

    type: ObjectType = Field(description="Type of detected object")
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence score")
    bbox: list[float] = Field(
        description="Bounding box as [x1, y1, x2, y2] in pixel coordinates",
        min_length=4,
        max_length=4,
    )


class PageDetectionResult(BaseModel):
    """Results of object detection on a single page."""

    model_config = ConfigDict(frozen=True, slots=True)

    objects: list[DetectedObject] = Field(default_factory=list, description="Detected objects")
    has_table: bool = Field(False, description="Whether a table was detected")
    has_stamp: bool = Field(False, description="Whether a stamp/seal was detected")
    has_signature: bool = Field(False, description="Whether a signature was detected")
    has_watermark: bool = Field(False, description="Whether a watermark was detected")
    table_count: int = Field(0, ge=0, description="Number of tables detected")


class OCRResult(BaseModel):
    """Result of processing a single page through the OCR pipeline."""

    model_config = ConfigDict(frozen=True, slots=True)

    page: int = Field(ge=1, description="1-based page number")
    ocr_used: bool = Field(description="Whether OCR was required (False = direct text extraction)")
    confidence: float = Field(ge=0.0, le=1.0, default=0.0, description="Overall OCR confidence score")
    language: str = Field(default="english", description="Detected primary language")
    text: str = Field(default="", description="Extracted and cleaned text content")
    ocr_engine: str | None = Field(default=None, description="Which OCR engine was used (paddle/tesseract/none)")
    preprocessing_steps: list[str] = Field(default_factory=list, description="Image preprocessing steps applied")
    detection: PageDetectionResult = Field(
        default_factory=PageDetectionResult,
        description="Results of object detection on this page",
    )
    error: str | None = Field(default=None, description="Error message if processing failed")
