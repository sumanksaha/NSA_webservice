"""
Legal Metadata Extraction Engine — hybrid regex + NER extractor for
Indian legal documents.  Supports Acts, Rules, Regulations, Notifications,
Gazettes, Bills, and Judgments.

Usage::

    from app.metadata_extractor import LegalMetadataEngine

    engine = LegalMetadataEngine()
    text = open("food_safety_act.txt").read()
    result = engine.extract(text)
    print(result.model_dump_json(indent=2))
"""

from app.metadata_extractor.engine import LegalMetadataEngine
from app.metadata_extractor.models import FieldConfidence, LegalMetadata

__version__ = "0.1.0"

__all__ = [
    "FieldConfidence",
    "LegalMetadata",
    "LegalMetadataEngine",
]
