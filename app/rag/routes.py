"""Routes for the Legal RAG query interface."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.rag.agent.graph import route_after_verify
from app.rag.agent.state import RAGState

router = APIRouter(prefix="/rag", tags=["RAG"])


@router.get("/query", response_class=JSONResponse)
def query_legal(
    query: str = "",
    collection: str | None = None,
    api_key: str | None = None,
):
    """Legal RAG query endpoint.

    Builds a RAGState, runs the multi-hop agent pipeline, and returns
    the composed response.
    """
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    state: RAGState = {
        "query": query,
        "top_k": 10,
        "collection_name": collection,
        "query_type": "legal",
        "retry_count": 0,
        "max_retries": 3,
        "groundedness": 0.0,
        "hallucination_detected": False,
    }

    # Route through the multi-hop agent pipeline
    try:
        result = route_after_verify(state)
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent pipeline failed: {exc}") from exc


@router.post("/query", response_class=JSONResponse)
def submit_query(
    query: str = "",
    collection: str | None = None,
    api_key: str | None = None,
):
    """Alternative POST endpoint for bulk queries."""
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    state: RAGState = {
        "query": query,
        "top_k": 10,
        "collection_name": collection,
        "query_type": "legal",
        "retry_count": 0,
        "max_retries": 3,
        "groundedness": 0.0,
        "hallucination_detected": False,
    }

    try:
        result = route_after_verify(state)
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent pipeline failed: {exc}") from exc
