"""LangGraph agent pipeline (M3 + M4).

The agent package wraps the existing RAG services (retrieval, generation,
verification) into a self-correcting LangGraph: classify → retrieve →
generate → verify, with a conditional expand-and-retry loop when the
response is not grounded enough.

``langgraph`` is imported only inside :mod:`app.rag.agent.graph` — the
legacy pipeline and the rest of the app never import it (plan §5.1).
"""

from app.rag.agent.state import RAGState, initial_state

__all__ = ["RAGState", "initial_state"]
