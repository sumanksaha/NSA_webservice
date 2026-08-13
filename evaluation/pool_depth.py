"""4d — union pool-depth diagnostic (offline, no retrieval).

The report's union composes dense@200 + sparse@200 + KG@200.  This measures
how much the 200-slice truncation costs the candidate pool: for slice depths
d in {100, 200, 300, 400, 500} it builds the dedup union pool *with the
report's own machinery* (build_union_arms + unit_first_ranks, pool-collapse
semantics) and reports the pool coverage — the candidate-generation ceiling
at each depth.  Depth 200 must reproduce the report's E_union_pool R@500
exactly, which makes the truncation-cost numbers directly comparable.

Usage:
    python -m evaluation.pool_depth [--raw ceiling_v3/raw] [--out ceiling_v3/pool_depth.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions  # noqa: E402
from evaluation.report_ceiling import (  # noqa: E402
    load_payload_index,
    build_union_arms,
    unit_first_ranks,
    metrics_from_ranks,
)
from evaluation.resolution import FamilyMap  # noqa: E402
from evaluation.ceiling_config import DEPTHS, UNION_KG_DEPTH  # noqa: E402

DEPTHS_RUN = (100, 200, 300, 400, 500)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="evaluation/out/ceiling_v3/raw")
    parser.add_argument("--out", default="evaluation/out/ceiling_v3/pool_depth.json")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}

        raw_dir = Path(args.raw)

        def load(arm: str) -> dict[str, dict]:
            recs = {}
            p = raw_dir / f"{arm}.jsonl"
            if p.exists():
                for line in p.open(encoding="utf-8"):
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        recs[r["question_id"]] = r
            return recs

        dense = load("A_dense")
        sparse = load("B_sparse")
        kg = load("D_kg")

        results = {}
        for d in DEPTHS_RUN:
            n = 0
            r500_sum = 0.0   # mean of per-question recall@500 (report convention)
            r10_sum = 0.0
            q_pool_coverage = 0
            pool_sizes = []
            for qid, q in questions.items():
                a, b, k = dense.get(qid), sparse.get(qid), kg.get(qid)
                if not (a and b and k):
                    continue
                union = build_union_arms(
                    a, b, k, payload_index, family_map,
                    dense_n=d, sparse_n=d, kg_n=UNION_KG_DEPTH,
                )
                pool_rec = union["E_union_pool"]
                pool_sizes.append(len(pool_rec.get("fused_items", [])))
                ranks = unit_first_ranks(pool_rec, q, payload_index, family_map)
                # pool-collapse semantics: any hit => retrieved at every K
                collapsed = {pid: (1 if v is not None else None) for pid, v in ranks.items()}
                m = metrics_from_ranks(collapsed, q, DEPTHS)
                rel = q.relevant_units()
                n += 1
                r500_sum += m.get("recall@500", 0)
                r10_sum += m.get("recall@10", 0)
                unit_hits = sum(
                    1 for u in rel if collapsed.get(u.provision_id) is not None
                )
                q_pool_coverage += int(unit_hits > 0)
            results[d] = {
                "n_questions": n,
                "pool_ceiling_R500": round(r500_sum / max(n, 1), 4),
                "pool_ceiling_R10": round(r10_sum / max(n, 1), 4),
                "questions_with_gold_in_pool": round(q_pool_coverage / max(n, 1), 4),
                "mean_pool_size": round(sum(pool_sizes) / max(len(pool_sizes), 1), 1),
                "note": "R@K is the mean of per-question unit recall (report convention); "
                        "the pool is unranked, so R@10 == R@500 == pool coverage.",
            }
            print(f"depth {d:4d}: ceiling R@500={results[d]['pool_ceiling_R500']:.4f} "
                  f"q-coverage={results[d]['questions_with_gold_in_pool']:.4f} "
                  f"pool_size={results[d]['mean_pool_size']}")

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
