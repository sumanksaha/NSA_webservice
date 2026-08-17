"""Database models package.

All models are exported from their respective submodules for organisation.
The ``__init__.py`` re-exports every model class so that existing imports
like ``from app.models import User`` continue to work transparently.
"""

# flake8: noqa: F401
# ruff: noqa: F401

from app.models.auth import AirtableBaseMap, Comment, RecordAudit, Role, User, user_roles
from app.models.billing import Bill, BillSample, CodeSequence, Sample
from app.models.config import AppSecret, Settings
from app.models.document import (
    Adjudication,
    Annexure,
    CaseFile,
    Entity,
    Evidence,
    Relationship,
    TimelineEvent,
    Version,
)
from app.models.enrichment import (
    ChunkCrossReference,
    ChunkEnrichment,
    EnrichmentCheckpoint,
    ResourceUsage,
)
from app.models.food_cell import DoIntimation
from app.models.inspection import FSO, AuditLog, Inspection
from app.models.issue import FboIssue, FboIssueAudit
from app.models.ocr import (
    ConflictLog,
    FieldAuthority,
    LabTestParameter,
    OCRCorrection,
    OCRDocument,
)
from app.models.rag import LegalChunk, LegalDocument, RAGEvalDataset, RAGEvalResult, RAGQueryLog
