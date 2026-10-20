"""Paired analysis: thinking-budget variants vs the LAGUNA baseline.

The 150-run measured minimax against laguna on truncated reasoning traces.
This asks the question that matters for the fix: under each thinking budget,
does the model still beat laguna on the same 24 questions?
"""

import io
import json
import math
import os
import statistics as st
import sys
from pathlib import Path

REPO = r"C:\github\NSA_webservice"
sys.path.insert(0, REPO)
os.chdir(REPO)
from dotenv import load_dotenv

load_dotenv(Path(REPO) / ".env")
os.environ["APP_LAZY_IMPORT"] = "true"
os.environ["HF_HUB_OFFLINE"] = "1"

from evaluation import ab_model  # noqa: E402

OUT = Path(REPO) / "evaluation" / "out" / "ceiling_v5" / "reeval_89"


def load(path):
    if not path.exists():
        return []
    out = []
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


rows = [r for r in load(OUT / "reasoning_exp.jsonl")]

TOP8 = {"Q130", "Q031", "Q118", "Q058", "Q111", "Q053", "Q124", "Q033"}
MUST4 = {"Q052", "Q015", "Q047", "Q002"}
IE6 = {"Q133", "Q135", "Q128", "Q098", "Q042", "Q060"}
FAIL6 = {"Q016", "Q014", "Q040", "Q007", "Q025", "Q032"}


def p_two(t):
    x = abs(t)
    p = 0.3275911
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    s = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * s + a4) * s) + a3) * s + a2) * s + a1) * s * 2 ** (-x * x)
    return max(0.0, min(1.0, 2.0 * (1.0 - y)))


print("=" * 100)
print("PAIRED vs LAGUNA (n=24: 8 top-gain + 4 must-hold + 6 IE + 6 failure)")
print("=" * 100)

for variant in ("think512", "think1k", "think2k"):
    got = {r["qid"]: r for r in rows if r["variant"] == variant and not r.get("error")}
    pairs = []
    for qid, r in got.items():
        b = ab_model.laguna_base(qid)
        if b and not b.get("error") and b.get("answer_correctness") is not None:
            pairs.append((qid, b, r))
    if not pairs:
        continue
    d = [float(r["answer_correctness"]) - float(b["answer_correctness"]) for _, b, r in pairs]
    n = len(d)
    mean = st.mean(d)
    sd = st.stdev(d) if n > 1 else 0.0
    t = mean / (sd / n**0.5) if sd else 0.0
    print(
        f"\n{variant}: n={n}  soft {st.mean([float(b['answer_correctness']) for _, b, _ in pairs]):.4f}"
        f" -> {st.mean([float(r['answer_correctness']) for _, _, r in pairs]):.4f}"
        f"  delta {mean:+.4f} (t={t:+.2f}, p~{p_two(t):.3f};"
        f" {sum(1 for x in d if x > 0.0005)}W/{sum(1 for x in d if x < -0.0005)}L)"
    )
    print(
        f"  bin {sum(1 for _, b, _ in pairs if b.get('binary_correct'))}"
        f" -> {sum(1 for _, _, r in pairs if r.get('binary_correct'))}"
        f"  credit {sum(1 for _, b, _ in pairs if b.get('binary_correct_abstention_credit'))}"
        f" -> {sum(1 for _, _, r in pairs if r.get('binary_correct_abstention_credit'))}"
        f"  elim {sum(1 for _, b, _ in pairs if b.get('eliminated'))}"
        f" -> {sum(1 for _, _, r in pairs if r.get('eliminated'))}"
        f"  len {st.mean([len(b.get('answer') or '') for _, b, _ in pairs]):.0f}"
        f" -> {st.mean([r['answer_length'] for _, _, r in pairs]):.0f}"
    )
    for label, subset in (
        ("top8", TOP8),
        ("must-hold4", MUST4),
        ("IE6", IE6),
        ("failure6", FAIL6),
    ):
        sub = [(q, b, r) for q, b, r in pairs if q in subset]
        if not sub:
            continue
        sd_ = [float(r["answer_correctness"]) - float(b["answer_correctness"]) for _, b, r in sub]
        print(
            f"    {label:12} n={len(sub):2}  {st.mean([float(b['answer_correctness']) for _, b, _ in sub]):.3f}"
            f" -> {st.mean([float(r['answer_correctness']) for _, _, r in sub]):.3f}"
            f"  ({st.mean(sd_):+.4f}; {sum(1 for x in sd_ if x > 0.0005)}W/{sum(1 for x in sd_ if x < -0.0005)}L)"
        )
    print("    per-QID (|delta|>0.03 or flip):")
    for q, b, r in pairs:
        ds = float(r["answer_correctness"]) - float(b["answer_correctness"])
        if abs(ds) > 0.03 or bool(b.get("binary_correct")) != bool(r.get("binary_correct")):
            print(
                f"      {q}: {float(b['answer_correctness']):.3f} ->"
                f" {float(r['answer_correctness']):.3f} ({ds:+.3f})"
                f"  bin {bool(b.get('binary_correct'))}->{bool(r.get('binary_correct'))}"
                f"  finish={r.get('finish_reason')}"
            )
