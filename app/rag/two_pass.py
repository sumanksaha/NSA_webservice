from __future__ import annotations

from typing import Any


class TwoPassReasoner:
    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        if self.llm_client is None:
            try:
                from app.shared.llm_client import get_llm_client

                self.llm_client = get_llm_client()
            except Exception:
                self.llm_client = None

    def reason(self, query: str, context: str, directive: str = "") -> Any:
        if not self.llm_client:
            return None
        try:
            from app.rag.generation.llm_client import GroundedLLMResponse

            draft_system = "You are a careful legal assistant. Provide a precise answer using only the provided context."
            draft_user = f"{directive}\nQuestion: {query}\n\nContext:\n{context}\n\nDraft your answer with citations if relevant."
            draft = self.llm_client.call(draft_system, draft_user)
            if not getattr(draft, "success", False):
                return draft
            critique_system = (
                "You are a strict reviewer. Critique the draft for unsupported claims, missing citations, and errors. "
                "Return an improved answer using only the context."
            )
            critique_user = (
                f"{directive}\nQuestion: {query}\n\nContext:\n{context}\n\nDraft:\n{draft.text}\n\n"
                "Revise and correct the answer, keeping only claims supported by the context."
            )
            revised = self.llm_client.call(critique_system, critique_user)
            if getattr(revised, "success", False) and (revised.text or "").strip():
                return revised
            return draft
        except Exception:
            return None


__all__ = ["TwoPassReasoner"]
