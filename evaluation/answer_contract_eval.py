"""Answer contract offline adjudication + comparison harness.

Scores cached generation answers against the deterministic answer contract and the
benchmark's gold authorities/concepts for the 66 audited provision_check / abstention_gate
failures (evaluation/out/ceiling_v5/full_review_tabulation.json). 0-LLM after cached answers
exist.

Writes, per requested tag, three artifacts under evaluation/out/ceiling_v5/rerun_gip/:

- <tag>_answer_contract.jsonl            — per-qid contract check + authority/concept signals
- <tag>_answer_contract_summary.json    — per model_action aggregation + totals
- <tag>_answer_contract_false_abstentions.json — abstention_gate answers the contract flagged,
                                                with competing sections named in the answer

Usage:
    python -m evaluation.answer_contract_eval                   # baseline only
    python -m evaluation.answer_contract_eval --tag cond_v3     # single named tag
    python -m evaluation.answer_contract_eval --tag baseline,cond_v3   # comparison
    python -m evaluation.answer_contract_eval --tag sum              # best-available summary
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

CEILING_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
RERUN_DIR = CEILING_DIR / "rerun_gip"
TABULATION = CEILING_DIR / "full_review_tabulation.json"

logger = logging.getLogger(__name__)

_SECTION_CITE_RE = re.compile(r"[Ss]ection\s+(\d+(?:\([^)]+\))*)|s\s+(\d{1,4})\b")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {"the", "of", "a", "an", "and", "or", "to", "in", "on", "for", "under", "per", "with", "by"}


def _toks(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP}


def concept_coverage(answer: str, required: list[str]) -> dict:
    ans = _toks(answer)
    covered, missing = [], []
    for c in required:
        need = _toks(c)
        if not need:
            continue
        hit = len(need & ans) / len(need)
        (covered if hit >= 0.8 else missing).append(c)
    return {"covered": covered, "missing": missing, "all": bool(covered) and not missing}


def authority_coverage(answer: str, required: list[str]) -> dict:
    ans = _toks(answer)
    for a in required:
        need = _toks(a)
        if not need:
            continue
        if len(need & ans) / len(need) >= 0.5:
            return {"hit": True, "authority": a}
    return {"hit": False, "authority": None}


def _answer_section_set(answer: str, governing: str | None) -> set[str]:
    vals: set[str] = set()
    for m in _SECTION_CITE_RE.finditer(answer or ""):
        num = m.group(1) or m.group(2)
        if num:
            vals.add(num)
    if governing:
        vals.discard(governing)
    return vals


def load_audit_actions() -> dict[str, str]:
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for rec in tab.get("records") or []:
        out[str(rec.get("qid"))] = str(rec.get("model_action") or "")
    return out


def score_tag(tag: str) -> dict:
    from app.rag.generation.answer_contract import get_answer_contract_resolver
    from evaluation.benchmark import load_questions
    from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index, RAW_DIR

    resolver = get_answer_contract_resolver()
    payload_index = _load_payload_index()

    arms = {}
    for line in (RAW_DIR / "C_hybrid.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            arms[str(r["question_id"])] = r["chunk_ids"]

    questions = {q.question_id: q for q in load_questions()}
    cache_path = RERUN_DIR / f"{tag}.jsonl"
    if not cache_path.exists():
        return {"tag": tag, "error": f"missing {cache_path}"}

    cache = {}
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            cache[str(rec.get("qid"))] = rec

    audit_actions = load_audit_actions()

    targets = [
        (qid, action)
        for qid, action in audit_actions.items()
        if action in ("provision_check", "abstention_gate")
        and qid in questions
        and qid in cache
    ]

    by_action: dict[str, dict] = defaultdict(
        lambda: {
            "n": 0,
            "flagged": 0,
            "false_abstention": 0,
            "provision_mismatch": 0,
            "violations": 0,
            "authority_hit": 0,
            "concepts_covered_all": 0,
        }
    )
    rows = []
    for qid, action in targets:
        q = questions[qid]
        rec = cache[qid]
        ids = arms.get(qid) or []
        chunks = _chunks_for(ids, payload_index)
        c = None
        check = None
        try:
            c = resolver.build_contract(
                q.question,
                chunks,
                query_type=q.question_types[0] if q.question_types else "",
                question_obj=q,
            )
            check = resolver.check_answer(rec.get("answer") or "", c, chunks)
        except Exception as exc:
            logger.warning("contract failed for %s: %s", qid, exc)

        governing = c.governing.section if (c and c.governing) else None
        answer = rec.get("answer") or ""
        competing = _answer_section_set(answer, governing) if governing else set()

        if check and check.false_abstention and not competing:
            check.violations = [
                v for v in check.violations if v.kind != "false_abstention"
            ]
            if not check.violations:
                check.passed = True
            check.false_abstention = False

        if check and check.provision_mismatch and not competing and not governing:
            check.violations = [
                v for v in check.violations if v.kind not in ("wrong_provision", "missed_provision")
            ]
            if not check.violations:
                check.passed = True
            check.provision_mismatch = False

        auth = authority_coverage(answer, q.gold_authorities)
        cov = concept_coverage(answer, q.gold_concepts)
        n_violations = len(check.violations) if check else 0
        flagged = bool(check and not check.passed)

        row = {
            "qid": qid,
            "action": action,
            "answer_length": len(answer),
            "answer": answer,
            "has_contract": c is not None,
            "named_sections": c.named_sections if c else [],
            "named_act": c.named_act if c else None,
            "governing_section": governing,
            "competing_sections_in_answer": sorted(competing),
            "passed": check.passed if check else None,
            "false_abstention": check.false_abstention if check else False,
            "provision_mismatch": check.provision_mismatch if check else False,
            "n_violations": n_violations,
            "flagged": flagged,
            "violations": [
                {"kind": v.kind, "severity": v.severity, "message": v.message}
                for v in (check.violations if check else [])
            ],
            "authority": auth["authority"],
            "authority_hit": auth["hit"],
            "concepts_covered": len(cov["covered"]),
            "concepts_missing": len(cov["missing"]),
            "concepts_all": cov["all"],
        }
        rows.append(row)

        slot = by_action[action]
        slot["n"] += 1
        slot["flagged"] += int(flagged)
        slot["false_abstention"] += int(check.false_abstention if check else False)
        slot["provision_mismatch"] += int(check.provision_mismatch if check else False)
        slot["violations"] += n_violations
        slot["authority_hit"] += int(auth["hit"])
        slot["concepts_covered_all"] += int(cov["all"])

    flagged_any = sum(1 for r in rows if r["flagged"])

    out_path = RERUN_DIR / f"{tag}_answer_contract.jsonl"
    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    summary_path = RERUN_DIR / f"{tag}_answer_contract_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "tag": tag,
                "n_targets": len(targets),
                "n_flagged": flagged_any,
                "by_action": dict(by_action),
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fa_rows = [r for r in rows if r["action"] == "abstention_gate" and r["flagged"]]
    fa_path = RERUN_DIR / f"{tag}_answer_contract_false_abstentions.json"
    fa_path.write_text(
        json.dumps(
            {
                "tag": tag,
                "false_abstention_qids": [
                    {
                        "qid": r["qid"],
                        "governing_section": r["governing_section"],
                        "named_sections": r["named_sections"],
                        "competing_sections_in_answer": r["competing_sections_in_answer"],
                        "authority": r["authority"],
                        "violations": r["violations"],
                    }
                    for r in fa_rows
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "tag": tag,
        "n_targets": len(targets),
        "n_flagged": flagged_any,
        "n_false_abstentions": len(fa_rows),
        "by_action": dict(by_action),
        "out_path": str(out_path),
        "summary_path": str(summary_path),
        "false_abstention_path": str(fa_path),
        "false_abstention_rows": [(r["qid"], r["competing_sections_in_answer"]) for r in fa_rows],
        "rows_by_qid": {r["qid"]: r for r in rows},
    }


def print_tag(result: dict) -> None:
    if result.get("error"):
        print(f"{result['tag']}: {result['error']}", flush=True)
        return
    print("=== per model_action ===", flush=True)
    print(
        f"{'action':<20} {'n':>4} {'flagged':>8} {'false_abst':>11} "
        f"{'prov_mismatch':>15} {'violations':>11} {'auth_hit':>9} {'concepts_all':>13}",
        flush=True,
    )
    for action in ("provision_check", "abstention_gate"):
        s = result["by_action"].get(action, {})
        if s.get("n"):
            print(
                f"{action:<20} {s['n']:>4} {s['flagged']:>8} "
                f"{s['false_abstention']:>11} {s['provision_mismatch']:>15} "
                f"{s['violations']:>11} {s['authority_hit']:>9} "
                f"{s['concepts_covered_all']:>13}",
                flush=True,
            )
    print(
        f"tag={result['tag']}  flagged {result['n_flagged']}/{result['n_targets']} "
        f"of the 66 audited failures  | false_abstentions={result['n_false_abstentions']}",
        flush=True,
    )
    if result.get("false_abstention_rows"):
        print(
            f"false-abstention deep dive ({result['n_false_abstentions']} flagged):",
            flush=True,
        )
        for qid, competing in result["false_abstention_rows"]:
            print(f"  {qid}  competing_sections={competing}", flush=True)
    print(f"written {result['out_path']}", flush=True)
    print(f"written {result['summary_path']}", flush=True)
    print(f"written {result['false_abstention_path']}", flush=True)


def box_line(text: str, width: int = 78) -> str:
    return f"| {text.ljust(width - 2)} |"


def print_comparison(left: dict, right: dict) -> None:
    lb = left["rows_by_qid"]
    rb = right["rows_by_qid"]
    union = sorted(set(lb) | set(rb))
    print("\n=== per-qid comparison ===", flush=True)
    print(
        f"{'qid':<7} {'act':<18} {'b_flag':>6} {'c_flag':>6} "
        f"{'b_viols':>8} {'c_viols':>8} {'delta':>6}  note",
        flush=True,
    )
    newly_flagged: list[str] = []
    no_longer_flagged: list[str] = []
    newly_flagged_auth_hit = 0
    newly_flagged_concepts_all = 0
    no_longer_flagged_auth_hit = 0
    no_longer_flagged_concepts_all = 0
    for qid in union:
        lrow = lb.get(qid, {})
        rrow = rb.get(qid, {})
        b_flag = int(lrow.get("flagged", False))
        c_flag = int(rrow.get("flagged", False))
        b_v = lrow.get("n_violations", 0)
        c_v = rrow.get("n_violations", 0)
        delta = c_v - b_v
        action = rrow.get("action", lrow.get("action", ""))
        note = ""
        if c_flag and not b_flag:
            note = "newly flagged"
            newly_flagged.append(qid)
            newly_flagged_auth_hit += int(rrow.get("authority_hit", False))
            newly_flagged_concepts_all += int(rrow.get("concepts_all", False))
        elif b_flag and not c_flag:
            note = "no longer flagged"
            no_longer_flagged.append(qid)
            no_longer_flagged_auth_hit += int(lrow.get("authority_hit", False))
            no_longer_flagged_concepts_all += int(lrow.get("concepts_all", False))
        if delta:
            note = (note + " " if note else "") + f"violations {b_v}->{c_v}"
        print(f"{qid:<7} {action:<18} {b_flag:>6} {c_flag:>6} {b_v:>8} {c_v:>8} {delta:>6}  {note}", flush=True)

    print("\n" + "=" * 78, flush=True)
    print(box_line(" contract adjudication summary | baseline vs cond_v3 "), flush=True)
    print("=" * 78, flush=True)
    print(box_line(f" baseline flagged:  {left['n_flagged']:>3}/{left['n_targets']} of the 66 audited failures"), flush=True)
    print(box_line(f" cond_v3  flagged:  {right['n_flagged']:>3}/{right['n_targets']} of the 66 audited failures"), flush=True)
    print(box_line(f" delta flagged:     {right['n_flagged'] - left['n_flagged']:+d}"), flush=True)
    print(box_line(f" newly flagged:     {', '.join(sorted(newly_flagged)) or '(none)'}"), flush=True)
    print(box_line(f" no longer flagged: {', '.join(sorted(no_longer_flagged)) or '(none)'}"), flush=True)
    print(box_line(f" newly flagged authority_hit / concepts_all: {newly_flagged_auth_hit}/{len(newly_flagged)} / {newly_flagged_concepts_all}/{len(newly_flagged)}"), flush=True)
    print(box_line(f" no longer flagged authority_hit / concepts_all: {no_longer_flagged_auth_hit}/{len(no_longer_flagged)} / {no_longer_flagged_concepts_all}/{len(no_longer_flagged)}"), flush=True)
    print("=" * 78, flush=True)

    changed = sorted(newly_flagged) + sorted(no_longer_flagged)
    if not changed:
        return
    print("\n=== answer diffs for changed rows ===", flush=True)
    for qid in changed:
        lrow = lb.get(qid, {})
        rrow = rb.get(qid, {})
        ba = (lrow.get("answer") or "").strip()
        ca = (rrow.get("answer") or "").strip()
        print(f"\n--- {qid} ({rrow.get('action','')}) ---", flush=True)
        print(f"baseline flagged={lrow.get('flagged')} violations={[v['kind'] for v in lrow.get('violations',[]) ]}", flush=True)
        print(f"cond_v3  flagged={rrow.get('flagged')} violations={[v['kind'] for v in rrow.get('violations',[]) ]}", flush=True)
        if not ba and not ca:
            print("  (both answers missing from cached jsonl)", flush=True)
            continue
        print("baseline answer:", ba[:300], flush=True)
        print("cond_v3  answer:", ca[:300], flush=True)


def print_best_summary(loaded: dict[str, dict]) -> None:
    tags = sorted(loaded.keys())
    if len(tags) < 2:
        print("\n=== best-available summary ===", flush=True)
        print(f"only one contract-tagged run available ({tags[0]}); comparison requires both baseline and cond_v3.", flush=True)
        print_tag(loaded[tags[0]])
        return
    left = loaded[tags[0]]
    right = loaded[tags[1]]
    print("\n=== best-available summary ===", flush=True)
    print_comparison(left, right)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Usage examples:\n"
        "    python -m evaluation.answer_contract_eval                   # baseline only\n"
        "    python -m evaluation.answer_contract_eval --tag cond_v3     # single named tag\n"
        "    python -m evaluation.answer_contract_eval --tag baseline,cond_v3   # comparison\n"
        "    python -m evaluation.answer_contract_eval --tag sum              # best-available summary",
    )
    ap.add_argument(
        "--tag",
        default="baseline",
        help="comma-separated cached tags under rerun_gip/, or 'sum' for a best-available summary (default: baseline)",
    )
    args = ap.parse_args(argv)

    tags = [t.strip() for t in args.tag.split(",") if t.strip()]
    if not tags:
        tags = ["baseline"]

    print(f"tags: {tags}", flush=True)

    if args.tag.strip().lower() == "sum":
        available = []
        for candidate in ("baseline", "cond_v1", "cond_v2", "cond_v3", "cond_v4", "control_post"):
            if (RERUN_DIR / f"{candidate}_answer_contract.jsonl").exists():
                available.append(candidate)
        if not available:
            available = [t for t in tags if (RERUN_DIR / f"{t}_answer_contract.jsonl").exists()]
        requested = available if available else sorted(set(tags) & {c for c in ("baseline", "cond_v1", "cond_v2", "cond_v3", "cond_v4", "control_post") if (RERUN_DIR / f"{c}_answer_contract.jsonl").exists()})
        if not requested:
            print("no contract-tagged runs found; run with --tag baseline first.", flush=True)
            return 2
        loaded: dict[str, dict] = {}
        for tag in requested:
            loaded[tag] = score_tag(tag)
        print_best_summary(loaded)
        return 0

    results = {}
    for tag in tags:
        results[tag] = score_tag(tag)
        print_tag(results[tag])

    if len(results) == 2:
        ordered = sorted(results.keys())
        print_comparison(results[ordered[0]], results[ordered[1]])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
