"""Notepad legal-RAG thread — query rewriting, fragmentation, Answer turns.

The module that answers R2/R3/R4 of the Notepad requirement: the AI button
routes through the **legal RAG module** (``run_generation_pipeline``), not a
generic LLM. One Note is a thread of append-only ``NoteAnswer`` turns.

Three responsibilities, one owner (spec FR-5):

1. **Fragmentation** (F1–F5) — the user splits a compound question into
   focused fragments (one per line); each fragment gets its own pipeline
   call and its own Answer row. Fragments of one ask are siblings: they
   share the statement and the prior turns but never see each other's
   answers.
2. **Query rewriting** (FR-6, INV-7) — the pipeline takes a single query
   string, so multi-turn is deterministic string assembly: statement +
   last N prior turns + the new question. No LLM participates in the
   rewrite — that would re-couple the answer path to generic AI.
3. **Result mapping** (§5.4) — the pipeline result dict maps onto a
   ``NoteAnswer`` row, including the degraded flag from the circuit-breaker
   fallback and the ``rag_log_id`` join-back.

``ask`` constructs rows but never touches the session: the caller adds and
commits, so a failure mid-ask leaves zero rows (INV-8).

The pipeline seam (``get_pipeline``) resolves
``run_generation_pipeline`` and ``cfg.context_max_chunks`` **at call time**
(late binding), mirroring ``app/rag/routes.py::_get_query_breaker``, so
tests monkeypatch without rebuilding the singleton.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.notepad import Note, NoteAnswer
from app.shared.config import cfg

__all__ = [
    "ask",
    "build_standalone_query",
    "get_pipeline",
    "split_fragments",
    "validate_question",
]

#: Deterministic caps on the rewritten query (INV-7).
_MAX_PRIOR_TURNS = 5
_ANSWER_PREVIEW_CHARS = 600

#: Spend bounds per ask (spec V-3/V-4).
_MAX_FRAGMENTS = 10
_MAX_FRAGMENT_CHARS = 4000

#: Closing instruction of the rewritten query — inline constant, no prompts
#: package in this repo (NFR-4).
_ANSWER_INSTRUCTION = (
    "Answer the question now, grounded in the retrieved law. "
    "Cite the exact sections you rely on; if the retrieved material does "
    "not answer it, say so."
)

#: Lazily-built circuit-breaker pipeline (module-level so state survives
#: across asks within a process, same rationale as ``_query_breaker``).
_PIPELINE: Any = None


def split_fragments(text: str) -> list[str]:
    """Split an ask box into fragments: one non-empty line each, in order.

    A single-line question yields exactly one fragment; blank and
    whitespace-only lines are skipped. Non-string input yields ``[]``.
    """
    if not isinstance(text, str):
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_standalone_query(
    note_text: str,
    prior_turns: list[tuple[str, str]],
    question: str,
) -> str:
    """Rewrite a follow-up into a standalone query for the RAG pipeline.

    Deterministic string assembly (FR-6): the statement, the last
    ``_MAX_PRIOR_TURNS`` prior exchanges (question + answer truncated to
    ``_ANSWER_PREVIEW_CHARS``), then the new question and the answering
    instruction. The prior-turns section is omitted entirely when empty.
    """
    sections = ["Context — the officer's statement:\n" + (note_text or "").strip()]

    turns = list(prior_turns)[-_MAX_PRIOR_TURNS:]
    if turns:
        lines = ["Earlier exchanges (oldest first):"]
        for idx, (prior_question, prior_answer) in enumerate(turns, start=1):
            lines.append(f"Q{idx}: {prior_question.strip()}")
            lines.append(f"A{idx}: {(prior_answer or '')[:_ANSWER_PREVIEW_CHARS]}")
        sections.append("\n".join(lines))

    sections.append("Question now:\n" + question.strip())
    sections.append(_ANSWER_INSTRUCTION)
    return "\n\n".join(sections)


def get_pipeline() -> Any:
    """Return the shared :class:`ResilientRAGPipeline` for Notepad asks.

    The wrapped callable resolves ``run_generation_pipeline`` and
    ``cfg.context_max_chunks`` at call time (FR-4, FR-7): patching either
    after this singleton is built still takes effect.
    """
    global _PIPELINE
    if _PIPELINE is None:
        import app.rag.tasks as tasks_mod
        from app.rag.resilient import ResilientRAGPipeline

        def _pipeline(query: str, top_k: int | None = None, **kwargs: Any) -> dict[str, Any]:
            return tasks_mod.run_generation_pipeline(
                query=query,
                top_k=top_k or cfg.context_max_chunks,
                **kwargs,
            )

        _PIPELINE = ResilientRAGPipeline(pipeline_fn=_pipeline)
    return _PIPELINE


def validate_question(question: str) -> list[str]:
    """Validate one ask box and return its fragments (V-1…V-4).

    This is the route's validation entry point (spec §8): the route runs it
    **before** the kill-switch gates so an input problem and a disabled
    feature each report distinctly. ``ask`` reuses it, so the fragment caps
    stay owned in exactly one place (FR-5). Raises ``ValueError`` on the
    first violation.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    fragments = split_fragments(question)
    if not fragments:
        raise ValueError("question must contain at least one fragment")
    if len(fragments) > _MAX_FRAGMENTS:
        raise ValueError(f"at most {_MAX_FRAGMENTS} fragments per ask")
    for idx, fragment in enumerate(fragments, start=1):
        if len(fragment) > _MAX_FRAGMENT_CHARS:
            raise ValueError(f"fragment {idx} exceeds {_MAX_FRAGMENT_CHARS} characters")
    return fragments


def ask(note: Note, question: str) -> list[NoteAnswer]:
    """Answer ``question`` against ``note`` through the legal RAG pipeline.

    Returns one :class:`NoteAnswer` per fragment, in input order — **not**
    added to the session; the caller adds and commits so a mid-ask failure
    leaves zero rows (INV-8). Raises ``ValueError`` for invalid input
    (no RAG call is made) and lets pipeline exceptions propagate.
    """
    fragments = validate_question(question)

    # Snapshot + prior turns are captured once: fragments of one ask are
    # siblings sharing the same context (F4/F5).
    snapshot = note.content_text or ""
    prior_turns = [(a.question, a.answer) for a in note.answers]

    pipeline = get_pipeline()
    answers: list[NoteAnswer] = []
    for fragment in fragments:
        query = build_standalone_query(snapshot, prior_turns, fragment)
        result = pipeline.run(query=query, top_k=None)
        answers.append(_to_answer(note, fragment, snapshot, result))
    return answers


def _to_answer(
    note: Note,
    question: str,
    snapshot: str,
    result: dict[str, Any],
) -> NoteAnswer:
    """Map one pipeline result dict onto a NoteAnswer row (spec §5.4)."""
    if not isinstance(result, dict):
        raise ValueError("pipeline returned a non-object result")
    debug = result.get("debug") or {}
    return NoteAnswer(
        note_id=note.id,
        question=question,
        answer=result.get("answer") or "",
        citations=json.dumps(result.get("citations") or []),
        sub_queries=json.dumps(result.get("sub_queries") or []),
        note_snapshot=snapshot,
        groundedness_score=result.get("groundedness_score"),
        hallucination_detected=bool(result.get("hallucination_detected")),
        degraded=bool(debug.get("degraded_mode")),
        provider_model=result.get("llm_model"),
        token_usage=result.get("token_usage"),
        rag_log_id=result.get("log_id"),
    )
