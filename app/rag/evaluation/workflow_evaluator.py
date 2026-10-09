"""Workflow answer evaluator -- deterministic quality assessment for FSSAI workflow answers."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass

from app.rag.evaluation.metrics import EvalScore
from app.rag.evaluation.textmatch import content_tokens, token_coverage
from app.rag.verification.claim_extractor import ClaimExtractor
from app.rag.verification.evidence_verifier import EvidenceVerifier

__all__ = [
    "WorkflowAnswerEvaluation",
    "evaluate_workflow_answer",
    "score_citation_quality",
    "score_completeness",
    "score_faithfulness",
    "score_structure",
]

_CITATION_RE = re.compile(r"\[(\d+)\]")
_FORM_RE = re.compile(r"\bform\s+(viia|vii|viii|iii|ii|iv|vi|v)\b", re.IGNORECASE)


@dataclass(frozen=True)
class WorkflowAnswerEvaluation:
    query: str
    answer: str
    gold_answer: str | None = None
    retrieved_chunks: list = None
    cited_chunk_ids: list | None = None
    faithfulness: EvalScore = None
    completeness: EvalScore = None
    citation_quality: EvalScore = None
    structure: EvalScore = None
    overall: EvalScore = None
    fa_form_coverage: float = 0.0
    gold_form_coverage: float = 0.0
    steps_count: int = 0

    @property
    def is_workflow(self) -> bool:
        return self.faithfulness.score >= 0.0


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _chunk_text(c) -> str:
    if hasattr(c, "text"):
        return str(c.text or "")
    if isinstance(c, dict):
        return str(c.get("text") or c.get("chunk_text") or "")
    return str(c)


def _extract_forms(answer: str) -> set[str]:
    forms = set()
    for m in _FORM_RE.finditer(answer or ""):
        forms.add(m.group(1).lower())
    return forms


def _extract_steps(answer: str) -> list[str]:
    steps = []
    patterns = [
        r"\s*(\d+)\.\s+(.*?)(?=\s*\d+\.\s+|\Z)",
        r"\s*[*\-]\s+(.*?)(?=^\s*[*\-]\s+|\Z)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, answer or "", re.MULTILINE | re.DOTALL):
            steps.append(m.group(1).strip())
    return steps


# ---------------------------------------------------------------------------
# Faithfulness
# ---------------------------------------------------------------------------


def score_faithfulness(answer: str, chunks: list, query: str = "") -> EvalScore:
    if not chunks:
        return EvalScore(name="faithfulness", score=0.0, explanation="No evidence retrieved.", detail={})
    claims = ClaimExtractor().extract(answer or "")
    if not claims:
        return EvalScore(name="faithfulness", score=1.0, explanation="No claims to verify.", detail={"claims": 0})
    verifier = EvidenceVerifier()
    chunk_objs = []
    for c in chunks:
        chunk_objs.append(c if hasattr(c, "text") else type("Chunk", (), {"text": str(c)})())
    verifications = verifier.verify_claims(claims, chunk_objs)
    supported = sum(1 for v in verifications if v.confidence >= 0.5)
    score = supported / len(claims)
    return EvalScore(
        name="faithfulness",
        score=round(score, 4),
        explanation=f"{supported}/{len(claims)} claims grounded",
        detail={"claims": len(claims), "grounded": supported},
    )


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def score_completeness(answer: str, gold_answer: str | None, chunks: list | None = None) -> EvalScore:
    if not gold_answer:
        return EvalScore(name="completeness", score=1.0, explanation="No gold answer.", detail={"note": "no_gold"})
    fa_forms = _extract_forms(answer)
    gold_forms = _extract_forms(gold_answer)
    gold_steps = _extract_steps(gold_answer)
    answer_steps = _extract_steps(answer)
    steps_covered = 0
    for gs in gold_steps:
        for as_ in answer_steps:
            if token_coverage(content_tokens(gs), content_tokens(as_)) >= 0.5:
                steps_covered += 1
                break
    forms_covered = len(fa_forms & gold_forms) / max(len(gold_forms), 1)
    form_score = forms_covered
    step_score = steps_covered / max(len(gold_steps), 1) if gold_steps else 0.0
    if chunks:
        needles = content_tokens(gold_answer)
        chunk_texts = [_chunk_text(c) for c in chunks]
        evidence_matched = any(token_coverage(needles, ct) >= 0.5 for ct in chunk_texts)
    else:
        evidence_matched = True
    score = (form_score + step_score + float(evidence_matched)) / 3.0
    detail = {
        "fa_forms": sorted(fa_forms),
        "gold_forms": sorted(gold_forms),
        "steps_matched": steps_covered,
        "form_score": round(form_score, 4),
        "step_score": round(step_score, 4),
    }
    return EvalScore(
        name="completeness",
        score=round(score, 4),
        explanation=f"forms={form_score:.2f}, steps={step_score:.2f}, evidence={evidence_matched} -> {score:.2f}",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Citation quality
# ---------------------------------------------------------------------------


def score_citation_quality(answer: str, chunks: list | None = None) -> EvalScore:
    cited = _CITATION_RE.findall(answer or "")
    if not cited:
        return EvalScore(name="citation_quality", score=0.5, explanation="No citations found.", detail={"count": 0})
    valid_numbers = set()
    for c in cited:
        with suppress(ValueError):
            valid_numbers.add(int(c))
    if not valid_numbers:
        return EvalScore(name="citation_quality", score=0.0, explanation="Invalid citations.", detail={"cited": cited})
    if chunks:
        chunk_ids = set()
        for c in chunks:
            if hasattr(c, "chunk_id"):
                chunk_ids.add(c.chunk_id)
        valid = [cid for cid in valid_numbers if str(cid) in chunk_ids]
        return EvalScore(
            name="citation_quality",
            score=round(len(valid) / len(valid_numbers), 4),
            explanation=f"Cited {len(valid)}/{len(valid_numbers)} valid.",
            detail={"cited": sorted(valid_numbers), "valid": sorted(valid)},
        )
    return EvalScore(
        name="citation_quality",
        score=round(len(valid_numbers) / max(len(cited), 1), 4),
        explanation=f"Cited {len(valid_numbers)}/{len(cited)}.",
        detail={"cited": sorted(valid_numbers)},
    )


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def score_structure(answer: str, query: str = "") -> EvalScore:
    text = (answer or "").strip()
    if not text:
        return EvalScore(name="structure", score=0.0, explanation="Empty.", detail={"note": "empty"})
    step_count = len(_extract_steps(text))
    if step_count == 0:
        step_count = 1 if re.search(r"\d+\.", text) else (2 if re.search(r"\n", text) else 0)
    if step_count == 0:
        score = 0.3
        explanation = "Narrative only."
    elif step_count <= 3:
        score = 0.7
        explanation = f"{step_count} steps."
    elif step_count <= 7:
        score = 0.85
        explanation = f"{step_count} steps; good coverage."
    else:
        score = 0.95
        explanation = f"{step_count} steps; comprehensive."
    return EvalScore(
        name="structure", score=round(score, 4), explanation=explanation, detail={"steps_found": step_count},
    )


# ---------------------------------------------------------------------------
# Main evaluation entry point
# ---------------------------------------------------------------------------


def evaluate_workflow_answer(
    query: str,
    answer: str,
    gold_answer: str | None = None,
    chunks: list | None = None,
    cited_chunk_ids: list | None = None,
) -> WorkflowAnswerEvaluation:
    faithfulness = score_faithfulness(answer, chunks or [], query)
    completeness = score_completeness(answer, gold_answer, chunks)
    citation_quality = score_citation_quality(answer, chunks)
    structure = score_structure(answer, query)
    overall = (
        faithfulness.score * 0.35 + completeness.score * 0.30 + citation_quality.score * 0.15 + structure.score * 0.20
    )
    fa_forms = _extract_forms(answer)
    if gold_answer:
        gold_forms = _extract_forms(gold_answer)
        form_coverage = len(fa_forms & gold_forms) / max(len(gold_forms), 1)
    else:
        form_coverage = 0.0
    return WorkflowAnswerEvaluation(
        query=query,
        answer=answer,
        gold_answer=gold_answer,
        retrieved_chunks=chunks,
        cited_chunk_ids=cited_chunk_ids or [],
        faithfulness=faithfulness,
        completeness=completeness,
        citation_quality=citation_quality,
        structure=structure,
        overall=EvalScore(name="overall", score=round(overall, 4), explanation="Weighted avg.", detail={}),
        fa_form_coverage=round(len(fa_forms), 4),
        gold_form_coverage=round(form_coverage, 4),
        steps_count=structure.detail.get("steps_found", 0),
    )
