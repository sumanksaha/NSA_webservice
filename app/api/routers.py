"""FastAPI routers for ported Core-API endpoints (FASTAPI_IMPLEMENTATION_PLAN.md, Phase 3).

These routes mirror existing Flask endpoints under `/api/v2/*` so they can be
tested and cut-over independently.  All business logic delegates to the
existing service layer — FastAPI owns only the transport/HTTP layer.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_flag

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["v2"])


# --------------------------------------------------------------------------- #
# /api/v2/search — mirror of Flask's search.api_search
# --------------------------------------------------------------------------- #
class SearchResultItem(BaseModel):
    """One search result row (shape mirrors Flask search_indexer output)."""

    id: str | None = None
    title: str | None = None
    snippet: str | None = None
    score: float | None = None
    type: str | None = None

    model_config = {"extra": "allow"}


class SearchResponse(BaseModel):
    query: str
    total: int
    fuzzy: bool
    results: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/search", response_model=SearchResponse)
async def v2_search(
    q: str = Query(default="", description="Search terms (stripped; empty returns no results)."),
    type: str | None = Query(default=None, description="Entity type filter."),
    limit: int = Query(default=20, description="Max results (capped at 100 like the Flask route)."),
    fuzzy: bool = Query(default=False, description="Force fuzzy matching."),
) -> Any:
    """JSON search API — mirrors Flask ``search_bp.api_search``.

    Delegates to ``app.search.indexer.search`` (SQLite FTS5 + rapidfuzz
    fuzzy fallback).  No DB session needed (FTS5 is self-contained).
    Parameter handling mirrors the Flask route: ``q`` is stripped (empty →
    empty result set, not an error) and ``limit`` is capped at 100.
    """
    from app.search.indexer import ENTITY_TYPES, search

    q = q.strip()
    if type and type not in ENTITY_TYPES:
        return JSONResponse({"error": f"Invalid entity type: {type}"}, status_code=400)

    limit = min(int(limit), 100)
    results = search(q, entity_type=type, limit=limit, fuzzy=fuzzy)
    fuzzy_used = fuzzy or bool(results and "score" in results[0])

    return {
        "results": results,
        "query": q,
        "total": len(results),
        "fuzzy": fuzzy_used,
    }


@router.post("/search/reindex")
async def v2_search_reindex(request: Request) -> Any:
    """Manually trigger a full re-index of the FTS5 table (mirrors Flask route).

    No feature gate — FTS5 reindexing is a search concern, not RAG. The audit
    actor is ``"system"`` here (no Flask session on this transport); the
    dialect detail matches the Flask audit record via the shared helper.
    """
    try:
        # ``index_all`` is context-free (FTS5), but ``log_audit`` writes through
        # Flask-SQLAlchemy's session — that requires a Flask app context, which
        # no ASGI request ever has. Without this wrapper the audit write raises
        # ("Working outside of application context") and the endpoint 500s in
        # production, not just in tests.
        from app.api.deps import get_flask_app

        with get_flask_app().app_context():
            from app.search.indexer import dialect_name
            from app.search.indexer import index_all as search_index_all
            from app.services.audit import log_audit

            count = search_index_all()
            log_audit(
                entity_type="search",
                entity_id="all",
                action="index_rebuilt",
                actor="system",
                details={"records_indexed": count, "dialect": dialect_name()},
            )
        return {"status": "ok", "records_indexed": count}
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


# --------------------------------------------------------------------------- #
# /api/v2/ai-assistant/assist — mirror of Flask's ai_assistant.assist
# --------------------------------------------------------------------------- #
class AssistRequest(BaseModel):
    action: str = Field(
        ..., description="One of: summarize, refine_legal, detect_contradictions, suggest_annexures, draft_prayers."
    )
    content: str = Field(default="", description="Text content to process (validated by the domain function).")
    context: dict[str, Any] | None = Field(
        default=None, description="Optional context for draft_prayers (facts/grounds)."
    )


@router.post("/ai-assistant/assist")
async def v2_ai_assistant_assist(req: AssistRequest) -> Any:
    """Dispatch an AI action and return the result (mirrors Flask ``ai_bp.assist``).

    Delegates to ``app.ai_assistant.service.dispatch_ai_action`` — the same
    domain function the Flask route uses, so calling conventions (notably
    ``draft_prayers(facts, grounds)``) and list serialization cannot drift.
    Returns 503 when the AI service is not configured (no API key).
    """
    from app.ai_assistant.service import dispatch_ai_action
    from app.plugins.registry import PluginRegistry

    service = PluginRegistry.get_instance().get_active("ai")

    try:
        return dispatch_ai_action(service, req.action, req.content, req.context or {})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        if "not configured" in str(exc):
            return JSONResponse({"error": "AI Assistant is not configured."}, status_code=503)
        logger.error("AI action '%s' failed: %s", req.action, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    except Exception as exc:
        logger.error("AI action '%s' raised unexpected error: %s", req.action, exc)
        return JSONResponse({"error": "AI request failed."}, status_code=500)


# --------------------------------------------------------------------------- #
# /api/v2/bill/lookup-fbo-issues — mirror of Flask bill_generator.lookup_fbo_issues
# --------------------------------------------------------------------------- #
class BillLookupResponse(BaseModel):
    issue_id: int
    fbo_id: str
    manufacturer_fbo_id: str | None = None
    fbo_name: str
    source_type: str
    state: str
    fso_name: str
    created_at: Any
    detail: dict[str, Any] | str | None = None
    prefill: dict[str, Any] | None = None


@router.get("/bill/lookup-fbo-issues", response_model=list[BillLookupResponse])
async def v2_bill_lookup_fbo_issues(
    fbo_id: str | None = Query(default=None, description="FBO ID to look up."),
    issue_id: int | None = Query(default=None, description="Specific issue ID."),
    db: Session = Depends(get_db),
) -> Any:
    """Lookup open/permission_granted FBO issues for bill pre-fill.

    Thin adapter over ``app.bill_generator.lookup.lookup_fbo_issues`` — the
    same domain function the Flask route uses, so the prefill schema (sample /
    inspection / generic branches with full billing-form fields) cannot drift.
    """
    if not fbo_id and not issue_id:
        return JSONResponse({"error": "Either fbo_id or issue_id is required"}, status_code=400)

    from app.bill_generator.lookup import lookup_fbo_issues

    return lookup_fbo_issues(db, fbo_id=fbo_id, issue_id=issue_id)


# --------------------------------------------------------------------------- #
# /api/v2/rag/eval — mirror of Flask rag.eval_batch (Phase 4 batch eval)
# --------------------------------------------------------------------------- #
class EvalRequest(BaseModel):
    dataset: list[dict[str, Any]] = Field(..., min_length=1)
    eval_run_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)


@router.post("/rag/eval")
async def v2_rag_eval(req: EvalRequest) -> Any:
    """Batch evaluation endpoint (mirrors Flask /api/rag/eval).

    Delegates to ``app.rag.tasks.run_evaluate`` with the same dataset schema.
    """
    if not get_flag("RAG_ENABLED"):
        return JSONResponse({"error": "RAG is disabled."}, status_code=503)

    try:
        from app.rag.tasks import run_evaluate

        result = run_evaluate(
            dataset=req.dataset,
            eval_run_id=req.eval_run_id,
            top_k=req.top_k,
        )
    except Exception as exc:
        logger.error("RAG eval failed: %s", exc)
        return JSONResponse({"error": f"Evaluation failed: {exc}"}, status_code=500)

    return result


# --------------------------------------------------------------------------- #
# /api/v2/validation/validate — mirror of Flask validation.validate
# --------------------------------------------------------------------------- #
class ValidationRequest(BaseModel):
    case_id: int = Field(..., description="Case ID to validate.")
    case_type: str = Field(..., description="Must be 'case_file' or 'adjudication'.")


@router.post("/validation/validate")
async def v2_validation_validate(req: ValidationRequest) -> Any:
    """Run the legal validation engine (mirrors Flask /api/validation/validate).

    Delegates to ``app.validation.engine.ValidationEngine.validate_case``.
    """
    valid_types = ("case_file", "adjudication")
    if req.case_type not in valid_types:
        return JSONResponse(
            {"error": ("case_id (int) and case_type ('case_file' | 'adjudication') are required.")},
            status_code=400,
        )

    from app.validation.engine import ValidationEngine

    engine = ValidationEngine()
    result = engine.validate_case(req.case_id, req.case_type)
    if "error" in result:
        return JSONResponse(result, status_code=404)
    return result


# --------------------------------------------------------------------------- #
# /api/v2/rag/ingest — mirror of Flask rag.ingest (single document)
# --------------------------------------------------------------------------- #
class IngestRequest(BaseModel):
    text: str | None = Field(default=None, description="Raw text to ingest.")
    source: str | None = Field(
        default=None, description="Corpus file path to ingest (pdf/docx/txt). source takes precedence over text."
    )
    document: dict[str, Any] | None = Field(
        default=None, description="Caller-provided metadata (wins over extracted values)."
    )
    full_enrichment: bool | None = Field(default=None, description="Override RAG_FULL_ENRICHMENT for this request.")


@router.post("/rag/ingest")
async def v2_rag_ingest(req: IngestRequest) -> Any:
    """Ingest a single legal document (mirrors Flask /api/rag/ingest).

    ``source`` (file path) takes precedence over ``text``.  Delegates to
    ``app.rag.ingestion.make_ingestion_pipeline`` + ``run_ingest_document``.
    """
    if not get_flag("RAG_ENABLED"):
        return JSONResponse({"error": "RAG is disabled."}, status_code=503)

    if not req.text and not req.source:
        return JSONResponse({"error": "Provide either 'text' or 'source'."}, status_code=400)

    document = req.document or {}
    try:
        from app.rag.ingestion import make_ingestion_pipeline, run_ingest_document

        pipeline = make_ingestion_pipeline(full_enrichment=req.full_enrichment)
        result = run_ingest_document(req.source or req.text, document=document, pipeline=pipeline)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.error("RAG ingest failed: %s", exc)
        return JSONResponse({"error": f"Ingestion failed: {exc}"}, status_code=500)
    return result


# --------------------------------------------------------------------------- #
# /api/v2/rag/ingest/corpus — mirror of Flask rag.ingest_corpus
# --------------------------------------------------------------------------- #
class IngestCorpusRequest(BaseModel):
    corpus_dir: str = Field(..., min_length=1, description="Directory to scan for pdf/docx/txt files.")
    document: dict[str, Any] | None = Field(default=None, description="Caller-provided metadata applied to all files.")
    full_enrichment: bool | None = Field(default=None, description="Override RAG_FULL_ENRICHMENT for this request.")


@router.post("/rag/ingest/corpus")
async def v2_rag_ingest_corpus(req: IngestCorpusRequest) -> Any:
    """Ingest every supported file under a corpus directory (mirrors Flask route).

    Delegates to ``app.rag.ingestion.ingest_corpus_dir``.
    """
    if not get_flag("RAG_ENABLED"):
        return JSONResponse({"error": "RAG is disabled."}, status_code=503)

    document = req.document or {}
    try:
        from app.rag.ingestion import ingest_corpus_dir, make_ingestion_pipeline

        pipeline = make_ingestion_pipeline(full_enrichment=req.full_enrichment)
        summary = ingest_corpus_dir(req.corpus_dir, document=document, pipeline=pipeline)
    except Exception as exc:
        logger.error("RAG corpus ingest failed: %s", exc)
        return JSONResponse({"error": f"Corpus ingestion failed: {exc}"}, status_code=500)
    return summary
