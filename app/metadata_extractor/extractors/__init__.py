"""Individual field extractors for the Legal Metadata Extraction Engine.

Each extractor is a class that implements ``extract(text: str) -> list[tuple]``
returning a list of ``(value, confidence, method, detail)`` tuples.
"""

from app.metadata_extractor.extractors.base import BaseExtractor

__all__ = [
    "BaseExtractor",
]
