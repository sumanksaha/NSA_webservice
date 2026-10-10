import json
import logging
import os
import time

# Suppress framework warnings that trip shell error capture
logging.getLogger("app").setLevel(logging.CRITICAL)
logging.getLogger("qstash_client").setLevel(logging.CRITICAL)
logging.getLogger("app.core").setLevel(logging.CRITICAL)
from concurrent.futures import ThreadPoolExecutor, as_completed

from evaluation.bench_p0_1_packing import _load_payload_index, run_arm
from evaluation.benchmark import load_questions
from evaluation.resolution import FamilyMap

OUT = "evaluation/out/ceiling_v5/p0_1_packing_sweep.json"
pi = _load_payload_index()
qs = load_questions()
qs_by_id = {q.question_id: q for q in qs}
fm = FamilyMap()
arms = ("A_dense", "B_sparse", "C_hybrid", "D_kg")
sweep = [(5, 10000), (10, 10000), (15, 10000), (20, 24000)]
os.makedirs("evaluation/out/ceiling_v5", exist_ok=True)
results = []
lock = __import__("threading").Lock()


def one(args):
    arm, maxc, chars = args
    r = run_arm(arm, pi, qs_by_id, fm, max_chunks=maxc, max_context_chars=chars)
    r["run_at"] = time.time()
    with lock:
        results.append(r)
        print(
            "[OK] %s mc=%2d chars=%d -> goldBase=%.4f goldPack=%.4f prim1st=%.4f changed=%.4f esFit=%.4f"
            % (
                arm,
                maxc,
                chars,
                r["baseline_gold_rate"],
                r["packed_gold_rate"],
                r["packed_primary_first_rate"],
                r["prompt_changed_rate"],
                r["evidence_set_fit_rate"],
            )
        )


with ThreadPoolExecutor(max_workers=4) as ex:
    futures = [ex.submit(one, (a, c, cc)) for a in arms for c, cc in sweep]
    for _ in as_completed(futures):
        pass

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("WROTE", OUT)
print("n_run =", len(results))
