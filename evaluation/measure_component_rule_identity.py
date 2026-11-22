"""Shared loaders/helpers for component-rule identity evaluation.

Lightweight module: no torch/matplotlib imports. Provides the stable API used by
evaluator_v4, audit/calibration scripts, and live A/B scoring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
CACHE_DIR = PROJECT_ROOT / "evaluation" / "out" / "cache"


def load_payload_index(path: Path | None = None) -> dict[str, dict]:
    """Load {point_id: payload} from the cached Qdrant payload index."""
    cache_file = Path(path) if path else CACHE_DIR / "payload_index.jsonl"
    index: dict[str, dict] = {}
    if not cache_file.exists():
        return index
    with open(cache_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[str(rec["id"])] = rec.get("payload") or {}
    return index


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_answers(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load C-O3 answer records keyed by qid.

    Primary source: evaluator_v2_per_question.jsonl (C-O3 arm). Falls back to
    human_gold_labels.jsonl when present. Missing/absent files yield {}.
    """
    src = Path(path) if path else OUT / "evaluator_v2_per_question.jsonl"
    records = _load_jsonl_records(src)
    answers: dict[str, dict[str, Any]] = {}
    for rec in records:
        qid = rec.get("qid") or rec.get("question_id")
        if not qid:
            continue
        arm = rec.get("C-O3") or rec.get("c_o3") or rec
        if not isinstance(arm, dict):
            continue
        status = arm.get("status", "ok")
        cited = arm.get("cited_ids") or arm.get("cited_chunk_ids") or []
        context = arm.get("context_ids") or arm.get("evidence_chunk_ids") or []
        answers[str(qid)] = {
            "qid": str(qid),
            "status": status,
            "answer": arm.get("answer") or "",
            "cited_ids": [str(x) for x in cited],
            "context_ids": [str(x) for x in context],
            "grader": arm.get("grader") if isinstance(arm.get("grader"), dict) else {},
            "v2": arm.get("v2") if isinstance(arm.get("v2"), dict) else {},
        }
    if answers:
        return answers

    # Fallback: human_gold_labels.jsonl (same C-O3 shape)
    fallback = OUT / "human_gold_labels.jsonl"
    for rec in _load_jsonl_records(fallback):
        qid = rec.get("qid") or rec.get("question_id")
        if not qid:
            continue
        arm = rec.get("C-O3") or rec
        if not isinstance(arm, dict):
            continue
        answers[str(qid)] = {
            "qid": str(qid),
            "status": arm.get("status", "ok"),
            "answer": arm.get("answer") or "",
            "cited_ids": [str(x) for x in (arm.get("cited_ids") or arm.get("cited_chunk_ids") or [])],
            "context_ids": [str(x) for x in (arm.get("context_ids") or arm.get("evidence_chunk_ids") or [])],
            "grader": arm.get("grader") if isinstance(arm.get("grader"), dict) else {},
            "v2": arm.get("v2") if isinstance(arm.get("v2"), dict) else {},
        }
    return answers


def load_verdicts(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load human audit verdicts keyed by qid.

    Accepts full_review_tabulation.json or human_audit_tabulation.json format
    (dict with 'records' list). Returns {} when absent/empty.
    """
    candidates = [Path(path)] if path else [OUT / "full_review_tabulation.json", OUT / "human_audit_tabulation.json"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(records, list):
            continue
        out: dict[str, dict[str, Any]] = {}
        for rec in records:
            if not isinstance(rec, dict):
                continue
            qid = rec.get("qid") or rec.get("question_id")
            if qid:
                out[str(qid)] = rec
        if out:
            return out
    return {}


def evidence_texts_for(context_ids: list[str], payload_index: dict[str, dict]) -> dict[str, str]:
    """Map context chunk ids to chunk text for grader evidence checks."""
    out: dict[str, str] = {}
    for cid in context_ids or []:
        key = str(cid)
        payload = payload_index.get(key) or {}
        text = payload.get("chunk_text") or payload.get("text") or ""
        if text:
            out[key] = str(text)
    return out
