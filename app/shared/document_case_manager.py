from __future__ import annotations

from flask import Blueprint

from app.shared import document_generation as generation
from app.shared import document_lookup as lookup
from app.shared import document_reports as reports
from app.shared.document_routes import register_document_routes

import logging
logger = logging.getLogger(__name__)

class DocumentCaseManager:
    """Document case management — shared abstraction for CaseFile and Adjudication.

    Deleted in favor of direct helper calls. Kept for backward compatibility.
    """
    pass
