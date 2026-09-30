"""Food-intent failure-mode metrics (task spec §18) over benchmark v1.1.

Six metrics, each computed from a ranked chunk-id list + the question's gold
record (benchmark/benchmark_food_intent_v1.1.jsonl)::

    Entity Recall@K      — any gold chunk in top-K        (entity identity)
    Standard Recall@K    — a *standard-shaped* gold chunk in top-K
                           (requirement language / measurement rows; the
                           anti-definition-anchoring metric)
    Provision Recall@K   — any gold chunk at all in top-K  (provision coverage)
    Parameter Recall@K   — a gold chunk containing the named parameter(s)
                           (parameter-specific asks only)
    Source Recall@K      — a gold chunk sharing the question's gold document
                           (act/regulation located)
    Definition-vs-Standard Disambiguation Accuracy — top-1 is in
                           gold_top1_chunks AND no trap_chunk outranks every
                           gold chunk.

All metrics are deterministic set/rank computations over chunk ids — no
model, no network — so they are unit-testable offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_V11_FILE = PROJECT_ROOT / "benchmark" / "benchmark_food_intent_v1.1.jsonl"

#: Ranking depths reported for every metric.
FOOD_INTENT_KS = (1, 3, 5, 10, 20)


# --------------------------------------------------------------------------- #
# Benchmark loading
# --------------------------------------------------------------------------- #
def load_food_intent_questions(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the v1.1 food-intent questions (validated schema)."""
    p = Path(path) if path else BENCHMARK_V11_FILE
    questions: list[dict[str, Any]] = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            for key in ("question_id", "question", "expected_intent", "gold_source_chunks", "gold_top1_chunks"):
                if key not in rec:
                    raise ValueError(f"{p.name}:{rec.get('question_id', '?')} missing required field {key!r}")
            questions.append(rec)
    return questions


# --------------------------------------------------------------------------- #
# Metric primitives
# --------------------------------------------------------------------------- #
def _rank_of_first(chunk_ids: list[str], wanted: set[str]) -> int | None:
    """1-based rank of the first chunk_id in *wanted*, or None."""
    for i, cid in enumerate(chunk_ids):
        if cid in wanted:
            return i + 1
    return None


def entity_recall_at_k(chunk_ids: list[str], q: dict[str, Any], k: int) -> bool:
    """Any gold chunk within top-K."""
    gold = set(q["gold_source_chunks"])
    top = set(chunk_ids[:k])
    return bool(gold & top)


def standard_recall_at_k(chunk_ids: list[str], q: dict[str, Any], k: int) -> bool:
    """A standard-shaped gold chunk within top-K.

    Definition questions score vacuously True only when a gold chunk is
    retrieved — the metric is designed for standard/parameter/compliance
    intents; for definition intents it degenerates to entity recall.
    """
    if q["expected_intent"] == "definition":
        return entity_recall_at_k(chunk_ids, q, k)
    standard_gold = set(q.get("gold_top1_chunks") or q["gold_source_chunks"])
    top = set(chunk_ids[:k])
    return bool(standard_gold & top)


def provision_recall_at_k(chunk_ids: list[str], q: dict[str, Any], k: int) -> bool:
    """Any gold chunk (provision coverage) within top-K."""
    return entity_recall_at_k(chunk_ids, q, k)


def parameter_recall_at_k(chunk_ids: list[str], q: dict[str, Any], k: int) -> bool:
    """A gold chunk carrying the named parameter(s) within top-K.

    Only meaningful when the question names parameters; vacuous otherwise.
    """
    params = q.get("parameters") or []
    if not params:
        return False
    gold = set(q["gold_source_chunks"])
    top = set(chunk_ids[:k])
    return bool(gold & top)


def source_recall_at_k(
    chunk_ids: list[str],
    q: dict[str, Any],
    k: int,
    doc_for_chunk: dict[str, str] | None = None,
) -> bool:
    """A chunk from the gold document within top-K (source located).

    Retrieved ids are Qdrant point ids; the gold document id lives on the
    payload, so pass ``doc_for_chunk`` (point id → document id, from the
    cached payload index).  Without the map the metric falls back to the
    gold-chunk intersection (every gold chunk belongs to the gold document).
    """
    docs = set(q.get("gold_document_ids") or ([q["source_document"]] if q.get("source_document") else []))
    if not docs:
        return False
    top = chunk_ids[:k]
    if doc_for_chunk:
        return any(doc_for_chunk.get(cid) in docs for cid in top)
    gold = set(q["gold_source_chunks"])
    return bool(gold & set(top))


def disambiguation_correct(chunk_ids: list[str], q: dict[str, Any]) -> bool:
    """Definition-vs-Standard Disambiguation Accuracy (top-1 judgement).

    Correct when top-1 is an expected top-1 gold chunk and every trap chunk
    (if any) ranks below the first gold chunk.
    """
    if not chunk_ids:
        return False
    top1 = set(q.get("gold_top1_chunks") or q["gold_source_chunks"])
    if chunk_ids[0] not in top1:
        return False
    gold = set(q["gold_source_chunks"])
    traps = set(q.get("trap_chunks") or [])
    traps -= top1  # a trap listed as gold_top1 is a data error, not a trap
    first_gold_rank = _rank_of_first(chunk_ids, gold)
    for t in traps:
        # trap must not appear above the first gold chunk
        try:
            trap_rank = chunk_ids.index(t) + 1
        except ValueError:
            continue  # trap not retrieved — fine
        if first_gold_rank is None or trap_rank < first_gold_rank:
            return False
    return True


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def evaluate_food_intent(
    results: dict[str, list[str]],
    questions: list[dict[str, Any]] | None = None,
    ks: tuple[int, ...] = FOOD_INTENT_KS,
    doc_for_chunk: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate the §18 metrics over ``{question_id: ranked_chunk_ids}``.

    Returns a JSON-serialisable report with per-K rates (pp = percentage of
    questions) plus the disambiguation accuracy and per-question detail.
    """
    questions = questions if questions is not None else load_food_intent_questions()
    by_id = {q["question_id"]: q for q in questions}

    detail: dict[str, dict[str, Any]] = {}
    counts = {name: {k: 0 for k in ks} for name in ("entity", "standard", "provision", "parameter", "source")}
    disambig_hits = 0
    disambig_total = 0
    parameter_eligible = 0

    for qid, chunk_ids in results.items():
        q = by_id.get(qid)
        if q is None:
            continue
        chunk_ids = list(chunk_ids)
        entry: dict[str, Any] = {}
        for k in ks:
            e = entity_recall_at_k(chunk_ids, q, k)
            s = standard_recall_at_k(chunk_ids, q, k)
            p = provision_recall_at_k(chunk_ids, q, k)
            par = parameter_recall_at_k(chunk_ids, q, k)
            src = source_recall_at_k(chunk_ids, q, k, doc_for_chunk=doc_for_chunk)
            counts["entity"][k] += int(e)
            counts["standard"][k] += int(s)
            counts["provision"][k] += int(p)
            counts["parameter"][k] += int(par)
            counts["source"][k] += int(src)
            entry[f"entity@{k}"] = e
            entry[f"standard@{k}"] = s
            entry[f"provision@{k}"] = p
            entry[f"parameter@{k}"] = par
            entry[f"source@{k}"] = src
        disambig = disambiguation_correct(chunk_ids, q)
        disambig_hits += int(disambig)
        disambig_total += 1
        entry["disambiguation_correct"] = disambig
        if q.get("parameters"):
            parameter_eligible += 1
            entry["parameter_eligible"] = True
        detail[qid] = entry

    n = len(by_id) or 1
    report: dict[str, Any] = {
        "n_questions": len(by_id),
        "n_evaluated": len(detail),
        "ks": list(ks),
        "metrics": {},
        "disambiguation_accuracy": round(disambig_hits / disambig_total, 4) if disambig_total else 0.0,
        "parameter_eligible_questions": parameter_eligible,
        "detail": detail,
    }
    for name, per_k in counts.items():
        # Parameter recall is only meaningful for parameter-named questions;
        # the other 13 (non-eligible) questions contribute vacuous False.
        # Normalise over the eligible denominator so 1.0 = all eligible
        # questions answered (dividing by n understates by exactly half).
        denominator = parameter_eligible if name == "parameter" and parameter_eligible else n
        report["metrics"][name] = {
            f"recall@{k}": round(v / denominator, 4) for k, v in per_k.items()
        }
    return report


def failure_examples(
    results: dict[str, list[str]],
    questions: list[dict[str, Any]] | None = None,
    arm: str = "",
) -> list[dict[str, Any]]:
    """Per-question failure records for the §23 failure analysis."""
    questions = questions if questions is not None else load_food_intent_questions()
    by_id = {q["question_id"]: q for q in questions}
    failures: list[dict[str, Any]] = []
    for qid, chunk_ids in results.items():
        q = by_id.get(qid)
        if q is None:
            continue
        chunk_ids = list(chunk_ids)
        modes: list[str] = []
        if not entity_recall_at_k(chunk_ids, q, 10):
            modes.append("entity_miss@10")
        if not standard_recall_at_k(chunk_ids, q, 5):
            modes.append("standard_miss@5")
        if not disambiguation_correct(chunk_ids, q):
            top1 = chunk_ids[0] if chunk_ids else None
            if top1 in (q.get("trap_chunks") or []):
                modes.append("trap_rank1")
            elif top1 not in (q.get("gold_top1_chunks") or []):
                modes.append("wrong_top1")
        if modes:
            failures.append(
                {
                    "arm": arm,
                    "question_id": qid,
                    "category": q["category"],
                    "question": q["question"],
                    "modes": modes,
                    "top5": chunk_ids[:5],
                    "gold_top1": q.get("gold_top1_chunks"),
                    "gold_all": q["gold_source_chunks"],
                    "traps": q.get("trap_chunks", []),
                }
            )
    return failures
