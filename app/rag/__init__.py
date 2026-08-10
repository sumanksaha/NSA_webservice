"""RAG blueprint for the FSSAI Legal RAG system.

Phase 1 (Retrieval Foundation) creates the blueprint and the Celery task.
Phase 5 (Integration) adds the ingestion API endpoints via
``app/rag/routes.py`` (imported below so route decorators register — same
pattern as ``app/ai_assistant/__init__.py``).

The blueprint is registered in ``app/__init__.py::create_app()`` so health /
ingestion endpoints are reachable without extra wiring.
"""

from flask import Blueprint

rag_bp = Blueprint(
    "rag",
    __name__,
    url_prefix="/api/rag",
)

# Import routes after the blueprint is defined so route decorators register
# (same pattern as app/ai_assistant/__init__.py).
from app.rag import routes  # noqa: F401, E402
