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
    q: str = Query(..., min_length=1, description="Search terms."),
    type: str | None = Query(default=None, description="Entity type filter."),
    limit: int = Query(default=20, ge=1, le=100, description="Max results."),
    fuzzy: bool = Query(default=False, description="Force fuzzy matching."),
) -> Any:
    """JSON search API — mirrors Flask ``search_bp.api_search``.

    Delegates to ``app.search.indexer.search`` (SQLite FTS5 + rapidfuzz
    fuzzy fallback).  No DB session needed (FTS5 is self-contained).
    """
    from app.search.indexer import ENTITY_TYPES, search

    if type and type not in ENTITY_TYPES:
        return JSONResponse({"error": f"Invalid entity type: {type}"}, status_code=400)

    results = search(q, entity_type=type, limit=limit, fuzzy=fuzzy)
    fuzzy_used = fuzzy or bool(results and "score" in results[0])

    return {
        "query": q,
        "total": len(results),
        "fuzzy": fuzzy_used,
        "results": results,
    }


@router.post("/search/reindex")
async def v2_search_reindex(request: Request) -> Any:
    """Manually trigger a full re-index of the FTS5 table (mirrors Flask route)."""
    if not get_flag("RAG_ENABLED"):
        return JSONResponse({"error": "Search reindex is disabled."}, status_code=503)
    try:
        from app.search.indexer import index_all as search_index_all
        from app.services.audit import log_audit

        count = search_index_all()
        actor = "system"
        log_audit(
            entity_type="search",
            entity_id="all",
            action="index_rebuilt",
            actor=actor,
            details={"records_indexed": count},
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
    content: str = Field(..., min_length=1, description="Text content to process.")
    context: dict[str, Any] | None = Field(
        default=None, description="Optional context for draft_prayers (facts/grounds)."
    )


_ACTION_METHODS: dict[str, str] = {
    "summarize": "summarize_text",
    "refine_legal": "refine_legal_language",
    "detect_contradictions": "detect_contradictions",
    "suggest_annexures": "suggest_missing_annexures",
    "draft_prayers": "draft_prayers",
}


@router.post("/ai-assistant/assist")
async def v2_ai_assistant_assist(req: AssistRequest) -> Any:
    """Dispatch an AI action and return the result (mirrors Flask ``ai_bp.assist``).

    Uses the Phase 20 plugin registry to resolve the active AI provider.
    Returns 503 when the AI service is not configured (no API key).
    """
    if req.action not in _ACTION_METHODS:
        return JSONResponse(
            {"error": (f"Invalid action. Must be one of: {', '.join(sorted(_ACTION_METHODS))}.")},
            status_code=400,
        )

    from app.plugins.registry import PluginRegistry

    service = PluginRegistry.get_instance().get_active("ai")
    if not service.is_enabled():
        return JSONResponse({"error": "AI Assistant is not configured."}, status_code=503)

    method_name = _ACTION_METHODS[req.action]
    method = getattr(service, method_name)
    context = req.context or {}
    result = method(req.content, **context)

    return {
        "result": result,
        "tokens_used": getattr(service, "tokens_used", 0),
        "action": req.action,
    }


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
    """Lookup open/permission_granted FBO issues for bill pre-fill (mirrors Flask route).

    Query params:
        fbo_id   — required (or issue_id)
        issue_id — optional, specific issue lookup

    Returns JSON array of issues with pre-fill data for bill generation.
    """
    from app.models.issue import FboIssue

    if not fbo_id and not issue_id:
        return JSONResponse({"error": "Either fbo_id or issue_id is required"}, status_code=400)

    query = db.query(FboIssue).filter(FboIssue.state.in_(["open", "permission_granted"]))

    if issue_id:
        query = query.filter(FboIssue.id == issue_id)
    elif fbo_id:
        query = query.filter(FboIssue.fbo_id == fbo_id)

    issues = query.order_by(FboIssue.created_at.desc()).all()

    result = []
    for issue in issues:
        detail = None
        if issue.detail_json:
            try:
                import json

                detail = json.loads(issue.detail_json)
            except Exception:
                detail = issue.detail_json

        item: dict[str, Any] = {
            "issue_id": issue.id,
            "fbo_id": issue.fbo_id,
            "manufacturer_fbo_id": issue.manufacturer_fbo_id,
            "fbo_name": issue.fbo_name,
            "source_type": issue.source_type,
            "state": issue.state,
            "fso_name": issue.fso_name,
            "created_at": issue.created_at,
            "detail": detail,
        }

        if issue.source_type == "sample" and detail:
            item["prefill"] = {
                "Name": issue.fbo_name,
                "EMP_ID": issue.fso_name,
            }
            if issue.manufacturer_fbo_id:
                item["prefill"]["manufacturer_fbo_id"] = issue.manufacturer_fbo_id
        elif issue.source_type == "inspection" and detail:
            item["prefill"] = {
                "Name": issue.fbo_name,
                "EMP_ID": issue.fso_name,
            }

        result.append(item)

    return result


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
