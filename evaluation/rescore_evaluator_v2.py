"""Evaluator-v2 rescoring of all stored answers (0 LLM calls).

Applies ``evaluator_v2_overlay.json`` to the frozen answer sets from
Experiments C (O3), D (D2, D3) and E (E1):

  - soft score  = max token_overlap-correctness over {v1 conclusion} ∪ {added alternatives}
                  (token_overlap / thresholds imported UNCHANGED from experiment_b_topk_eval)
  - binary      = soft > 0.5, OR abstention-credit on insufficient_evidence
                  questions (``abstention_rule.abstain_credit`` — the benchmark's
                  own "a correct abstention is the right answer" rule, which v1
                  computed as ``abstain_correct`` but never folded into
                  ``correct``; Step 0 analysis 2026-09-26). v1 binary stays
                  frozen, so every report shows both.
  - abstained   = v1 regex (UNCHANGED semantics — only the credit path uses the
                  extended lexicon), except the two overlay-listed qids where
                  reference-matching 'does not ...' phrasing was a false
                  positive (markers removed there)
  - citation metrics / groundedness / latency: untouched (not judgment-dependent)

Comparator discipline: per-condition n is identical to the frozen reports; answers,
contexts and models are unchanged - only the reference side of the comparison moves.

Outputs (evaluation/out/ceiling_v5/):
  evaluator_v2_per_question.jsonl   - per-question v1/v2 scores for all conditions
  evaluator_v2_results.json         - aggregates + audit-transition counterfactuals
"""

from __future__ import annotations

import io
import json
import re
import sys
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"
OVERLAY = ROOT / "evaluation" / "evaluator_v2_overlay.json"

# Unchanged scoring primitives (spec: evaluator stack frozen except the reference side)
from evaluation.abstention_rule import abstain_credit
from evaluation.experiment_b_topk_eval import token_overlap

_AbstainRe = re.compile(
    r"\b(i (do not|cannot|dont|can't|can not)|cannot (find|answer|determine|locate)|"
    r"no relevant|insufficient inform|unable to|not possible to|cannot be (determine|established|reliably)|"
    r"not (recorded|established|stipulated|provided|specified|mentioned|available)|"
    r"no (evidence|information|provision|specific)|does not (specify|establish|provide|state)|"
    r"the corpus does not|no provision in the corpus)\b",
    re.IGNORECASE,
)


def abstain_check_v2(answer: str, banned: set[str]) -> bool:
    """v1 abstain_check with per-qid banned markers (overlay rule)."""
    if not answer or not answer.strip():
        return True
    low = answer.lower()
    if banned:
        for m in banned:
            low = low.replace(m, " ")
    return bool(_AbstainRe.search(low))


def score(answer: str, refs: list[str], banned: set[str], *, insufficient_evidence: bool = False) -> dict:
    toks = [token_overlap(answer or "", r) for r in refs if r]
    best = max((t["correctness"] for t in toks), default=0.0)
    abstained = abstain_check_v2(answer or "", banned)
    credit = abstain_credit(answer or "", banned, insufficient_evidence)
    return {
        "soft": round(best, 4),
        # v2 binary: overlap rule + abstention credit on IE questions only
        "correct": bool(best > 0.5) or credit,
        "abstained": abstained,
        "abstain_correct": credit,
    }


# --------------------------------------------------------------------------- #
# Load artifacts
# --------------------------------------------------------------------------- #
overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
widened = overlay["widened_conclusions"]
banned_by_qid = {
    qid: set(overlay.get("banned_abstain_markers", {}).get("markers", []))
    for qid in overlay.get("banned_abstain_markers", {}).get("applies_to_qids", [])
}

bench = (
    json.load(open(OUT / "ceiling_v5" / "experiment_C_per_question.json", encoding="utf-8")) if False else None
)  # placeholder guard (unused)
bench = None
with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
    from evaluation.benchmark import load_questions

    QUESTIONS = {q.raw["question_id"]: q for q in load_questions()}


def refs_for(qid: str) -> list[str]:
    q = QUESTIONS[qid]
    refs = [q.acceptable_conclusion or ""]
    w = widened.get(qid)
    if w:
        refs += [w["add"]]
    return refs


C_PERQ = json.load(open(OUT / "experiment_C_per_question.json", encoding="utf-8"))
D_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_D_per_question.jsonl").open(encoding="utf-8"))}
E_PERQ = {r["qid"]: r for r in map(json.loads, (OUT / "experiment_E_per_question.jsonl").open(encoding="utf-8"))}
TAB = json.load(open(OUT / "human_audit_tabulation.json", encoding="utf-8"))
AUDIT = {r["qid"]: r for r in TAB["records"]}

CONDITIONS = ("C-O3", "D2", "D3", "E1")


def answers_for(qid: str) -> dict[str, str | None]:
    d = D_PERQ.get(qid, {})
    e = E_PERQ.get(qid, {}).get("E1") or {}
    return {
        "C-O3": ((C_PERQ.get(qid, {}).get("oracle_conditions", {}).get("O3_full_support") or {}).get("answer")),
        "D2": (d.get("D2") or {}).get("answer"),
        "D3": (d.get("D3") or {}).get("answer"),
        "E1": e.get("answer"),
    }


def v1_for(qid: str) -> dict[str, dict | None]:
    """Frozen v1 metrics from the per-question artifacts."""
    d = D_PERQ.get(qid, {})
    e1 = E_PERQ.get(qid, {}).get("E1") or {}
    c = C_PERQ.get(qid, {}).get("oracle_conditions", {}).get("O3_full_support") or {}
    out = {
        "C-O3": {k: c.get(k) for k in ("answer_correctness", "correct", "abstained")} if c else None,
        "D2": {k: (d.get("D2") or {}).get(k) for k in ("answer_correctness", "correct", "abstained")} or None,
        "D3": {k: (d.get("D3") or {}).get(k) for k in ("answer_correctness", "correct", "abstained")} or None,
        "E1": {k: e1.get(k) for k in ("answer_correctness", "correct", "abstained")} or None,
    }
    return out


per_q: dict[str, dict] = {}
for qid in sorted(QUESTIONS):
    refs = refs_for(qid)
    banned = banned_by_qid.get(qid, set())
    ans = answers_for(qid)
    v1 = v1_for(qid)
    ie = bool(getattr(QUESTIONS[qid], "insufficient_evidence", False))
    row: dict[str, dict] = {"qid": qid, "insufficient_evidence": ie}
    for cond in CONDITIONS:
        a = ans.get(cond)
        if not a:
            row[cond] = {"status": "not_run", "v1": v1.get(cond)}
            continue
        s2 = score(a, refs, banned, insufficient_evidence=ie)
        row[cond] = {"status": "ok", "v1": v1.get(cond), "v2": s2, "answer": a}
    per_q[qid] = row


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #
def agg(cond: str, key: str, version: str) -> tuple[float, int]:
    vals, n = [], 0
    for row in per_q.values():
        rec = row.get(cond) or {}
        if rec.get("status") != "ok":
            continue
        n += 1
        item = rec.get(version) or {}
        v = item.get(key)
        if v is not None:
            vals.append(v)
    return (round(sum(vals) / len(vals), 4) if vals else 0.0), n


results: dict = {"version": "evaluator_v2", "overlay": str(OVERLAY.name), "conditions": {}}
for cond in CONDITIONS:
    n = sum(1 for row in per_q.values() if (row.get(cond) or {}).get("status") == "ok")
    results["conditions"][cond] = {
        "n": n,
        "soft_v1": agg(cond, "answer_correctness", "v1")[0],
        "soft_v2": agg(cond, "soft", "v2")[0],
        "binary_v1": agg(cond, "correct", "v1")[0],
        "binary_v2": agg(cond, "correct", "v2")[0],
        "abstain_v2": agg(cond, "abstained", "v2")[0],
        "abstain_credited_v2": agg(cond, "abstain_correct", "v2")[0],
    }


# Near-threshold diagnostics (Step 0 analysis): a binary claim on answers
# sitting just under 0.5 is fragile — publish the band counts next to every
# binary figure so flips are never read without this context.
def bands(version: str, key: str) -> dict[str, int]:
    edges = ((0.45, 0.50), (0.40, 0.45), (0.35, 0.40))
    out = {f"[{a:.2f},{b:.2f})": 0 for a, b in edges}
    out["<0.35"] = 0
    for row in per_q.values():
        for cond in CONDITIONS:
            rec = row.get(cond) or {}
            if rec.get("status") != "ok":
                continue
            v = (rec.get(version) or {}).get(key)
            if v is None:
                continue
            for (a, b), k in zip(edges, out):
                if a <= v < b:
                    out[k] += 1
                    break
            else:
                if v < 0.35:
                    out["<0.35"] += 1
    return out


results["near_threshold"] = {
    "note": "condition-level counts; a qid can appear in several conditions",
    "soft_v1": bands("v1", "answer_correctness"),
    "soft_v2": bands("v2", "soft"),
}


# transitions under v2 (audit-based counterfactuals)
def transitions(cond_a: str, cond_b: str) -> dict:
    imp = reg = 0
    for row in per_q.values():
        a, b = row.get(cond_a) or {}, row.get(cond_b) or {}
        if a.get("status") != "ok" or b.get("status") != "ok":
            continue
        if a["v2"]["correct"] and not b["v2"]["correct"]:
            reg += 1
        if not a["v2"]["correct"] and b["v2"]["correct"]:
            imp += 1
    return {"improved": imp, "worsened": reg}


results["v2_transitions"] = {
    "C-O3_to_D2": transitions("C-O3", "D2"),
    "D2_to_D3": transitions("D2", "D3"),
    "C-O3_to_D3": transitions("C-O3", "D3"),
    "C-O3_to_E1": transitions("C-O3", "E1"),
}

# Human-audit consistency check: do v2 scores now agree with the reviewer's verdicts?
em = [r for r in TAB["records"] if r["verdict"] == "evaluator_miss"]
agree = 0
checked = 0
for r in em:
    qid = r["qid"]
    best_v2 = max((((per_q.get(qid, {}).get(c) or {}).get("v2") or {}).get("correct") or False, c) for c in CONDITIONS)
    checked += 1
    if best_v2[0]:
        agree += 1
results["audit_consistency"] = {
    "evaluator_miss_questions": checked,
    "now_machine_correct_under_best_condition": agree,
}

# III recovery counterfactual under v2 (original definition: incorrect under O1, O2, O3)
iii = {
    qid
    for qid in per_q
    if D_PERQ.get(qid, {}).get("C-O3", {}).get("correct_o1") is False
    and D_PERQ.get(qid, {}).get("C-O3", {}).get("correct_o2") is False
    and D_PERQ.get(qid, {}).get("C-O3", {}).get("correct") is False
}
results["iii_v2"] = {
    "n_iii": len(iii),
    "d2_recovered_v2": sum(1 for q in iii if (per_q[q]["D2"].get("v2") or {}).get("correct")),
    "d3_recovered_v2": sum(1 for q in iii if (per_q[q]["D3"].get("v2") or {}).get("correct")),
    "c_o3_correct_v2_within_iii": sum(1 for q in iii if (per_q[q]["C-O3"].get("v2") or {}).get("correct")),
}

# Near-miss diagnostic: evaluator-miss questions still <0.5 under every condition
results["audit_unresolved"] = []
for r in em:
    qid = r["qid"]
    best = max((((per_q.get(qid, {}).get(c) or {}).get("v2") or {}).get("soft") or 0.0, c) for c in CONDITIONS)
    if not best[0] > 0.5:
        results["audit_unresolved"].append({"qid": qid, "best_v2_soft": best[0], "best_cond": best[1]})

with (OUT / "evaluator_v2_per_question.jsonl").open("w", encoding="utf-8") as f:
    for qid in sorted(per_q):
        f.write(json.dumps({"qid": qid, **per_q[qid]}, ensure_ascii=False) + "\n")
(OUT / "evaluator_v2_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

print(json.dumps(results["conditions"], indent=1))
print("near-threshold bands:", json.dumps(results["near_threshold"], indent=1))
print("v2 transitions:", results["v2_transitions"])
print("audit consistency:", results["audit_consistency"])
print("audit unresolved (still <0.5):", results["audit_unresolved"])
print("III v2:", results["iii_v2"])
print("wrote evaluator_v2_per_question.jsonl + evaluator_v2_results.json")
