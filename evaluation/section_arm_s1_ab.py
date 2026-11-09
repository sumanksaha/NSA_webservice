"""Section-arm A/B on the gold-recall misses (retrieval-only, 0 LLM calls).

``RAG_SECTION_ARM`` (default **False**, absent from render.yaml) adds a
section/act-scoped structural arm to the hybrid retriever.  Its prior
validation (``evaluation/out/ceiling_v5/section_arm_validation.json``) showed
gold moving into the top-10 window 3 -> 7 of 10 with zero gold lost, but that
ran arm-level retrieval over an older failure cohort.

This script re-measures it against the *current* S1 misses: for every
answerable qid whose gold section was absent from the recorded pool, it runs
the production retrieval pipeline twice — arm off, then on — in one process
(identical code, collection, embedder), and scores gold by provision stamp.

Answer: does turning the arm on put the missing provision in the pool, and
does it ever displace gold that was already there?
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
os.environ["RAG_QDRANT_BM25"] = "true"
os.environ["HF_HUB_OFFLINE"] = "1"

from app import create_app  # noqa: E402

from evaluation.provision_match import stamp_is_exact, stamp_match  # noqa: E402
from evaluation.signals_io import load_signals  # noqa: E402

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
SIGNALS = OUT_DIR / "s1_pipeline_signals.jsonl"
RESULT = OUT_DIR / "section_arm_s1_ab.json"


def _hits(gold: list[str], pool: list[dict]) -> list[str]:
    return [g for g in gold if any(stamp_match(g, c) for c in pool)]


def main(limit: int = 0) -> int:
    bench: dict[str, dict] = {}
    with open(PROJECT_ROOT / "benchmark" / "benchmark_v1.0.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                bench[d["question_id"]] = d

    rows = load_signals(SIGNALS)
    targets = []
    for r in rows:
        if r["expected_abstain"] or r.get("retrieval_error"):
            continue
        gold = [g for g in (bench[r["question_id"]].get("gold_source_chunks") or []) if stamp_is_exact(g)]
        if not gold:
            continue
        stamps = r.get("chunk_stamps") or []
        if not any(stamp_match(g, c) for g in gold for c in stamps):
            targets.append(r["question_id"])
    if limit:
        targets = targets[:limit]
    print(f"section-arm A/B on {len(targets)} gold-recall misses", flush=True)

    app = create_app()
    with app.app_context():
        from app.rag.tasks import clear_retrieval_cache, run_retrieval_pipeline
        from app.shared.config import cfg

        per_qid: dict[str, dict] = {}
        for i, qid in enumerate(targets, 1):
            q = bench[qid]
            gold = [g for g in (q.get("gold_source_chunks") or []) if stamp_is_exact(g)]
            out = {"gold": gold}
            for label, enabled in (("arm_off", False), ("arm_on", True)):
                cfg.section_arm = enabled
                clear_retrieval_cache()
                try:
                    res = run_retrieval_pipeline(q["question"], top_k=10, collection_name=q.get("collections") or None)
                    pool = res.get("chunks") or []
                    out[label] = {
                        "n_chunks": len(pool),
                        "gold_hits": _hits(gold, pool),
                        "sec_stamped": sum(1 for c in pool if c.get("section_number")),
                        "error": str(res.get("error"))[:120] if res.get("error") else None,
                    }
                except Exception as exc:  # keep the sweep moving
                    out[label] = {
                        "n_chunks": 0,
                        "gold_hits": [],
                        "sec_stamped": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            per_qid[qid] = out
            print(
                f"  {qid} off={bool(out['arm_off']['gold_hits'])} on={bool(out['arm_on']['gold_hits'])} "
                f"secs {out['arm_off']['sec_stamped']}->{out['arm_on']['sec_stamped']}",
                flush=True,
            )
            if i % 10 == 0:
                print(f"{i}/{len(targets)}", flush=True)

    off_hits = [q for q, v in per_qid.items() if v["arm_off"]["gold_hits"]]
    on_hits = [q for q, v in per_qid.items() if v["arm_on"]["gold_hits"]]
    gained = sorted(set(on_hits) - set(off_hits))
    lost = sorted(set(off_hits) - set(on_hits))
    summary = {
        "n": len(per_qid),
        "gold_hit_arm_off": len(off_hits),
        "gold_hit_arm_on": len(on_hits),
        "gained": gained,
        "lost": lost,
    }
    RESULT.write_text(json.dumps({"summary": summary, "per_qid": per_qid}, indent=1) + "\n", encoding="utf-8")
    print(f"\nsummary: {summary}")
    print(f"written: {RESULT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
