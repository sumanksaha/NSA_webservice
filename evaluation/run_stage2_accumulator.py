"""Stage-2 answers A/B accumulator (persistent, one qid per invocation).

The live OpenRouter free tier is bursty (~50% 429). This script runs the
harness one recovered qid at a time (2 calls), persists coverage state to
disk, and retries uncovered slots until all 44 (22 qids x 2 arms) are done.
Each invocation finishes in ~1-10 s so it survives the 30-s tool window.
"""

import sys
import time
import json
import glob
import os
sys.path.insert(0, "C:/github/NSA_webservice")
sys.path.insert(0, "C:/github/NSA_webservice/evaluation")

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path("C:/github/NSA_webservice").resolve() / ".env", override=True)

import evaluation.llm_ssl_client as _l
# Retry 429s inside a single call (exponential backoff, 1/2/4s). The free tier
# fails ~50% of first attempts; a single attempt per slot wastes invocations.
_l.MAX_ATTEMPTS = 3


ROOT = Path("C:/github/NSA_webservice/evaluation/out/ceiling_v5")
OUT = ROOT / "targeted_retry_answers_ab.json"
STAGE1 = ROOT / "targeted_retry_ab.json"
STATE = ROOT / "stage2_state.json"

#: Retries per qid per invocation. The free tier fails ~50% of first attempts;
#: a couple of retries inside one invocation covers far more than restarting.
ATTEMPTS_PER_QID = int(os.environ.get("RAG_STAGE2_ATTEMPTS", "3") or 3)

stage1 = json.load(open(STAGE1, encoding="utf-8"))
qids = stage1["recovered_qids"]
SLOTS = {(q, "base") for q in qids} | {(q, "retry") for q in qids}
master = {}
if STATE.exists():
    raw = json.load(open(STATE, encoding="utf-8"))
    master = {}
    for k, v in raw.items():
        if isinstance(k, tuple):
            master[k] = v
        else:
            parts = k.split("::", 1)
            if len(parts) == 2:
                master[(parts[0], parts[1])] = v

def merge_run():
    pths = list(glob.glob(str(ROOT / "targeted_retry_answers_ab_base_*.partial.json")))
    pths += list(glob.glob(str(ROOT / "targeted_retry_answers_ab_retry_*.partial.json")))
    print("merge scanning %d partial files" % len(pths), flush=True)
    for p in pths:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        q = d.get("qid")
        arm = "base" if "_base_" in p else "retry"
        if not (d.get("answer") or "").strip():
            continue  # only keep rows with real answers (429s are transient)
        if (q, arm) in master and (master[(q, arm)].get("answer") or "").strip():
            continue  # keep the first good answer; don't overwrite with later failure
        master[(q, arm)] = d

def run_qid(q):
    """Run harness for one qid, return 0 on success."""
    import evaluation.ab_targeted_retry_answers_fast as m
    # The harness re-derives MAX_ATTEMPTS from the env at import time.
    os.environ["RAG_LLM_MAX_ATTEMPTS"] = "3"
    m._llm_ssl.MAX_ATTEMPTS = 3
    sys.argv = ["m", "--qids", q, "--progress"]
    return m.main()

def is_covered(q, arm):
    d = master.get((q, arm))
    return bool(d and d.get("m") and (d.get("answer") or "").strip())

def save_state():
    flat = {"%s::%s" % (k[0], k[1]): v for k, v in master.items()}
    json.dump(flat, open(STATE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

# Process one uncovered qid per invocation so each run finishes
# within the tool window (~1-10 s). Guarded by __main__ so import
# never executes the network loop.
if __name__ == "__main__":
    print("DEBUG: __main__ block started", flush=True)
    # Process exactly one uncovered qid per invocation so each run finishes
    # within the tool window, persisting coverage state for the next run.
    uncovered = [q for q in qids if not is_covered(q, "base") or not is_covered(q, "retry")]
    if not uncovered:
        pass  # all covered; report built below
    else:
        q = uncovered[0]
        for attempt in range(ATTEMPTS_PER_QID):
            t0 = time.time()
            rc = run_qid(q)
            elapsed = time.time() - t0
            merge_run()
            if is_covered(q, "base") and is_covered(q, "retry"):
                print(f"qid {q} covered in {attempt+1} attempts t={elapsed:.1f}s", flush=True)
                break
            print(f"qid {q} attempt {attempt+1} t={elapsed:.1f}s covered={is_covered(q,'base')}/{is_covered(q,'retry')}", flush=True)
        save_state()
        sys.exit(0)  # done one qid; next invocation continues from state

    # --- build final report ---
    B = {q: master[(q, "base")] for q in qids if is_covered(q, "base")}
    R = {q: master[(q, "retry")] for q in qids if is_covered(q, "retry")}
    common = sorted(set(B) & set(R))
    keys = ("binary_correct", "answer_correctness", "citation_recall", "citation_precision",
            "groundedness_score", "hallucination_detected", "latency_s")
    def agg(rows, qs):
        return {k: round(sum(rows[q]["m"][k] for q in qs) / max(len(qs), 1), 4) for k in keys}
    ab, ar = agg(B, common), agg(R, common)
    flips_up = sorted(q for q in common if R[q]["m"]["binary_correct"] == 1 and B[q]["m"]["binary_correct"] == 0)
    flips_down = sorted(q for q in common if R[q]["m"]["binary_correct"] == 0 and B[q]["m"]["binary_correct"] == 1)
    # SPEC-3 eval mirror: guarded retry adoption (retry-as-fallback, not replacement).
    import evaluation.ab_targeted_retry_answers_fast as _fast
    guarded = _fast.guarded_report(B, R, common)
    summary = {
        "benchmark": "targeted_retry_stage2_ab",
        "qids": qids,
        "n_questions": len(common),
        "model": "poolside/laguna-s-2.1:free",
        "runs": len(master),
        "covered": len(master),
        "aggregate": {"baseline": ab, "retry": ar},
        "delta": {k: round(ar[k] - ab[k], 4) for k in keys},
        "flips_up": flips_up,
        "flips_down": flips_down,
        "guarded": guarded,
        "per_question": {"baseline": B, "retry": R},
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("=" * 72)
    print("Stage-2 answers A/B n=%d covered=%d/44" % (len(common), len(master)))
    print("flips up:", flips_up)
    print("flips down:", flips_down)
    print("GUARDED flips up: %s" % guarded["flips_up"])
    print("GUARDED flips down: %s" % guarded["flips_down"])
    print("GUARDED selected_retry: %s" % guarded["selected_retry"])
    for k in keys:
        print(f"{k:<24} {ab[k]:>9.4f} {ar[k]:>9.4f} {ar[k]-ab[k]:+9.4f} | guarded {guarded['aggregate'][k]:>9.4f}")
    print("written:", OUT)
