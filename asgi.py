"""FastAPI ASGI entry-point — coexists with Flask (FASTAPI_IMPLEMENTATION_PLAN.md, Ambition B).

Flask (the full UI + legacy RAG routes under ``/api/rag``) is mounted at the
root via ``WSGIMiddleware``.  FastAPI owns ``/api/v2/*`` natively:

* ``GET  /api/v2/health``        — standalone ASGI health probe (no Flask ctx)
* ``POST /api/v2/rag/generate``  — FastAPI-native, delegates to the resilient
  RAG pipeline (circuit-breaker + stub-LLM fallback, so no Qdrant required)
* ``POST /api/v2/rag/query/agent`` — FastAPI-native, delegates to the LangGraph
  agent (``run_agent``) when ``RAG_USE_AGENT_PIPELINE`` is set

All heavy lifting stays in the existing Flask services (`app.rag.*`); FastAPI
owns only the transport/HTTP layer for these endpoints.  ``run_generation_pipeline``
and its helpers already degrade gracefully when Qdrant/LLM are unavailable.

Deploy: replace gunicorn with ``uvicorn asgi:app`` in render.yaml start_command.
"""

from __future__ import annotations

import os
from typing import Any

from a2wsgi import WSGIMiddleware
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

# Flask app created via the existing factory — registered blueprints, routes,
# extensions, and RAG services are all available.
from app import create_app
from app.api.deps import get_db, get_flag, get_rag_pipeline, set_flask_app

flask_app = create_app()
# Hand the Flask app to the v2 dependency layer (dependency inversion —
# routers cannot import asgi). Handlers that touch Flask-managed singletons
# (db.session, log_audit) retrieve it via get_flask_app().
set_flask_app(flask_app)


# --------------------------------------------------------------------------- #
# Pydantic request models (Phase 2 schema — RAGResponse is dict-shaped, so the
# response is returned as a raw dict to avoid coupling ASGI to the model.)
# --------------------------------------------------------------------------- #
class GenerateRequest(BaseModel):
    """Request body for ``POST /api/v2/rag/generate`` and ``/api/v2/rag/retrieve``."""

    query: str = Field(..., min_length=1, description="The legal question to answer.")
    top_k: int = Field(default=10, ge=1, le=50, description="Chunks to retrieve.")
    collection_name: str | None = Field(default=None, description="Qdrant collection override.")
    filters: dict[str, Any] | None = Field(default=None, description="Metadata filters.")


class QueryAgentRequest(BaseModel):
    """Request body for ``POST /api/v2/rag/query/agent``."""

    query: str = Field(..., min_length=1, description="The legal question to answer.")
    top_k: int = Field(default=10, ge=1, le=50, description="Chunks to retrieve.")
    collection_name: str | None = Field(default=None, description="Qdrant collection override.")
    filters: dict[str, Any] | None = Field(default=None, description="Metadata filters.")
    thread_id: str | None = Field(default=None, description="Resume a paused HITL run (M5).")


class AgentResumeRequest(BaseModel):
    """Request body for ``POST /api/v2/rag/query/agent/resume`` (M5).

    ``approved`` is the human's decision on the reviewed answer — it is
    forwarded to ``resume_agent`` verbatim (a previous version hardcoded
    ``True``, silently defeating rejections).
    """

    thread_id: str = Field(default="", description="Thread id from the 202 awaiting_review response.")
    approved: bool = Field(default=True, description="Human decision: approve finalize, reject to retry.")


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="NSA Webservice ASGI Gateway",
    description=("FastAPI ASGI entry-point coexisting with Flask (FASTAPI_IMPLEMENTATION_PLAN.md, Ambition B)."),
    version="0.8.0-asgi.1",
    docs_url="/api/v2/docs",
    redoc_url=None,
    openapi_url="/api/v2/openapi.json",
)


def _resolve_origins() -> list[str]:
    """Build the CORS allow-list from ``RAG_QUERY_ORIGINS``.

    Defaults to ``["*"]`` when unset or ``"*"``.
    """
    raw = os.environ.get("RAG_QUERY_ORIGINS", "*")
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Security headers (Phase 4) — HSTS, X-Content-Type-Options, X-Frame-Options,
# Referrer-Policy.  Applied to /api/v2/* only (not the Flask-mounted UI, which
# has its own Talisman CSP via app/__init__.py).
# --------------------------------------------------------------------------- #
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security headers into all /api/v2/* responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v2"):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            if os.environ.get("RENDER") or os.environ.get("APP_ENV", "").lower() in ("production", "prod"):
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


app.add_middleware(SecurityHeadersMiddleware)


# --------------------------------------------------------------------------- #
# API-key auth (Phase 4) — simple header check for /api/v2/* routes.
# When ``API_V2_KEY`` is set, requests must include ``x-api-key`` header.
# When unset (dev/default), routes are open.
# --------------------------------------------------------------------------- #
class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Require ``x-api-key`` header on /api/v2/* when ``API_V2_KEY`` is set."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/v2") and _api_v2_key_enabled():
            provided = request.headers.get("x-api-key", "")
            expected = os.environ.get("API_V2_KEY", "")
            if not provided or provided != expected:
                return JSONResponse(
                    {"error": "Unauthorized: missing or invalid x-api-key header."},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
        return await call_next(request)


def _api_v2_key_enabled() -> bool:
    """True when API_V2_KEY is set (production gating)."""
    return bool(os.environ.get("API_V2_KEY", ""))


app.add_middleware(ApiKeyAuthMiddleware)


# NOTE: RAG service wiring (get_db, get_flag, get_rag_pipeline) lives in
# app/api/deps.py — shared across all /api/v2/* routes.
from app.api.routers import router as v2_router

app.include_router(v2_router)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/v2/health")
async def v2_health() -> dict[str, Any]:
    """Standalone ASGI health probe — no Flask app context needed."""
    return {"status": "ok", "fastapi": True}


@app.post("/api/v2/rag/generate")
async def v2_rag_generate(req: GenerateRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Grounded RAG generation (FastAPI-native, delegates to the resilient pipeline).

    Mirrors ``POST /api/rag/generate`` but owns the transport layer.  The
    circuit breaker ensures a 200 response even when Qdrant/LLM is down.
    ``db`` is available for audit-log writes when the pipeline is extended.
    """
    pipeline = get_rag_pipeline()
    result = pipeline.run(
        query=req.query,
        top_k=req.top_k,
        collection_name=req.collection_name,
        filters=req.filters or {},
    )
    return result


@app.post("/api/v2/rag/retrieve", response_model=None)
async def v2_rag_retrieve(req: GenerateRequest) -> dict[str, Any] | JSONResponse:
    """Live hybrid retrieval — dense (Qdrant + remote embeddings, RAG_REMOTE_EMBED)
    + sparse (Qdrant BM25 / rapidfuzz), fused via RRF (k=60).

    No LLM call; returns raw ``SearchResult`` chunks.  Graceful 503 when
    RAG is disabled, so the endpoint is safe in test/CI without Qdrant.
    """
    from app.rag.retrieval.dense_retriever import DenseRetriever
    from app.rag.retrieval.hybrid_retriever import HybridRetriever
    from app.rag.retrieval.sparse_retriever import SparseRetriever

    if not get_flag("RAG_ENABLED"):
        return JSONResponse({"error": "RAG is disabled."}, status_code=503)

    dense = DenseRetriever(collection_name=req.collection_name or "fssai_legal_768")
    sparse = SparseRetriever(store=dense._client) if dense._client else SparseRetriever()
    retriever = HybridRetriever(dense=dense, sparse=sparse)
    try:
        result = retriever.retrieve(
            query=req.query,
            top_k=req.top_k,
            filters=req.filters or {},
        )
    except Exception as exc:
        return JSONResponse({"error": f"Retrieval failed: {exc}"}, status_code=502)

    return _result_to_dict(result)


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Serialize a SearchResult into a JSON-safe dict (SearchResult has no to_dict)."""
    from dataclasses import asdict

    return {
        "query": result.query,
        "query_type": result.query_type,
        "total": result.total,
        "source": result.source,
        "latency_ms": result.latency_ms,
        "error": result.error,
        "chunks": [asdict(c) if hasattr(c, "__dataclass_fields__") else c.to_dict() for c in result.chunks],
    }


@app.post("/api/v2/rag/query/agent", response_model=None)
async def v2_rag_query_agent(req: QueryAgentRequest) -> dict[str, Any] | JSONResponse:
    """Full RAG pipeline as a LangGraph agent (M3, M5).

    Delegates to ``app.rag.agent.graph.run_agent`` when ``RAG_USE_AGENT_PIPELINE``
    is true; otherwise mirrors the legacy ``GET /api/rag/query`` behaviour.
    """
    use_agent = get_flag("RAG_USE_AGENT_PIPELINE")
    use_hitl = get_flag("RAG_AGENT_HITL")

    if not use_agent:
        # Legacy path — run the resilient pipeline directly.
        pipeline = get_rag_pipeline()
        result = pipeline.run(
            query=req.query,
            top_k=req.top_k,
            collection_name=req.collection_name,
            filters=req.filters or {},
        )
        return result

    # Agent path — LangGraph import is lazy (app boots without langgraph).
    try:
        from app.rag.agent.graph import run_agent
        from app.rag.agent.state import initial_state

        state = initial_state(
            req.query,
            top_k=req.top_k,
            collection_name=req.collection_name,
            filters=req.filters or {},
        )
        result = run_agent(state, thread_id=req.thread_id, hitl=use_hitl)
    except ImportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception as exc:
        return JSONResponse({"error": f"RAG agent query failed: {exc}"}, status_code=500)

    if use_hitl and "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        review = interrupts[0].value if interrupts else {}
        return JSONResponse(
            {
                "status": "awaiting_review",
                "thread_id": req.thread_id,
                "review": review,
                "hint": "POST /api/v2/rag/query/agent/resume with {thread_id, approved}.",
            },
            status_code=202,
        )

    return result.get("response") or {}


@app.post("/api/v2/rag/query/agent/resume", response_model=None)
async def v2_rag_query_agent_resume(req: AgentResumeRequest) -> dict[str, Any] | JSONResponse:
    """Resume a paused M5 human-in-the-loop run (mirrors the Flask route)."""
    use_hitl = get_flag("RAG_AGENT_HITL")
    if not use_hitl:
        return JSONResponse(
            {"error": "RAG_AGENT_HITL is false — no review flow to resume."},
            status_code=400,
        )
    if not req.thread_id or not req.thread_id.strip():
        return JSONResponse({"error": "thread_id must be a non-empty string."}, status_code=400)

    try:
        from app.rag.agent.graph import resume_agent

        result = resume_agent(req.thread_id, approved=req.approved)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"RAG agent resume failed: {exc}"}, status_code=500)

    if "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        review = interrupts[0].value if interrupts else {}
        # The graph paused again (e.g. after a rejection-triggered retry that
        # still isn't grounded) — 202, matching the Flask route's contract.
        return JSONResponse(
            {
                "status": "awaiting_review",
                "thread_id": req.thread_id,
                "review": review,
            },
            status_code=202,
        )

    return result.get("response") or {}


# --------------------------------------------------------------------------- #
# Mount Flask at the root — all non-/api/v2 paths fall through to Flask.
# --------------------------------------------------------------------------- #
app.mount("/", WSGIMiddleware(flask_app.wsgi_app))  # type: ignore[arg-type]

# NOTE: FastAPI owns /api/v2/* natively above; Flask (mounted at /) serves
# the remainder including /api/rag/* and the full UI.


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "asgi:app", host="127.0.0.1", port=int(os.environ.get("PORT", 8000))
    )  # pi-lens-ignore: ast-grep:unchecked-throwing-call-python
