"""Grounded generation service — orchestrates the Phase 2 pipeline.

``GroundedGenerationService`` coordinates the multi-step process of
turning retrieved chunks + a user query into a grounded, cited, and
validated LLM response.  It follows the service-layer orchestration
pattern from ``app/ai_assistant/service.py`` and
``app/food_cell/services.py``: dependencies are injected, configuration
is lazy, and failures in any stage degrade gracefully.

Pipeline:
    1. Build context        — ContextBuilder
    2. Render prompt         — PromptTemplate
    3. Call LLM              — GroundedLLMClient
    4. Extract citations     — CitationTracker
    5. Sanitise / validate   — ResponseSanitizer
    6. Log generation        — GenerationLogger (best-effort)
    7. Assemble RAGResponse  — result.RAGResponse
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.rag.generation.citation_tracker import CitationTracker
from app.rag.generation.context_builder import BuiltContext, ContextBuilder
from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse
from app.rag.generation.logger import GenerationLogger
from app.rag.generation.prompt_template import PromptTemplate
from app.rag.generation.sanitizer import ResponseSanitizer, SanitizedResponse
from app.rag.retrieval.result import RAGResponse, RetrievedChunk
from app.rag.verification.token_counter import TokenCounter

logger = logging.getLogger(__name__)


class GroundedGenerationService:
    """Orchestrates grounded RAG generation.

    Args:
        llm_client: The LLM client (stub or real).  A default
            :class:`GroundedLLMClient` is created if not provided.
        context_builder: Context builder.  Defaults to a
            :class:`ContextBuilder` with standard limits.
        prompt_template: Prompt template renderer.  Defaults to a
            :class:`PromptTemplate`.
        citation_tracker: Citation tracker.  Defaults to a new instance.
        sanitizer: Response sanitiser.  Defaults to a new instance.
        generation_logger: Generation logger.  Defaults to a new instance.
    """

    def __init__(
        self,
        llm_client: GroundedLLMClient | None = None,
        context_builder: ContextBuilder | None = None,
        prompt_template: PromptTemplate | None = None,
        citation_tracker: CitationTracker | None = None,
        sanitizer: ResponseSanitizer | None = None,
        generation_logger: GenerationLogger | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.llm_client = llm_client or GroundedLLMClient()
        self.context_builder = context_builder or ContextBuilder()
        self.prompt_template = prompt_template or PromptTemplate()
        self.citation_tracker = citation_tracker or CitationTracker()
        self.sanitizer = sanitizer or ResponseSanitizer()
        self.generation_logger = generation_logger or GenerationLogger()
        self.token_counter = token_counter or TokenCounter()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def generate(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
        query_log_id: str | None = None,
    ) -> RAGResponse:
        """Run the full grounded-generation pipeline.

        Args:
            query: The user's question.
            chunks: Retrieved chunks from the hybrid retriever.
            query_type: Classified query type (for logging).
            query_log_id: Optional ``RAGQueryLog`` UUID to update with
                generation metrics.

        Returns:
            A :class:`RAGResponse` with answer, citations, and groundedness.
        """
        total_start = time.perf_counter()

        # Early exit — no chunks means no context, no LLM call needed.
        if not chunks:
            total_latency_ms = int((time.perf_counter() - total_start) * 1000)
            return RAGResponse(
                query=query,
                query_type=query_type,
                answer="",
                citations=[],
                retrieved_chunks=chunks,
                groundedness_score=0.0,
                hallucination_detected=False,
                hallucinated_claims=[],
                confidence=0.0,
                generation_latency_ms=0,
                total_latency_ms=total_latency_ms,
                llm_model="",
                debug={
                    "context_length": 0,
                    "chunk_count": 0,
                    "truncated": False,
                    "empty_context": True,
                },
            )

        # 1. Build context
        built = self._build_context(query, chunks, query_type)

        # 2. Render prompt
        system_prompt, user_prompt = self._render_prompt(query, built)

        # 3. Call LLM
        llm_response = self._call_llm(system_prompt, user_prompt)

        # 4. Extract citations
        citations = self._extract_citations(llm_response, chunks, built)

        # 5. Sanitise
        sanitized = self.sanitizer.sanitize(llm_response.text, citations, chunks)

        # 6. Log generation (best-effort)
        total_latency_ms = int((time.perf_counter() - total_start) * 1000)
        self._log_generation(
            query_log_id, query, llm_response, sanitized, total_latency_ms, built
        )

        # 7. Assemble response
        return self._assemble_response(
            query, query_type, chunks, llm_response, sanitized, total_latency_ms, built
        )

    # ------------------------------------------------------------------ #
    # Pipeline steps (each isolated for testability)
    # ------------------------------------------------------------------ #

    def _build_context(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str,
    ) -> BuiltContext:
        try:
            return self.context_builder.build(query, chunks, query_type)
        except Exception as exc:
            logger.warning("ContextBuilder failed: %s", exc)
            return BuiltContext(
                context="", citations=[], chunk_count=0, truncated=True
            )

    def _render_prompt(
        self, query: str, built: BuiltContext
    ) -> tuple[str, str]:
        try:
            return self.prompt_template.render_default(query, built.context)
        except Exception as exc:
            logger.warning("PromptTemplate failed: %s", exc)
            system_prompt = "Answer the following question."
            user_prompt = f"Question: {query}"
            return system_prompt, user_prompt

    def _call_llm(self, system_prompt: str, user_prompt: str) -> GroundedLLMResponse:
        try:
            return self.llm_client.call(system_prompt, user_prompt)
        except Exception as exc:
            logger.warning("LLM client call failed: %s", exc)
            return GroundedLLMResponse(error=str(exc))

    def _extract_citations(
        self,
        llm_response: GroundedLLMResponse,
        chunks: list[RetrievedChunk],
        built: BuiltContext,
    ) -> list:
        """Extract citations from the LLM response.

        Builds a ``{index: chunk}`` map from the ``BuiltContext.citations``
        list so that ``[1]`` maps to the first chunk in the assembled
        context (which may differ from the original chunk ordering).
        """
        if not llm_response.success:
            return []

        chunk_by_id = {c.chunk_id: c for c in chunks}
        citation_map: dict[int, RetrievedChunk] = {}
        for cit in built.citations:
            chunk = chunk_by_id.get(cit["chunk_id"])
            if chunk is not None:
                citation_map[cit["index"]] = chunk

        try:
            return self.citation_tracker.extract(llm_response.text, chunks, citation_map)
        except Exception as exc:
            logger.warning("CitationTracker failed: %s", exc)
            return []

    def _log_generation(
        self,
        query_log_id: str | None,
        query: str,
        llm_response: GroundedLLMResponse,
        sanitized: SanitizedResponse,
        total_latency_ms: int,
        built: BuiltContext,
    ) -> None:
        if not query_log_id:
            return
        try:
            # Estimate real token counts (LLM client may be in stub mode).
            full_prompt = built.context
            token_est = self.token_counter.estimate_usage(
                context=full_prompt, response=llm_response.text or ""
            )
            self.generation_logger.log_generation(
                query_log_id,
                query=query,
                response_text=llm_response.text,
                cited_chunk_ids=[c.chunk_id for c in sanitized.valid_citations],
                groundedness_score=sanitized.groundedness_score,
                hallucination_detected=sanitized.hallucination_detected,
                hallucinated_claims=sanitized.hallucinated_claims,
                total_latency_ms=total_latency_ms,
                prompt_tokens=llm_response.usage.get("prompt_tokens"),
                completion_tokens=llm_response.usage.get("completion_tokens"),
                llm_model=llm_response.model,
                error=llm_response.error,
                context_length=token_est.context_length,
                token_counter_result=token_est.to_dict(),
            )
        except Exception as exc:
            logger.warning("GenerationLogger.log_generation failed: %s", exc)

    def _assemble_response(
        self,
        query: str,
        query_type: str,
        chunks: list[RetrievedChunk],
        llm_response: GroundedLLMResponse,
        sanitized: SanitizedResponse,
        total_latency_ms: int,
        built: BuiltContext,
    ) -> RAGResponse:
        """Build the final :class:`RAGResponse`."""
        usage = llm_response.usage
        gen_latency_ms = int(llm_response.latency * 1000) if llm_response.success else 0
        # Use TokenCounter for real context length (LLM client stub returns
        # placeholder token counts).
        real_context_tokens = self.token_counter.estimate(built.context) if built.context else 0

        return RAGResponse(
            query=query,
            query_type=query_type,
            answer=llm_response.text if llm_response.success else "",
            citations=sanitized.valid_citations,
            retrieved_chunks=chunks,
            groundedness_score=sanitized.groundedness_score,
            hallucination_detected=sanitized.hallucination_detected,
            hallucinated_claims=sanitized.hallucinated_claims,
            confidence=sanitized.confidence,
            generation_latency_ms=gen_latency_ms,
            total_latency_ms=total_latency_ms,
            llm_model=llm_response.model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            token_usage={
                "prompt": usage.get("prompt_tokens", 0) or real_context_tokens,
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0) or (real_context_tokens + usage.get("completion_tokens", 0)),
            },
            debug={
                "context_length": real_context_tokens,
                "chunk_count": built.chunk_count,
                "truncated": built.truncated,
                "invalid_citation_count": len(sanitized.invalid_citations),
                "error": llm_response.error,
            },
        )
