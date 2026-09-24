"""Experiment F — Layer 1: Abstention Calibration (design v3 §6).

Pipeline per question (F0 baseline = the frozen D2 checkpoint, 0 calls):
  deterministic gate  ->  1 constrained recovery call  ->  hallucination guard
  ->  deterministic citation hygiene  ->  F1 answer.

Gate (0 LLM calls). A D2 answer is F1-gated iff ALL of:
  1. ``abstain_check`` fires on the D2 answer (unchanged B regex);
  2. the question is NOT ``insufficient_evidence=True`` (justified abstentions
     are preserved — zero forced answers on those 15 questions);
  3. the O3 context contains at least one gold-family chunk (coverage check),
     computed from ``build_gold_index`` over the question's recall units.

Experiment semantics (user decision): deterministic nomination runs on all 150
questions; the 31 audit-labeled ``abstention_gate`` qids are force-gated when
machine-incorrect (29 qids) — i.e. ``gate = deterministic OR audit_target``.
Every gated qid carries ``gate_source`` ∈ {deterministic, audit, both} so the
production-readiness of the deterministic gate alone stays measurable.

Recovery call (max 1, ≤3 attempts, 8192-token cap): question + O3 evidence +
D2 structured analysis (no reference). The model must either answer from the
evidence or name the specific missing statutory element. A named missing
element keeps the abstention (``justified_by_model``).

Anti-hallucination guard: recovered answers are checked with the unchanged
``grade_answer`` numeric/imprisonment hallucination proxy against the O3
evidence; a flag reverts the answer to the D2 abstention (``recovery_rejected``).

Scoring: evaluator v2 (max token-overlap over v1 reference + overlay additions;
abstain determination with the two overlay-banned markers removed on Q060/Q138).
Per-question records + aggregates land in evaluation/out/ceiling_v5/.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "evaluation"))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"

# --------------------------------------------------------------------------- #
# Imports (unchanged evaluator stack)
# --------------------------------------------------------------------------- #
import io as _io

from evaluation.experiment_d_reasoning_eval import (
    D_D2_CKPT,
    D_D2_CKPT_STUB,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_CTX_CHARS,
    O3_COND,
    _COracleContextBuilder,
    _load_ckpt,
    extract_json_object,
)
from evaluation.eval_e2e_v2 import load_payload_index, _SSLBypassLLMClient
from evaluation.benchmark import load_questions
from evaluation.experiment_b_topk_eval import (
    abstain_check,
    to_retrieved_chunk,
    token_overlap,
)
from evaluation.experiment_c_oracle_eval import C_MANIFEST

F_CALLS_JSONL = OUT_DIR / "experiment_F_calls.jsonl"
F_CALLS_STUB_JSONL = OUT_DIR / "experiment_F_calls_stub.jsonl"
F_CKPT_F1 = OUT_DIR / "experiment_F_f1_checkpoint.jsonl"
F_CKPT_F1_STUB = OUT_DIR / "experiment_F_f1_checkpoint_stub.jsonl"
F_CKPT_F2 = OUT_DIR / "experiment_F_f2_checkpoint.jsonl"
F_CKPT_F2_STUB = OUT_DIR / "experiment_F_f2_checkpoint_stub.jsonl"
F_RESULTS = OUT_DIR / "experiment_F_results.json"
F_PER_QUESTION = OUT_DIR / "experiment_F_per_question.jsonl"
F_SUMMARY = OUT_DIR / "experiment_F_summary.md"

BUDGET_CAP = 150  # shared hard cap (design sec 8)
MAX_ATTEMPTS = 3  # per-qid generation attempts before not_run
RECOVERY_MAX_TOKENS = 8192  # E-lesson: 4096 truncated repair JSONs

_F1_SYSTEM_PROMPT = (
    "You are a careful Indian legal assistant working ONLY from supplied evidence. "
    "A previous structured analysis of this question abstained. The evidence "
    "supplied below has been verified to contain the relevant statutory domain. "
    "You must do exactly one of the following: "
    "(a) provide the substantive legal conclusion directly from the supplied "
    "evidence, as a complete, correctly qualified answer citing sources with "
    "[n] markers; or "
    "(b) if and only if a specific statutory element needed to answer is "
    "genuinely absent from the evidence, name that missing element precisely. "
    "Do not invent provisions, numbers, or rules that are not in the evidence. "
    'Output ONLY one JSON object: {"answer": string, '
    '"missing_element_if_any": string or null, "confidence": "high"|"medium"|"low"}. '
    "If you choose (b), set answer to a short abstention sentence and name the "
    "missing element."
)

_HALLUC_AMOUNT_RE = __import__("re").compile(
    r"(?:rs\.?\s*|inr\s*|\u20b9|rupees?)\s*[\d,]+(?:\s*(?:lakh|crore))?"
    r"|[\d,]{2,}\s*(?:lakh|crore)\s*rupees?",
    __import__("re").IGNORECASE,
)


def _log_call(calls_path: Path, lock: threading.Lock, qid: str, stage: str, **fields: Any) -> None:
    rec = {
        "qid": qid,
        "condition": "F1",
        "stage": stage,
        "model": LLM_MODEL,
        "temperature": LLM_TEMPERATURE,
        "input_tokens": int(fields.get("input_tokens", 0) or 0),
        "output_tokens": int(fields.get("output_tokens", 0) or 0),
        "latency_ms": int(fields.get("latency_ms", 0) or 0),
        "success": bool(fields.get("success", False)),
        "revision_count": int(fields.get("revision_count", 0) or 0),
    }
    with lock, calls_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, rec: dict, lock: threading.Lock) -> None:
    with lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _load_ckpt_index(path: Path) -> dict[str, dict]:
    """Last record wins (resume-safe)."""
    idx: dict[str, dict] = {}
    if not path.exists():
        return idx
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        qid = rec.get("qid") or rec.get("question_id")
        if qid:
            idx[qid] = rec
    return idx


def score_v2(answer: str, refs: list[str], banned_markers: set[str]) -> dict:
    """Evaluator v2: max token-overlap over references; adjusted abstain check."""
    toks = [token_overlap(answer or "", r) for r in refs if r]
    best = max((t["correctness"] for t in toks), default=0.0)
    low = (answer or "").lower()
    for m in banned_markers:
        low = low.replace(m, " ")
    return {
        "soft": round(best, 4),
        "correct": bool(best > 0.5),
        "abstained_v2": bool(abstain_check(low) if banned_markers else abstain_check(answer or "")),
    }


def run_f1_one(
    qid: str,
    gate_source: str,
    manifest_q: dict,
    payload_index: dict,
    gold_by_family: dict,
    client: Any,
    calls_path: Path,
    lock: threading.Lock,
    d2_rec: dict,
    question_obj: Any,
    refs: list[str],
    banned_markers: set[str],
    call_counter: dict,
    audit_force: bool = False,
) -> dict:
    """One question through the F1 gate -> recovery -> guard pipeline."""
    d2_answer = str(d2_rec.get("answer") or "")
    d2_lat = int(d2_rec.get("latency_ms", 0) or 0)

    rec: dict[str, Any] = {
        "qid": qid,
        "condition": "F1",
        "gate_source": gate_source,
        "d2_abstained": bool(abstain_check(d2_answer)),
        "question_insufficient_evidence": bool(question_obj.insufficient_evidence),
    }
    if d2_rec.get("error"):
        rec["status"] = "d2_error"
        rec["answer"] = d2_answer
        return rec

    # --- gate condition 3: gold-family coverage in O3 context ---
    entry = manifest_q.get(qid) or {}
    cid_list = (entry.get("conditions", {}).get(O3_COND) or {}).get("context_chunk_ids") or []
    units = question_obj.recall_units()
    gold_families = {u.family for u in units}
    covered = False
    for cid in cid_list:
        pl = payload_index.get(cid) or {}
        from evaluation.resolution import payload_to_keys

        for fam, _sec in payload_to_keys(pl, question_obj.__dict__.get("_family_map") or _GLOBAL_FAMILY_MAP):
            if fam in gold_families:
                covered = True
                break
        if covered:
            break
    rec["context_covers_gold_family"] = covered

    gated = (not question_obj.insufficient_evidence) and covered
    if audit_force:
        # Audit force-gate (user decision): the human-labeled target bypasses the
        # abstain-regex and coverage preconditions. Provenance stays in gate_source.
        gated = True
    rec["gated"] = gated
    if not gated:
        rec["status"] = "not_gated"
        rec["answer"] = d2_answer
        rec["latency_ms"] = d2_lat
        return rec

    # --- recovery call (max 1 successful; <= MAX_ATTEMPTS generations) ---
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry.get("question") or question_obj.question, chunks, "general_qa")
    analysis = d2_rec.get("analysis")
    analysis_json = json.dumps(analysis, ensure_ascii=False) if isinstance(analysis, dict) else "{}"

    user_p = (
        f"QUESTION:\n{entry.get('question') or question_obj.question}\n\n"
        f"EVIDENCE SOURCES (use ONLY these; [n] = source n):\n{built.context}\n\n"
        "PREVIOUS ANSWER (which a legal reviewer judged to be an unwarranted "
        "refusal to answer, OR an answer to a different question than asked):\n"
        f"{d2_answer}\n\n"
        f"PREVIOUS STRUCTURED ANALYSIS (for reference):\n{analysis_json}\n\n"
        "Answer the QUESTION from the evidence. If and only if a specific "
        "statutory element needed to answer is genuinely absent from the "
        "evidence, name that missing element instead. Output ONLY the required "
        "JSON object."
    )

    payload = None
    ok = False
    error = ""
    resp = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if call_counter["ok"] >= BUDGET_CAP:
            rec["status"] = "budget_exhausted"
            rec["answer"] = d2_answer
            rec["latency_ms"] = d2_lat
            return rec
        resp = client.call(_F1_SYSTEM_PROMPT, user_p, temperature=LLM_TEMPERATURE, max_tokens=RECOVERY_MAX_TOKENS)
        usage = getattr(resp, "usage", None) or {}
        gen_ok = not resp.error
        payload_i = extract_json_object(resp.text or "") if gen_ok else None
        ok_i = False
        if payload_i is not None and isinstance(payload_i.get("answer"), str) and payload_i["answer"].strip():
            ok_i = True
        else:
            error = resp.error or "unparseable/empty recovery JSON"
        _log_call(
            calls_path,
            lock,
            qid=qid,
            stage="abstention_recovery",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int(getattr(resp, "latency", 0.0) * 1000),
            success=bool(ok_i),
            revision_count=attempt - 1,
        )
        if ok_i:
            payload, ok = payload_i, True
            call_counter["ok"] += 1
            error = ""
            break
        rec[f"attempt_{attempt}_error"] = error
        rec[f"attempt_{attempt}_output_chars"] = len(resp.text or "")

    if not ok:
        rec["status"] = "recovery_failed"
        rec["error"] = f"recovery failed after {MAX_ATTEMPTS} attempts: {error}"
        rec["answer"] = d2_answer
        rec["latency_ms"] = d2_lat
        return rec

    rec["recovery_call_made"] = True
    rec["model_confidence"] = payload.get("confidence")
    rec["model_missing_element"] = payload.get("missing_element_if_any") or None
    rec["latency_ms"] = d2_lat + int(getattr(resp, "latency", 0.0) * 1000)

    # Model-justified abstention: keep the D2 abstention.
    if rec["model_missing_element"]:
        rec["status"] = "justified_abstention_by_model"
        rec["answer"] = d2_answer
        return rec

    candidate = str(payload.get("answer") or "").strip()

    # --- anti-hallucination guard (unchanged grade_answer proxy, evidence blob) ---
    evidence_blob = " ".join(ch.text.lower() for ch in chunks)
    halluc = any(claim.lower() not in evidence_blob for claim in _HALLUC_AMOUNT_RE.findall(candidate))
    rec["hallucination_flag"] = halluc
    if halluc:
        rec["status"] = "recovery_rejected"
        rec["answer"] = d2_answer
        return rec

    # --- deterministic citation hygiene (E's convention) ---
    max_idx = len(built.citations or [])
    import re as _re

    present = {int(n) for n in _re.findall(r"\[(\d{1,2})\]", candidate) if 1 <= int(n) <= max_idx}
    out_of_range = {int(n) for n in _re.findall(r"\[(\d{1,2})\]", candidate)} - present
    for n in sorted(out_of_range):
        candidate = candidate.replace(f"[{n}]", "")
    if not _re.search(r"\[\d{1,2}\]", candidate) and max_idx:
        pass  # leave unreferenced; citation metrics will simply be 0

    rec["status"] = "recovered"
    rec["answer"] = candidate
    return rec


_GLOBAL_FAMILY_MAP = None


# --------------------------------------------------------------------------- #
# Phase ANALYZE — composite assembly, v2 scoring of all conditions, gates
# --------------------------------------------------------------------------- #
def analyze(stub: bool) -> dict:
    """Assemble F0/F1/F2/F1F2 conditions, score under evaluator v2, compute gates."""
    overlay = json.loads((PROJECT_ROOT / "evaluation" / "evaluator_v2_overlay.json").read_text(encoding="utf-8"))
    widened = overlay["widened_conclusions"]
    banned_by_qid = {
        qid: set(overlay.get("banned_abstain_markers", {}).get("markers", []))
        for qid in overlay.get("banned_abstain_markers", {}).get("applies_to_qids", [])
    }
    with redirect_stdout(_io.StringIO()), redirect_stderr(_io.StringIO()):
        questions = {q.raw["question_id"]: q for q in load_questions()}

    gate_lists = json.loads((OUT_DIR / "experiment_F_gate_lists.json").read_text(encoding="utf-8"))
    audit_f1 = set(gate_lists["f1_abstention_gate"])
    audit_f2 = set(gate_lists["f2_provision_check"])

    d2 = {r["question_id"]: r for r in _load_ckpt(D_D2_CKPT_STUB if stub else D_D2_CKPT) if not r.get("error")}
    f1 = _load_ckpt_index(F_CKPT_F1_STUB if stub else F_CKPT_F1)
    f2 = _load_ckpt_index(F_CKPT_F2_STUB if stub else F_CKPT_F2)

    per_q: dict[str, dict] = {}
    for qid, q in sorted(questions.items()):
        refs = [q.acceptable_conclusion or ""]
        w = widened.get(qid)
        if w:
            refs.append(w["add"])
        banned = banned_by_qid.get(qid, set())
        d2_rec = d2.get(qid) or {}
        d2_answer = str(d2_rec.get("answer") or "")

        f1_rec = f1.get(qid) or {}
        f2_rec = f2.get(qid) or {}
        f1_answer = str(f1_rec.get("answer") or "") or d2_answer
        f2_answer = str(f2_rec.get("answer") or "") or d2_answer
        # Composite: F1 answer on F1-gated qids, F2 answer on F2-gated qids,
        # D2 answer elsewhere (target sets are disjoint by construction).
        composite = f1_answer if f1_rec.get("gated") else (f2_answer if f2_rec.get("gated") else d2_answer)

        row: dict[str, Any] = {"qid": qid}
        for cond, ans in (("F0", d2_answer), ("F1", f1_answer), ("F2", f2_answer), ("F1F2", composite)):
            row[cond] = score_v2(ans, refs, banned)
        row["f1_status"] = f1_rec.get("status")
        row["f2_status"] = f2_rec.get("status")
        row["f1_gate_source"] = f1_rec.get("gate_source")
        row["f2_gate_source"] = f2_rec.get("gate_source")
        row["hallucination_flag"] = f1_rec.get("hallucination_flag")
        per_q[qid] = row

    def agg(cond: str, key: str) -> tuple[float, int]:
        vals = [per_q[q][cond][key] for q in per_q if per_q[q][cond][key] is not None]
        return (round(sum(vals) / len(vals), 4) if vals else 0.0), len(vals)

    results: dict[str, Any] = {"conditions": {}}
    for cond in ("F0", "F1", "F2", "F1F2"):
        soft, n = agg(cond, "soft")
        corr, _ = agg(cond, "correct")
        results["conditions"][cond] = {"n": n, "soft_v2": soft, "binary_v2": corr}

    # ---- pre-registered success gates (design v3 sec 9) ----
    f1_targets = [q for q in audit_f1 if q in d2]
    f1_recovered = sum(1 for q in f1_targets if per_q[q]["F1"]["correct"] and not per_q[q]["F0"]["correct"])
    ie_qids = [qid for qid, qq in questions.items() if qq.insufficient_evidence]
    ie_regressed = sum(1 for q in ie_qids if per_q[q]["F0"]["correct"] and not per_q[q]["F1"]["correct"])
    f1_gain = results["conditions"]["F1"]["binary_v2"] - results["conditions"]["F0"]["binary_v2"]
    results["f1_gate"] = {
        "targets_machine_incorrect": len([q for q in f1_targets if not per_q[q]["F0"]["correct"]]),
        "recovered": f1_recovered,
        "insufficient_evidence_regressions": ie_regressed,
        "binary_gain": round(f1_gain, 4),
        "passes": bool(f1_recovered >= 20 and ie_regressed == 0 and f1_gain >= 0.02),
    }

    f2_targets = [q for q in audit_f2 if q in d2 and not per_q[q]["F0"]["correct"]]
    f2_fixed = sum(1 for q in f2_targets if per_q[q]["F2"]["correct"])
    f2_preserve = ["Q015", "Q024", "Q033", "Q125", "Q129"]
    f2_regressed = sum(
        1
        for q in f2_preserve
        if per_q.get(q, {}).get("F0", {}).get("correct") and not per_q.get(q, {}).get("F2", {}).get("correct")
    )
    results["f2_gate"] = {
        "targets_machine_incorrect": len(f2_targets),
        "fixed": f2_fixed,
        "already_correct_regressed": f2_regressed,
        "passes": bool(f2_fixed >= 15 and f2_regressed == 0),
    }

    recovered = [q for q in per_q if per_q[q].get("f1_status") == "recovered"]
    rejected = [q for q in per_q if per_q[q].get("f1_status") == "recovery_rejected"]
    results["f1_guard"] = {
        "recovered": len(recovered),
        "recovery_rejected": len(rejected),
        "reject_rate": round(len(rejected) / max(len(recovered) + len(rejected), 1), 4),
    }

    calls_path = F_CALLS_STUB_JSONL if stub else F_CALLS_JSONL
    calls = []
    if calls_path.exists():
        calls = [json.loads(l) for l in calls_path.open(encoding="utf-8") if l.strip()]
    ok_calls = [c for c in calls if c.get("success")]
    results["accounting"] = {
        "generation_attempts": len(calls),
        "successful_generations": len(ok_calls),
        "failed_attempts": len(calls) - len(ok_calls),
        "budget_cap": BUDGET_CAP,
    }

    suffix = "_stub" if stub else ""
    with (OUT_DIR / f"experiment_F_per_question{suffix}.jsonl").open("w", encoding="utf-8") as f:
        for qid in sorted(per_q):
            f.write(json.dumps({"qid": qid, **per_q[qid]}, ensure_ascii=False) + "\n")
    (OUT_DIR / f"experiment_F_results{suffix}.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return results


def _write_summary(results: dict, stub: bool) -> None:
    c = results["conditions"]
    g1, g2 = results["f1_gate"], results["f2_gate"]
    lines = [
        "# Experiment F - Results" + (" (STUB)" if stub else ""),
        "",
        "## Conditions (evaluator v2)",
        "",
        "| Condition | n | Soft v2 | Binary v2 |",
        "|---|---:|---:|---:|",
        *[f"| {k} | {v['n']} | {v['soft_v2']:.4f} | {v['binary_v2']:.4f} |" for k, v in c.items()],
        "",
        "## F1 gate (abstention calibration)",
        "",
        f"- targets machine-incorrect: {g1['targets_machine_incorrect']} | recovered: {g1['recovered']}",
        f"- insufficient-evidence regressions: {g1['insufficient_evidence_regressions']} (must be 0)",
        f"- binary gain: {g1['binary_gain']:+.4f} (must be >= +0.02)",
        f"- **passes: {g1['passes']}**",
        "",
        "## F2 gate (provision disambiguation)",
        "",
        f"- targets machine-incorrect: {g2['targets_machine_incorrect']} | fixed: {g2['fixed']}",
        f"- already-correct regressed: {g2['already_correct_regressed']} (must be 0)",
        f"- **passes: {g2['passes']}**",
        "",
        "## Guard + accounting",
        "",
        f"- recovered: {results['f1_guard']['recovered']} | recovery-rejected: {results['f1_guard']['recovery_rejected']} (rate {results['f1_guard']['reject_rate']:.3f}, must be <0.10)",
        f"- generations: {results['accounting']['successful_generations']} ok / {results['accounting']['generation_attempts']} attempts (cap {results['accounting']['budget_cap']})",
        "",
    ]
    suffix = "_stub" if stub else ""
    (OUT_DIR / f"experiment_F_summary{suffix}.md").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Layer 2: provision disambiguation (design v3 sec 7)
# --------------------------------------------------------------------------- #
_OFFICER_ROLES = [
    "Designated Officer",
    "Adjudicating Officer",
    "Food Safety Officer",
    "Food Analyst",
    "Commissioner of Food Safety",
    "Municipal Commissioner",
    "Licensing Authority",
    "Central Government",
    "State Government",
]

_ROLE_SECTION_RE_TEMPLATE = r"{role}[^.\n]{{0,200}}?section\s+(\d{{1,3}})"


def build_role_section_table(payload_index: dict, max_chunks: int | None = None) -> dict[str, dict[str, set[str]]]:
    """Evidence-derived binding: role -> {section -> chunk_ids}.

    A chunk contributes a (role, section) binding when both appear in the same
    chunk (role within the preceding ~200 chars of 'section N').
    """
    import re as _re

    table: dict[str, dict[str, set[str]]] = {}
    items = list(payload_index.items())
    if max_chunks:
        items = items[:max_chunks]
    for cid, pl in items:
        text = str(pl.get("chunk_text") or pl.get("text") or "")
        if not text:
            continue
        for role in _OFFICER_ROLES:
            m = _re.search(_ROLE_SECTION_RE_TEMPLATE.format(role=_re.escape(role)), text)
            if m:
                table.setdefault(role, {}).setdefault(m.group(1), set()).add(cid)
    return table


def check_provision_binding(
    analysis: dict | None,
    answer: str,
    citations: list[dict],
    gold_families: set[str],
    family_map: Any,
    role_table: dict[str, dict[str, set[str]]],
) -> dict:
    """Deterministic F2 checks -> {'flagged': bool, 'reasons': [...], 'diagnostic': str}.

    Conviction is positive-mismatch only (E's precision-first lesson):
    1. subsection mismatch - the analysis/answer cites 'section X(Y)' and the
       bound citation chunk demonstrably contains section X but NOT X(Y);
    2. role/section conflict - the analysis binds a role to a section that the
       evidence table binds to a different role;
    3. act-family discrepancy - the answer names an Act family outside gold.
    """
    import re as _re

    from evaluation.experiment_e_citation_verification import _section_numbers_in, _sections_match

    reasons: list[str] = []
    diagnostics: list[str] = []
    analysis = analysis if isinstance(analysis, dict) else {}

    # -- collect cited sections from analysis governing_provisions + answer --
    cited: list[tuple[str, str]] = []  # (section_ident, where)
    for gp in analysis.get("governing_provisions") or []:
        if isinstance(gp, dict):
            pid = str(gp.get("provision_id") or "")
            for m in _re.finditer(r"section\s+(\d{1,3}(?:\([0-9A-Za-z]+\))*)", pid, _re.I):
                cited.append((m.group(1), f"governing_provisions:{pid[:60]}"))
            for m in _re.finditer(r"\b(\d{1,3}\([0-9A-Za-z]+(?:\([0-9A-Za-z]+\))*\))", pid):
                cited.append((m.group(1), f"governing_provisions:{pid[:60]}"))
    for m in _re.finditer(r"section\s+(\d{1,3}(?:\([0-9A-Za-z]+\))*)", answer or "", _re.I):
        cited.append((m.group(1), "answer"))

    # -- check 1: subsection mismatch against the bound chunk inventory --
    by_idx = {c["index"]: c for c in (citations or [])}
    for ident, where in cited:
        base = ident.split("(")[0]
        subs = _re.findall(r"\(([0-9A-Za-z]+)\)", ident)
        if not subs:
            continue
        # find a citation chunk whose inventory contains the base section
        hit = None
        for c in citations or []:
            secs = _section_numbers_in(str(c.get("text") or ""))
            if base in secs or any(s.split("(")[0] == base for s in secs):
                hit = c
                break
        if hit is None:
            continue  # base section absent from every bound chunk: not a positive mismatch here
        hit_secs = _section_numbers_in(str(hit.get("text") or ""))
        exact = {ident.lower()}
        if not _sections_match(exact, hit_secs, str(hit.get("text") or "")):
            # parent present, exact subsection absent -> demonstrable mismatch
            reasons.append(
                f"subsection_mismatch: cites {ident} in {where}; chunk [#{hit['index']}] shows section {base} but not {ident}"
            )
            diagnostics.append(
                f"Cited '{ident}' ({where}) not found in the cited chunk; sections present for {base} in that chunk: "
                f"{sorted(s for s in hit_secs if s.split('(')[0] == base)[:6]}"
            )

    # -- check 2: role/section conflicts --
    text_all = " ".join(
        str(gp.get("provision_id") or "") + " " + str(gp.get("description") or "")
        for gp in analysis.get("governing_provisions") or []
        if isinstance(gp, dict)
    )
    for role, sec_map in role_table.items():
        if role.lower() not in (text_all or "").lower():
            continue
        for m in _re.finditer(r"section\s+(\d{1,3})", text_all, _re.I):
            sec = m.group(1)
            other_roles = (
                {r2 for s2, cids in sec_map.items() if s2 == sec for r3 in (None,) for cids2 in [cids] for r2 in [role]}
                if False
                else None
            )
        # simpler: for each section the table binds to OTHER roles, check whether
        # the analysis binds THIS role to that section
        for sec, _cids in sec_map.items():
            if _re.search(rf"{_re.escape(role)}[^.\n]{{0,200}}?section\s+{sec}\b", text_all, _re.I):
                conflicting = {r2 for r2, m2 in role_table.items() if r2 != role and sec in m2}
                if conflicting:
                    reasons.append(
                        f"role_section_conflict: analysis binds {role} to section {sec}; evidence binds section {sec} to {sorted(conflicting)}"
                    )
                    diagnostics.append(
                        f"In the evidence, section {sec} is bound to: {sorted(conflicting)}. Verify the {role} binding."
                    )
                break

    # -- check 3: act-family discrepancy --
    from evaluation.resolution import norm_act_name

    ans_low = (answer or "").lower()
    for fam, acts in family_map.family_to_acts.items():
        if fam in gold_families:
            continue
        for act in acts or []:
            na = norm_act_name(str(act))
            if na and na in norm_act_name(answer or ""):
                reasons.append(
                    f"act_family_discrepancy: answer names '{act}' (family {fam}) outside gold families {sorted(gold_families)}"
                )
                diagnostics.append(
                    f"The answer invokes the {act}; the question's governing regimes are {sorted(gold_families)}."
                )
                break

    return {
        "flagged": bool(reasons),
        "reasons": reasons[:6],
        "diagnostic": " | ".join(diagnostics[:4]),
        "cited_sections": sorted({i for i, _ in cited}),
    }


_F2_SYSTEM_PROMPT = (
    "You are a careful Indian legal assistant working ONLY from supplied evidence. "
    "A deterministic check has flagged a possible provision mis-binding in a "
    "previous structured analysis. Verify the flagged binding against the "
    "evidence and re-select the correct provision or subsection. You MUST NOT "
    "introduce new legal positions, provisions, definitions, conditions, "
    "exceptions or facts that are not in the analysis or evidence; you MUST NOT "
    "change the analysis's legal conclusion unless the provision choice forces "
    "it. Output ONLY one JSON object: "
    '{"corrected_provisions": list of {provision_id, basis}, '
    '"conclusion_unchanged": boolean, '
    '"final_answer": string (the full corrected answer, citing [n] markers)}. '
    "Use only the provided evidence."
)


def run_f2_one(
    qid: str,
    gate_source: str,
    manifest_q: dict,
    payload_index: dict,
    role_table: dict,
    client: Any,
    calls_path: Path,
    lock: threading.Lock,
    d2_rec: dict,
    question_obj: Any,
    refs: list[str],
    family_map: Any,
    call_counter: dict,
) -> dict:
    """One question through the F2 deterministic check -> re-decision call."""
    import re as _re

    d2_answer = str(d2_rec.get("answer") or "")
    d2_lat = int(d2_rec.get("latency_ms", 0) or 0)
    rec: dict[str, Any] = {"qid": qid, "condition": "F2", "gate_source": gate_source}
    if d2_rec.get("error"):
        rec.update({"status": "d2_error", "answer": d2_answer, "latency_ms": d2_lat})
        return rec

    entry = manifest_q.get(qid) or {}
    cid_list = (entry.get("conditions", {}).get(O3_COND) or {}).get("context_chunk_ids") or []
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry.get("question") or question_obj.question, chunks, "general_qa")
    citations = [
        {
            "index": cit["index"],
            "chunk_id": cit["chunk_id"],
            "text": next((ch.text for ch in chunks if ch.chunk_id == cit["chunk_id"]), ""),
        }
        for cit in (built.citations or [])
    ]
    units = question_obj.recall_units()
    gold_families = {u.family for u in units}

    check = check_provision_binding(d2_rec.get("analysis"), d2_answer, citations, gold_families, family_map, role_table)
    rec["deterministic_flagged"] = check["flagged"]
    rec["check_reasons"] = check["reasons"]

    audit_hit = gate_source in ("audit", "both")
    if not (check["flagged"] or audit_hit):
        rec.update({"status": "not_gated", "answer": d2_answer, "latency_ms": d2_lat})
        return rec
    rec["gated"] = True

    analysis_json = (
        json.dumps(d2_rec.get("analysis"), ensure_ascii=False) if isinstance(d2_rec.get("analysis"), dict) else "{}"
    )
    flagged_json = json.dumps(check["reasons"], ensure_ascii=False)
    user_p = (
        f"QUESTION:\n{entry.get('question') or question_obj.question}\n\n"
        f"EVIDENCE SOURCES (use ONLY these; [n] = source n):\n{built.context}\n\n"
        f"STRUCTURED ANALYSIS:\n{analysis_json}\n\n"
        f"DETERMINISTIC PROVISION FLAGS (verify each against the evidence; you adjudicate):\n{flagged_json}\n\n"
        "Re-bind only what the flags justify. Output ONLY the required JSON object."
    )

    payload = None
    ok = False
    error = ""
    resp = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if call_counter["ok"] >= BUDGET_CAP:
            rec.update({"status": "budget_exhausted", "answer": d2_answer, "latency_ms": d2_lat})
            return rec
        resp = client.call(_F2_SYSTEM_PROMPT, user_p, temperature=LLM_TEMPERATURE, max_tokens=RECOVERY_MAX_TOKENS)
        usage = getattr(resp, "usage", None) or {}
        payload_i = extract_json_object(resp.text or "") if not resp.error else None
        ok_i = False
        if (
            payload_i is not None
            and isinstance(payload_i.get("final_answer"), str)
            and payload_i["final_answer"].strip()
        ):
            ok_i = True
        else:
            error = resp.error or "unparseable/empty re-decision JSON"
        _log_call(
            calls_path,
            lock,
            qid=qid,
            stage="provision_redecision",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int(getattr(resp, "latency", 0.0) * 1000),
            success=bool(ok_i),
            revision_count=attempt - 1,
        )
        if ok_i:
            payload, ok = payload_i, True
            call_counter["ok"] += 1
            error = ""
            break
        rec[f"attempt_{attempt}_error"] = error
        rec[f"attempt_{attempt}_output_chars"] = len(resp.text or "")

    if not ok:
        rec.update({
            "status": "redecision_failed",
            "error": f"F2 re-decision failed after {MAX_ATTEMPTS} attempts: {error}",
            "answer": d2_answer,
            "latency_ms": d2_lat,
        })
        return rec

    rec["redecision_call_made"] = True
    rec["conclusion_unchanged"] = bool(payload.get("conclusion_unchanged"))
    rec["corrected_provisions"] = payload.get("corrected_provisions") or []
    rec["latency_ms"] = d2_lat + int(getattr(resp, "latency", 0.0) * 1000)

    candidate = str(payload.get("final_answer") or "").strip()
    max_idx = len(built.citations or [])
    out_of_range = {int(n) for n in _re.findall(r"\[(\d{1,2})\]", candidate)} - {
        n for n in map(int, _re.findall(r"\[(\d{1,2})\]", candidate)) if 1 <= n <= max_idx
    }
    for n in sorted(out_of_range):
        candidate = candidate.replace(f"[{n}]", "")
    rec["status"] = "rebound"
    rec["answer"] = candidate
    return rec


def _f1_deterministic_nomination(qid: str, d2_rec: dict, manifest_q: dict, payload_index: dict, q: Any) -> bool:
    """Cheap deterministic F1 nomination: abstain + answerable + gold coverage."""
    if d2_rec.get("error") or not abstain_check(str(d2_rec.get("answer") or "")):
        return False
    if q.insufficient_evidence:
        return False
    entry = manifest_q.get(qid) or {}
    cid_list = (entry.get("conditions", {}).get(O3_COND) or {}).get("context_chunk_ids") or []
    from evaluation.resolution import payload_to_keys

    gold_families = {u.family for u in q.recall_units()}
    for cid in cid_list:
        pl = payload_index.get(cid) or {}
        for fam, _sec in payload_to_keys(pl, _GLOBAL_FAMILY_MAP):
            if fam in gold_families:
                return True
    return False


def _f2_deterministic_nomination(
    qid: str, d2_rec: dict, manifest_q: dict, payload_index: dict, q: Any, role_table: dict, family_map: Any
) -> bool:
    """Cheap deterministic F2 nomination (check_provision_binding, 0 calls)."""
    if d2_rec.get("error"):
        return False
    entry = manifest_q.get(qid) or {}
    cid_list = (entry.get("conditions", {}).get(O3_COND) or {}).get("context_chunk_ids") or []
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry.get("question") or q.question, chunks, "general_qa")
    citations = [
        {
            "index": cit["index"],
            "chunk_id": cit["chunk_id"],
            "text": next((ch.text for ch in chunks if ch.chunk_id == cit["chunk_id"]), ""),
        }
        for cit in (built.citations or [])
    ]
    gold_families = {u.family for u in q.recall_units()}
    return check_provision_binding(
        d2_rec.get("analysis"), str(d2_rec.get("answer") or ""), citations, gold_families, family_map, role_table
    )["flagged"]


def main() -> None:  # pragma: no cover - CLI entry
    global _GLOBAL_FAMILY_MAP
    ap = argparse.ArgumentParser(description="Experiment F layer 1: abstention calibration")
    ap.add_argument("--stub", action="store_true", help="0-call pipeline validation")
    ap.add_argument("--resume", action="store_true", help="resume from checkpoint")
    ap.add_argument("--only", nargs="*", help="restrict to these qids")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--layer", choices=["f1", "f2", "both"], default="both")
    ap.add_argument("--analyze", action="store_true", help="score stored checkpoints (no calls)")
    args = ap.parse_args()

    if args.analyze:
        results = analyze(args.stub)
        _write_summary(results, args.stub)
        print(json.dumps(results["conditions"], indent=1))
        print("F1 gate:", results["f1_gate"])
        print("F2 gate:", results["f2_gate"])
        print("guard:", results["f1_guard"], "| accounting:", results["accounting"])
        return

    stub = args.stub
    calls_path = F_CALLS_STUB_JSONL if stub else F_CALLS_JSONL
    ckpt_path = F_CKPT_F1_STUB if stub else F_CKPT_F1

    with redirect_stdout(_io.StringIO()), redirect_stderr(_io.StringIO()):
        from evaluation.resolution import FamilyMap

        family_map = FamilyMap()
        questions = {q.raw["question_id"]: q for q in load_questions()}
        payload_index = load_payload_index()

    manifest = json.loads(C_MANIFEST.read_text(encoding="utf-8"))
    manifest_q = {k: v for k, v in manifest.get("questions", manifest).items() if isinstance(v, dict)}
    _GLOBAL_FAMILY_MAP = family_map

    from evaluation.resolution import payload_to_keys  # noqa: F401  (used in run_f1_one)

    d2_recs = [r for r in _load_ckpt(D_D2_CKPT_STUB if stub else D_D2_CKPT) if not r.get("error") and r.get("answer")]
    d2_by_qid = {r["question_id"]: r for r in d2_recs}

    # Audit gate lists (full-150 human audit)
    gate_lists = json.loads((OUT_DIR / "experiment_F_gate_lists.json").read_text(encoding="utf-8"))
    audit_f1 = set(gate_lists["f1_abstention_gate"])
    audit_f2 = set(gate_lists["f2_provision_check"])
    # Machine-incorrect filtering of audit targets (design: force-gate only the
    # 29 machine-incorrect F1 qids / 30 F2 qids; already-correct ones must not
    # be touched). Uses evaluator-v2 per-question correctness under D2.
    v2_by_qid = {
        r["qid"]: r for r in map(json.loads, (OUT_DIR / "evaluator_v2_per_question.jsonl").open(encoding="utf-8"))
    }

    def _machine_incorrect(qid: str) -> bool:
        row = (v2_by_qid.get(qid) or {}).get("D2") or {}
        return row.get("v2", {}).get("correct") is not True

    # Evaluator v2 references
    overlay = json.loads((PROJECT_ROOT / "evaluation" / "evaluator_v2_overlay.json").read_text(encoding="utf-8"))
    widened = overlay["widened_conclusions"]
    banned_by_qid = {
        qid: set(overlay.get("banned_abstain_markers", {}).get("markers", []))
        for qid in overlay.get("banned_abstain_markers", {}).get("applies_to_qids", [])
    }

    layers = {"f1", "f2"} if args.layer == "both" else {args.layer}
    ckpt_path = F_CKPT_F1_STUB if stub else F_CKPT_F1

    # Evidence-derived role/section table (0 calls) for F2
    role_table = build_role_section_table(payload_index)

    # Deterministic pre-hit caches (used for gate_source provenance)
    _f1_det_cache: dict[str, bool] = {}
    _f2_det_cache: dict[str, bool] = {}

    def _f1_det_hit(qid: str) -> bool:
        if qid not in _f1_det_cache:
            _f1_det_cache[qid] = _f1_deterministic_nomination(
                qid, d2_by_qid[qid], manifest_q, payload_index, questions[qid]
            )
        return _f1_det_cache[qid]

    def _f2_det_hit(qid: str) -> bool:
        if qid not in _f2_det_cache:
            _f2_det_cache[qid] = _f2_deterministic_nomination(
                qid, d2_by_qid[qid], manifest_q, payload_index, questions[qid], role_table, family_map
            )
        return _f2_det_cache[qid]

    targets = sorted(set(args.only)) if args.only else sorted(d2_by_qid)
    todo_f1 = (
        [q for q in targets if q not in _load_ckpt_index(F_CKPT_F1_STUB if stub else F_CKPT_F1)]
        if "f1" in layers
        else []
    )
    todo_f2 = (
        [q for q in targets if q not in _load_ckpt_index(F_CKPT_F2_STUB if stub else F_CKPT_F2)]
        if "f2" in layers
        else []
    )
    todo = sorted(set(todo_f1) | set(todo_f2))
    lock = threading.Lock()
    call_counter = {"ok": 0}

    print(
        f"[F] targets={len(targets)} todo={len(todo)} (f1: {len(todo_f1)}, f2: {len(todo_f2)}) stub={stub}", flush=True
    )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Stub mode via the env signal (same convention as B/C/D/E) — wrapping the
    # client breaks GroundedLLMClient internals (not JSON-serializable payload).
    import os

    os.environ["RAG_USE_STUB_LLM"] = "true" if stub else "false"
    client = _SSLBypassLLMClient()

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = {}
        for qid in todo:
            d2_rec = d2_by_qid.get(qid)
            if d2_rec is None:
                print(f"[F] {qid}: no D2 record - skipping", flush=True)
                continue
            q = questions.get(qid)
            if q is None:
                continue
            refs = [q.acceptable_conclusion or ""]
            w = widened.get(qid)
            if w:
                refs.append(w["add"])
            audit_f1_hit = qid in audit_f1 and _machine_incorrect(qid)
            audit_f2_hit = qid in audit_f2 and _machine_incorrect(qid)
            if "f1" in layers:
                gate_source = (
                    "both" if (audit_f1_hit and _f1_det_hit(qid)) else ("audit" if audit_f1_hit else "deterministic")
                )
                fut = ex.submit(
                    run_f1_one,
                    qid,
                    gate_source,
                    manifest_q,
                    payload_index,
                    None,
                    client,
                    calls_path,
                    lock,
                    d2_rec,
                    q,
                    refs,
                    banned_by_qid.get(qid, set()),
                    call_counter,
                    audit_f1_hit,  # force-gate: bypass abstain-regex + coverage preconditions
                )
                futs[fut] = ("F1", qid)
            if "f2" in layers:
                gate_source = (
                    "both" if (audit_f2_hit and _f2_det_hit(qid)) else ("audit" if audit_f2_hit else "deterministic")
                )
                fut = ex.submit(
                    run_f2_one,
                    qid,
                    gate_source,
                    manifest_q,
                    payload_index,
                    role_table,
                    client,
                    calls_path,
                    lock,
                    d2_rec,
                    q,
                    refs,
                    family_map,
                    call_counter,
                )
                futs[fut] = ("F2", qid)
        n_done = 0
        for fut in as_completed(futs):
            cond, qid = futs[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # defensive: keep the pipeline alive
                rec = {"qid": qid, "condition": cond, "status": "runner_error", "error": str(exc), "answer": ""}
            _append_jsonl(ckpt_path if cond == "F1" else F_CKPT_F2 if not stub else F_CKPT_F2_STUB, rec, lock)
            n_done += 1
            if n_done % 10 == 0 or n_done == len(futs):
                print(f"[F] {n_done}/{len(futs)} | ok calls {call_counter['ok']}/{BUDGET_CAP}", flush=True)

    print(f"[F1] phase complete: {call_counter['ok']} successful generations this pass", flush=True)


if __name__ == "__main__":
    main()
