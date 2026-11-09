"""Grounded generation service — backward-compatible thin adapter.

``GroundedGenerationService`` remains the public class callers and tests
import, but it now delegates the full pipeline to :class:`GenerationModule`
in :mod:`app.rag.generation.generation`.  The constructor still accepts the
seven injected dependencies (for testability), and all private pipeline
methods still exist so existing spies and monkeypatches keep working.

The deep logic lives in ``GenerationModule`` behind a single seam::

    generate(query, chunks, query_type="", query_log_id=None,
             evidence_set=None, prompts=None) -> RAGResponse
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.rag.generation.citation_tracker import CitationTracker
from app.rag.generation.context_builder import BuiltContext, ContextBuilder
from app.rag.generation.generation import GenerationModule
from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse
from app.rag.generation.logger import GenerationLogger
from app.rag.generation.prompt_template import PromptTemplate
from app.rag.generation.sanitizer import ResponseSanitizer, SanitizedResponse
from app.rag.retrieval.result import RAGResponse, RetrievedChunk
from app.rag.verification.token_counter import TokenCounter
from app.shared.config import cfg

logger = logging.getLogger(__name__)


def _abstention_threshold(qt: str) -> float:
    try:
        by_type = getattr(cfg, "abstention_threshold_by_type", None) or {}
        if qt and qt.lower() in by_type:
            return float(by_type[qt.lower()])
        return float(getattr(cfg, "abstention_threshold", 0.3))
    except Exception:
        return 0.3


class GroundedGenerationService:
    """Thin adapter over :class:`GenerationModule`.

    Constructor signature is unchanged so existing callers and tests keep
    working.  All pipeline behaviour is delegated to an internal
    ``GenerationModule``; injected dependencies are forwarded to it.
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
        self._module = GenerationModule(
            llm_client=llm_client,
            context_builder=context_builder,
            prompt_template=prompt_template,
            citation_tracker=citation_tracker,
            sanitizer=sanitizer,
            generation_logger=generation_logger,
            token_counter=token_counter,
        )
        # Expose for tests that reach into internals (spy targets, etc.)
        self.llm_client = self._module.llm_client
        self.context_builder = self._module.context_builder
        self.prompt_template = self._module.prompt_template
        self.citation_tracker = self._module.citation_tracker
        self.sanitizer = self._module.sanitizer
        self.generation_logger = self._module.generation_logger
        self.token_counter = self._module.token_counter

    # ------------------------------------------------------------------ #
    # Public API — delegated to GenerationModule
    # ------------------------------------------------------------------ #

    def generate(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
        query_log_id: str | None = None,
        evidence_set: dict[str, Any] | None = None,
    ) -> RAGResponse:
        """Run the full grounded-generation pipeline (orchestrates via self.*)."""
        total_start = time.perf_counter()

        if not chunks:
            total_latency_ms = int((time.perf_counter() - total_start) * 1000)
            return self._module._empty_response(query, query_type, total_latency_ms)

        built = self._build_context(query, chunks, query_type, evidence_set)
        reasoning = self._reason(query, built, query_type=query_type) if getattr(cfg, "structured_reasoner", False) else None

        contract = None
        contract_check = None
        if getattr(cfg, "answer_contract", False):
            try:
                resolver = self._module.resolver
                contract = resolver.build_contract(query, chunks, query_type)
            except Exception as exc:
                logger.debug("answer contract build failed: %s", exc)

        # Strategy 3: skip LLM generation when the contract marks the question
        # as unanswerable (named act+section absent from retrieved chunks).
        # Returns an abstention response without consuming an LLM call.
        if contract is not None and not contract.should_answer:
            total_latency_ms = int((time.perf_counter() - total_start) * 1000)
            return self._module._empty_response(query, query_type, total_latency_ms)

        system_prompt, user_prompt = self._render_prompt(query, built, reasoning=reasoning, query_type=query_type)
        if contract is not None and getattr(cfg, "answer_contract_repair", False):
            try:
                system_prompt, user_prompt = resolver.repair_prompt(contract, system_prompt, user_prompt)
            except Exception as exc:
                logger.debug("answer contract repair prompt failed: %s", exc)

        llm_response = self._call_llm(system_prompt, user_prompt)

        if contract is not None and getattr(cfg, "answer_contract_repair", False) and llm_response.success:
            try:
                contract_check = resolver.check_answer(llm_response.text or "", contract, chunks)
                if not contract_check.passed:
                    try:
                        rs, ru = self._render_prompt(query, built, reasoning=reasoning, query_type=query_type)
                        rs2, ru2 = resolver.repair_prompt(contract, rs, ru)
                        llm_response2 = self._call_llm(rs2, ru2)
                        if llm_response2.success and (llm_response2.text or ""):
                            llm_response = llm_response2
                            contract_check = resolver.check_answer(
                                llm_response.text or "", contract, chunks
                            )
                    except Exception as exc:
                        logger.debug("answer contract repair retry failed: %s", exc)
            except Exception as exc:
                logger.debug("answer contract check failed: %s", exc)
        elif contract is None:
            try:
                from app.answer_contract import verify_citations

                check = verify_citations(llm_response.text or "", [c["index"] for c in built.citations])
                if (not check.get("valid", True)) and llm_response.success:
                    try:
                        rs, ru = self._render_prompt(query, built, reasoning=reasoning, query_type=query_type)
                        rs2 = rs + "\nOnly use citations that appear in the provided context. Include only valid [n] citations present in the context."
                        llm_response2 = self._call_llm(rs2, ru)
                        if llm_response2.success and (llm_response2.text or ""):
                            check2 = verify_citations(llm_response2.text or "", [c["index"] for c in built.citations])
                            if check2.get("valid", True):
                                llm_response = llm_response2
                    except Exception as exc:
                        logger.debug("contract verification retry failed: %s", exc)
            except Exception as exc:
                logger.debug("contract verification skipped: %s", exc)

        citations = self._extract_citations(llm_response, chunks, built)
        sanitized = self.sanitizer.sanitize(llm_response.text, citations, chunks)

        total_latency_ms = int((time.perf_counter() - total_start) * 1000)
        self._log_generation(query_log_id, query, llm_response, sanitized, total_latency_ms, built)

        resp = self._assemble_response(
            query, query_type, chunks, llm_response, sanitized, total_latency_ms, built, reasoning=reasoning
        )
        if contract_check is not None:
            try:
                resp.debug = dict(resp.debug or {})
                resp.debug["answer_contract"] = {
                    "passed": contract_check.passed,
                    "false_abstention": contract_check.false_abstention,
                    "provision_mismatch": contract_check.provision_mismatch,
                    "citation_mismatch": contract_check.citation_mismatch,
                    "citation_gate_failed": contract_check.citation_gate_failed,
                    "violations": [
                        {"kind": v.kind, "severity": v.severity, "message": v.message}
                        for v in contract_check.violations
                    ],
                }
            except Exception:
                pass
        return resp

    def generate_with_prompts(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: str = "",
        prompts: tuple[str, str] | None = None,
        query_log_id: str | None = None,
    ) -> RAGResponse:
        """Backward-compatible wrapper: folds prompts into ``generate()``."""
        total_start = time.perf_counter()

        if not chunks:
            total_latency_ms = int((time.perf_counter() - total_start) * 1000)
            return self._module._empty_response(query, query_type, total_latency_ms)

        built = self._build_context(query, chunks, query_type)
        if prompts and prompts[0] and prompts[1]:
            system_prompt, user_prompt = prompts
        else:
            system_prompt, user_prompt = self._render_prompt(query, built)

        llm_response = self._call_llm(system_prompt, user_prompt)
        citations = self._extract_citations(llm_response, chunks, built)
        sanitized = self.sanitizer.sanitize(llm_response.text, citations, chunks)
        total_latency_ms = int((time.perf_counter() - total_start) * 1000)
        self._log_generation(query_log_id, query, llm_response, sanitized, total_latency_ms, built)
        return self._assemble_response(query, query_type, chunks, llm_response, sanitized, total_latency_ms, built)

    # ------------------------------------------------------------------ #
    # Pipeline steps — delegated (kept as spy targets for existing tests)
    # ------------------------------------------------------------------ #

    def _build_context(self, query, chunks, query_type, evidence_set=None):
        return self._module._build_context(query, chunks, query_type, evidence_set=evidence_set)

    def _render_prompt(self, query, built, reasoning=None, query_type=""):
        return self._module._render_prompt(query, built, reasoning=reasoning, query_type=query_type)

    def _reason(self, query, built, query_type=""):
        return self._module._reason(query, built, query_type=query_type)

    def _call_llm(self, system_prompt, user_prompt):
        return self._module._call_llm(system_prompt, user_prompt)

    def _extract_citations(self, llm_response, chunks, built):
        return self._module._extract_citations(llm_response, chunks, built)

    def _log_generation(
        self,
        query_log_id,
        query,
        llm_response,
        sanitized,
        total_latency_ms,
        built,
    ):
        self._module._log_generation(
            query_log_id, query, llm_response, sanitized, total_latency_ms, built
        )

    def _assemble_response(
        self,
        query,
        query_type,
        chunks,
        llm_response,
        sanitized,
        total_latency_ms,
        built,
        reasoning=None,
    ):
        return self._module._assemble_response(
            query, query_type, chunks, llm_response, sanitized, total_latency_ms, built,
            reasoning=reasoning,
        )
