"""Re-render D3 checkpoint answers with the fixed build_final_answer.

Zero LLM calls: rebuilds each record's answer deterministically from the
stored structured analysis + audit artifacts, reconstructing the context
exactly as run_d3_one did so citation-marker clamping matches the run.
Idempotent; edits the D3 checkpoint in place (after a .bak backup).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ["RAG_EVAL_SKIP_PLOTS"] = "1"
try:  # matplotlib shim for offline environments (same pattern as the D module)
    import matplotlib  # noqa: F401
except Exception:
    import types

    for _name in ("matplotlib", "matplotlib.pyplot", "matplotlib.figure"):
        sys.modules.setdefault(_name, types.ModuleType(_name))
    sys.modules["matplotlib"].pyplot = sys.modules["matplotlib.pyplot"]

from evaluation.experiment_d_reasoning_eval import (
    D_D3_CKPT,
    MAX_CTX_CHARS,
    O3_COND,
    _COracleContextBuilder,
    build_final_answer,
    load_c_questions,
    normalize_audit,
    to_retrieved_chunk,
)


def main() -> int:
    ckpt = Path(D_D3_CKPT)
    if not ckpt.exists():
        print(f"D3 checkpoint not found: {ckpt}")
        return 1

    from evaluation.eval_e2e_v2 import load_payload_index

    manifest_q = load_c_questions()
    payload_index = load_payload_index()

    backup = ckpt.with_suffix(".jsonl.bak")
    shutil.copy2(ckpt, backup)
    print(f"backup: {backup}")

    lines = [l for l in ckpt.read_text(encoding="utf-8").splitlines() if l.strip()]
    records = [json.loads(l) for l in lines]

    # Last-wins dedupe, preserving checkpoint semantics.
    seen: dict[str, dict] = {}
    order: list[str] = []
    for r in records:
        qid = r.get("question_id")
        if qid not in seen:
            order.append(qid)
        seen[qid] = r

    n_fixed = 0
    out_lines = []
    for qid in order:
        r = seen[qid]
        audit = r.get("audit")
        analysis = r.get("analysis")
        if r.get("answer") and audit and analysis:
            entry = manifest_q.get(qid)
            if entry is None:
                print(f"  !! {qid}: not in manifest, leaving answer untouched")
                out_lines.append(json.dumps(r, ensure_ascii=False))
                continue
            cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
            chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
            cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
            built = cb.build(entry["question"], chunks, "general_qa")
            max_index = len(built.citations or [])
            old = r["answer"]
            new = build_final_answer(analysis, normalize_audit(audit), max_index)
            if new != old:
                n_fixed += 1
            r["answer"] = new
            r["rerendered"] = True
        out_lines.append(json.dumps(r, ensure_ascii=False))

    ckpt.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"records: {len(out_lines)} | answers re-rendered: {n_fixed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
