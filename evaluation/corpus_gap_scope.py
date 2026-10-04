"""P0-2 scope: which benchmark questions have no in-corpus evidence?

The analysis doc lists missing statutes (Water Act, WB Meat Order, KMC
water rules, PCA schedules) from prose. This measures the gap from the data
side instead: for each of the 150 benchmark questions, do its gold
provisions actually appear in the indexed corpus?

Three outcomes per question:

  ``covered``      gold provision resolves against the payload cache
  ``not_in_corpus`` gold provision resolves nowhere — a corpus gap, and the
                   single most actionable bucket (no model can answer from
                   text that isn't there)
  ``no_gold``      the question declares no gold units (e.g. open-ended or
                   abstention items) — excluded from the gap rate

``evaluation/coverage_audit.py`` answers a different question (does a chunk
carry a retrievable identity), so this is deliberately separate.

Usage:
    python -m evaluation.corpus_gap_scope
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions, load_gold_registry
from evaluation.resolution import FamilyMap, matches_gold

CACHE_DIR = PROJECT_ROOT / "evaluation" / "out" / "cache"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "corpus_gap_scope.json"


def _load_payloads() -> list[dict[str, Any]]:
    path = CACHE_DIR / "payload_index.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> int:
    payloads = _load_payloads()
    if not payloads:
        print(f"missing payload cache: {CACHE_DIR / 'payload_index.jsonl'}", file=sys.stderr)
        return 1

    plist = [p["payload"] for p in payloads if "payload" in p]
    questions = load_questions()
    registry = load_gold_registry()
    family_map = FamilyMap()

    per_q: list[dict[str, Any]] = []
    missing_by_act: dict[str, set[str]] = defaultdict(set)
    covered_units = missing_units = 0

    for q in questions:
        units = q.recall_units()
        if not units:
            per_q.append({"qid": q.question_id, "status": "no_gold"})
            continue
        missing_here = []
        for u in units:
            hit = any((lambda pay: _safe_match(pay, u, family_map))(pay) for pay in plist)
            if hit:
                covered_units += 1
            else:
                missing_units += 1
                missing_here.append(u)
                missing_by_act[u.act or "(unknown act)"].add(u.provision_id)
        per_q.append({
            "qid": q.question_id,
            "status": "not_in_corpus" if missing_here else "covered",
            "question": q.question,
            "missing_provisions": [
                {"provision_id": u.provision_id, "act": u.act, "section": u.section} for u in missing_here
            ],
        })

    counts = Counter(r["status"] for r in per_q)
    graded = counts["covered"] + counts["not_in_corpus"]
    gap_rate = counts["not_in_corpus"] / max(graded, 1)

    out = {
        "version": "corpus-gap-scope-1",
        "date": "2026-10-04",
        "corpus": {"payloads_indexed": len(plist), "gold_registry_provisions": len(registry)},
        "summary": {
            "questions_total": len(questions),
            "covered": counts["covered"],
            "not_in_corpus": counts["not_in_corpus"],
            "no_gold": counts["no_gold"],
            "question_gap_rate": round(gap_rate, 4),
            "gold_units_total": covered_units + missing_units,
            "gold_units_missing": missing_units,
            "unit_gap_rate": round(missing_units / max(covered_units + missing_units, 1), 4),
        },
        "missing_by_act": {act: sorted(ids) for act, ids in sorted(missing_by_act.items())},
        "per_question": per_q,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    s = out["summary"]
    print("=" * 74)
    print("P0-2 corpus gap scope")
    print("=" * 74)
    print(f"payloads indexed : {out['corpus']['payloads_indexed']}")
    print(
        f"questions        : {s['questions_total']}  (covered {s['covered']}, "
        f"not_in_corpus {s['not_in_corpus']}, no_gold {s['no_gold']})"
    )
    print(f"question gap rate: {s['question_gap_rate']:.1%}")
    print(f"gold units       : {s['gold_units_missing']}/{s['gold_units_total']} missing ({s['unit_gap_rate']:.1%})")
    print()
    if missing_by_act:
        print("MISSING PROVISIONS BY ACT")
        for act, ids in sorted(missing_by_act.items(), key=lambda kv: -len(kv[1])):
            listed = sorted(ids)
            print(f"  {len(listed):>3}  {act}")
            print(f"       {', '.join(listed[:8])}{' ...' if len(listed) > 8 else ''}")
    else:
        print("No missing provisions detected — every gold unit resolves in-corpus.")
    print()
    print(f"written: {OUT_FILE}")
    return 0


def _safe_match(payload: dict[str, Any], unit: Any, family_map: Any) -> bool:
    try:
        return bool(matches_gold(payload, unit, family_map))
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
