"""QA-sample evaluation harness for the enlargement eval-set (Step 3).

Evaluates:
  1. benchmark/gold_provisions_templates_v1.json  - 198 template-generated questions (Option A)
  2. benchmark/stratified_eval_sample_v1.json     - 80 stratified sample for human spot-review

Modes:
    structural  - schema/validation pass over gold_provisions_templates_v1.json
    coverage    - corpus/registry resolution for a question set
    quality     - question-quality metrics + degeneracy flags
    worksheet   - emit human-review worksheet (stratified_eval_review_worksheet_v1.md)
    review      - consume a completed review sheet -> produce review results JSON
    freeze      - accept gate + write accepted pool + freeze record
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import GoldUnit, load_gold_registry
from evaluation.resolution import FamilyMap, payload_to_keys

TEMPLATES = {
    "make_question_1": "Under Section X, what [is-permitted/is-prohibited/allowed/is-restricted] regarding Y?",
    "make_question_2": "What is the maximum [fine|penalty|imprisonment term|fine amount|prison term] under Section X?",
    "make_question_3": "According to Section X of [act], what action is required for [actor]?",
    "make_question_4": "What does Section X state about [topic]?",
}

REQUIRED = ["qid", "question", "section", "act", "title", "domain"]
TEMPLATE_TO_SHAPE = {
    "make_question_1": ("Under Section", False),
    "make_question_2": ("What is the maximum", False),
    "make_question_3": ("According to Section", False),
    "make_question_4": ("What does Section", False),
}
ACTOR_WORDS = ["person", "entity", "company", "organization", "individual"]


def load_template_file(path: Path) -> dict[str, Any]:
    raw = json.load(open(path, encoding="utf-8"))
    assert raw["version"] == "template-generation-1", f"unexpected version {raw['version']}"
    return raw


def load_sample_file(path: Path) -> dict[str, Any]:
    raw = json.load(open(path, encoding="utf-8"))
    assert raw["version"] == "stratified-sample-v1", f"unexpected version {raw['version']}"
    return raw


def load_payload_index(path: Path | None = None) -> dict[str, dict]:
    """Cached Qdrant payload index {point_id: payload}."""
    if path is None:
        path = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
    index: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
    return index


# --------------------------------------------------------------------------- #
# (a) Structural / schema validation
# --------------------------------------------------------------------------- #


def validate_question(q: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Run schema checks on one question; return list of issues."""
    issues = []
    for key in REQUIRED:
        if key not in q or q.get(key) in (None, ""):
            issues.append({"kind": "missing_required", "field": key, "qid": q.get("qid"), "source": source})
    qid = q.get("qid", "?")
    qtext = q.get("question", "")

    tag = q.get("template")
    if tag and tag in TEMPLATES:
        prefix, _ = TEMPLATE_TO_SHAPE[tag]
        if not qtext.startswith(prefix):
            issues.append({
                "kind": "template_shape_mismatch",
                "template": tag,
                "question": qtext[:80],
                "qid": qid,
                "source": source,
            })
    elif tag not in (None, "none", "") and tag not in TEMPLATES:
        issues.append({"kind": "unknown_template_tag", "template": tag, "qid": qid, "source": source})

    has_new = "template" in q and q.get("template") not in (None, "none")
    has_old = "expected_answer_type" in q or "template_used" in q
    if has_new and has_old:
        issues.append({"kind": "mixed_schema", "qid": qid, "source": source})
    if has_old and not q.get("expected_answer_type"):
        issues.append({
            "kind": "orphan_old_fields",
            "qid": qid,
            "fields": ["expected_answer_type", "template_used"],
            "source": source,
        })

    if len(qtext) < 20:
        issues.append({"kind": "question_too_short", "question": qtext, "qid": qid, "source": source})
    if qtext.rstrip("?").endswith(("of", "about", "for", "to", "in")):
        issues.append({"kind": "question_incomplete_grammar", "question": qtext, "qid": qid, "source": source})

    sec = q.get("section", "")
    odd = (
        sec not in ("N/A", "Reg", "Reg-Labelling", "Sch/Reg", "First Schedule", "Penalty", "Rules-2022")
        and not re.match(r"^\d", str(sec))
        and not re.match(r"^Ch\.", str(sec))
        and not re.match(r"^Order", str(sec))
    )
    if odd:
        issues.append({"kind": "odd_section_ref", "section": sec, "qid": qid, "source": source})

    return issues


def validate_file(path: Path, source: str | None = None) -> dict[str, Any]:
    """Full structural validation of gold_provisions_templates_v1.json."""
    if source is None:
        source = path.name
    raw = load_template_file(path)
    qs = raw["questions"]
    issues: list[dict] = []
    warnings: list[dict] = []

    qids = [q["qid"] for q in qs]
    dupes = Counter(q for q in qids if qids.count(q) > 1)
    for qid, c in dupes.items():
        warnings.append({"kind": "duplicate_qid", "qid": qid, "occurrences": c, "source": source})

    for q in qs:
        issues.extend(validate_question(q, source))

    tags = Counter(q.get("template") for q in qs if q.get("template") in TEMPLATES)
    for t in TEMPLATES:
        if tags[t] == 0:
            warnings.append({"kind": "template_empty", "template": t, "source": source})

    return {
        "source": source,
        "version": raw["version"],
        "generated": raw["generated"],
        "total_questions": len(qs),
        "unique_qids": len(set(qids)),
        "duplicate_qids": dict(dupes),
        "issue_counts": dict(Counter(i["kind"] for i in issues)),
        "issues": issues,
        "warning_counts": dict(Counter(w["kind"] for w in warnings)),
        "warnings": warnings,
        "template_counts": dict(tags),
        "domains": dict(Counter(q["domain"] for q in qs)),
        "acts": dict(Counter(q["act"] for q in qs)),
    }


def report_structural(stats: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Source: {stats['source']} (v{stats['version']}, generated={stats['generated']})")
    lines.append(f"Total questions: {stats['total_questions']} | Unique qids: {stats['unique_qids']}")
    lines.append(f"Duplicate qids: {stats['duplicate_qids'] if stats['duplicate_qids'] else 'none'}")
    lines.append("Issues by kind:")
    for k, v in stats["issue_counts"].items():
        lines.append(f"  - {k}: {v}")
    lines.append("Warnings by kind:")
    for k, v in stats["warning_counts"].items():
        lines.append(f"  - {k}: {v}")
    lines.append(f"Template counts: {stats['template_counts']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# (b) Corpus / registry coverage
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Precomputed (family, section) -> point_ids index, so per-question coverage
# is O(1) rather than scanning ~27K payloads each time.
# --------------------------------------------------------------------------- #

_coverage_index: dict[str, dict[str, list[str]]] = {}


def build_coverage_index(payload_index: dict[str, dict]) -> dict[str, dict[str, list[str]]]:
    """Index: family -> {base_section -> [point_ids]} (+ 'ALL' for instrument-level)."""
    fm = FamilyMap()
    index: dict[str, dict[str, list[str]]] = {}
    for pid, payload in payload_index.items():
        keys = payload_to_keys(payload, fm)
        for family, section in keys:
            fam_idx = index.setdefault(family, {})
            key = section if section else "ALL"
            fam_idx.setdefault(key, []).append(pid)
    return index


def question_gold_units(q: dict[str, Any]) -> list[GoldUnit]:
    """Produce GoldUnit records from a question dict (qid like fssai:s31(2))."""
    qid = q["qid"]
    family = qid.split(":", 1)[0] if ":" in qid else "unknown"
    section = q.get("section")
    if section and re.match(r"^\d", str(section)):
        section = section.split("-")[0]
        m = re.match(r"^\d{1,4}", str(section))
        if m and "(" not in section:
            section = m.group(0)
    unit = GoldUnit(
        provision_id=qid,
        family=family,
        section=section,
        act=q.get("act", ""),
        collection=None,
        document_id=None,
        gain=2.0,
        role="primary",
    )
    return [unit]


def classify_coverage(qid: str, corpus: bool, registry: bool, act_family: bool) -> str:
    if corpus:
        return "fully_covered"
    if registry and act_family:
        return "registry_only"
    if registry:
        return "registry_missing"
    return "unresolved"


def coverage_per_question(
    q: dict[str, Any], registry: dict[str, dict], coverage_index: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    """Resolve a single question to gold + corpus coverage (O(1) via precomputed index)."""
    units = question_gold_units(q)
    points: dict[str, list[str]] = {}
    for u in units:
        if u.section is None:
            pts = coverage_index.get(u.family, {}).get("ALL", [])
        else:
            pts = coverage_index.get(u.family, {}).get(u.section, [])
        points[u.provision_id] = pts

    act = q.get("act", "")
    act_family = FamilyMap().family_s_for_act(act)
    return {
        "qid": q["qid"],
        "family": units[0].family,
        "section": units[0].section,
        "resolved_in_registry": q["qid"] in registry,
        "resolved_in_corpus": bool(points.get(q["qid"])),
        "corpus_points": len(points.get(q["qid"], [])),
        "registry_act_matches_corpus": bool(act_family),
        "corpus_family_matches": act_family,
        "coverage_status": classify_coverage(
            q["qid"], bool(points.get(q["qid"])), bool(registry.get(q["qid"])), bool(act_family),
        ),
    }


# --------------------------------------------------------------------------- #
# (c) Question quality
# --------------------------------------------------------------------------- #


def quality_per_question(q: dict[str, Any]) -> dict[str, Any]:
    qtext = q["question"]
    qid = q["qid"]
    title = q.get("title", "")

    degenerate = []
    if len(qtext) > 160:
        degenerate.append("question_overly_long")
    if qtext.startswith("Under Section") or qtext.startswith("According to Section"):
        for word in ACTOR_WORDS:
            if word in qtext.lower():
                degenerate.append("template_actor_generic")
    if qtext.startswith("What does Section"):
        rest = qtext[len("What does Section") :]
        if title.lower() in rest.lower():
            degenerate.append("title_repeated_in_question")
    if re.match(r"^\w+\s+regarding", qtext):
        if len(qtext.split()) > 18:
            degenerate.append("title_bloat_in_question")

    answerability = "open_ended"
    if re.search(r"maximum\s+(fine|penalty|imprisonment|prison|term|amount)", qtext, re.IGNORECASE):
        answerability = "limit_fact"
    elif (
        re.search(r"(is\s+(permitted|prohibited)|is\s+(allowed|restricted))", qtext)
        or "action is required" in qtext.lower()
    ):
        answerability = "duty_operator"

    actor = next((w for w in ACTOR_WORDS if w in qtext.lower()), None)

    return {
        "qid": q["qid"],
        "question_len": len(qtext),
        "title_len": len(title),
        "degenerate_flags": degenerate,
        "answerability": answerability,
        "actor_word": actor,
        "template": q.get("template"),
        "domain": q.get("domain"),
    }


def build_review_worksheet(
    sample: dict[str, Any], registry: dict[str, dict], coverage_index: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    rows = []
    for q in sample["questions"]:
        cov = coverage_per_question(q, registry, coverage_index)
        qd = quality_per_question(q)
        rows.append({
            "row": len(rows) + 1,
            "qid": q["qid"],
            "question": q["question"],
            "section": q["section"],
            "act": q["act"],
            "title": q["title"],
            "domain": q["domain"],
            "template": q.get("template"),
            "corpus_coverage": cov["coverage_status"],
            "corpus_points": cov["corpus_points"],
            "resolved_in_registry": cov["resolved_in_registry"],
            "answerability": qd["answerability"],
            "degenerate_flags": qd["degenerate_flags"],
            "question_len": qd["question_len"],
            "reviewer_verdict": "",
            "reviewer_reason": "",
            "reviewer_accepted_answer_type": "",
        })
    return {"version": "review-worksheet-v1", "sample_version": sample["version"], "n": len(rows), "rows": rows}


def emit_markdown_worksheet(worksheet: dict[str, Any], path: Path) -> None:
    rows = worksheet["rows"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("# QA Sample Human Review Worksheet (stratified_eval_sample_v1.json)\n\n")
        f.write("## Instructions\n\n")
        f.write(
            "For each of the " + str(len(rows)) + " questions, judge whether it is a good golden benchmark "
            "question and can be answered from the corpus.\n\n"
            "Verdict options:\n"
            "  - **ACCEPT** — answerable, unambiguous, tests a real legal fact.\n"
            "  - **REJECT** — degenerate, unanswerable from corpus, ambiguous, or no determinate answer.\n\n"
            "Marking columns: `reviewer_verdict`, `reviewer_reason` (e.g. D-DEGENERATE, C-CORPUS_GAP, A-AMBIGUOUS), "
            "`reviewer_accepted_answer_type` (limit_fact | duty_operator | provision_description).\n\n",
        )
        f.write("## Summary table\n\n")
        f.write(
            "| # | qid | question | template | coverage | answerability | degenerate | verdict | reason | answer_type |\n",
        )
        f.write(
            "|---|-----|----------|----------|----------|---------------|------------|---------|--------|-------------|\n",
        )
        for r in rows:
            flags = ",".join(r["degenerate_flags"]) if r["degenerate_flags"] else "-"
            f.write(
                f"| {r['row']} | {r['qid']} | {r['question'][:90]} | {r['template']} | {r['corpus_coverage']} | "
                f"{r['answerability']} | {flags} | {r['reviewer_verdict']} | {r['reviewer_reason']} | {r['reviewer_accepted_answer_type']} |\n",
            )
        f.write("\n## Per-question detail\n\n")
        for r in rows:
            f.write(f"### Row {r['row']}: {r['qid']}\n\n")
            f.write(f"- **question:** {r['question']}\n")
            f.write(f"- **section:** {r['section']}  - **act:** {r['act']}\n")
            f.write(f"- **title:** {r['title']}\n")
            f.write(f"- **domain:** {r['domain']}  - **template:** {r['template']}\n")
            f.write(f"- **corpus coverage:** {r['corpus_coverage']} ({r['corpus_points']} points)\n")
            f.write(f"- **answerability:** {r['answerability']}\n")
            f.write(
                f"- **degenerate flags:** {', '.join(r['degenerate_flags']) if r['degenerate_flags'] else 'none'}\n",
            )
            f.write("- **reviewer verdict:** ____________\n")
            f.write("- **reviewer reason:** ____________\n")
            f.write("- **accepted answer type:** ____________\n\n")
        f.write("## Acceptance gate\n\n")
        f.write(f"Target: >= 60 accepted out of {len(rows)} (>= 75%).\n")


# --------------------------------------------------------------------------- #
# (d) Review consumption + freeze baseline
# --------------------------------------------------------------------------- #


def consume_review(sheet: dict[str, Any]) -> dict[str, Any]:
    """Read a completed review worksheet (verdicts filled) and produce results."""
    rows = sheet["rows"]
    accepted = [r for r in rows if r["reviewer_verdict"] == "ACCEPT"]
    rejected = [r for r in rows if r["reviewer_verdict"] == "REJECT"]

    counts = {
        "total": len(rows),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "accept_rate": round(len(accepted) / len(rows), 4) if rows else 0.0,
        "accept_rate_pct": round(len(accepted) / len(rows) * 100, 1) if rows else 0.0,
        "target_met": len(accepted) >= 60,
        "rejected_details": [
            {"qid": r["qid"], "reason": r["reviewer_reason"], "question": r["question"][:100]} for r in rejected
        ],
        "accepted_by_template": dict(Counter(r["template"] for r in accepted)),
        "accepted_by_domain": dict(Counter(r["domain"] for r in accepted)),
        "accepted_answer_types": dict(Counter(r["reviewer_accepted_answer_type"] for r in accepted)),
    }
    return {
        "version": "review-results-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": counts,
        "accepted_qids": [r["qid"] for r in accepted],
        "rejected_qids": [r["qid"] for r in rejected],
    }


def freeze_baseline(accepted: list[dict[str, Any]], out_dir: Path, source_version: str) -> dict[str, Any]:
    """Write accepted questions + freeze record."""
    out_dir.mkdir(parents=True, exist_ok=True)
    accepted_questions = [
        {
            "qid": q["qid"],
            "question": q["question"],
            "section": q["section"],
            "act": q["act"],
            "title": q["title"],
            "domain": q["domain"],
            "template": q["template"],
            "expected_answer_type": "text",
            "accepted_in_review": source_version,
            "review_version": "stratified_eval_review_v1",
        }
        for q in accepted
    ]
    accepted_path = out_dir / "stratified_eval_sample_accepted_v1.json"
    json.dump(
        {"version": "accepted-sample-v1", "n": len(accepted_questions), "questions": accepted_questions},
        open(accepted_path, "w", encoding="utf-8"),
        indent=2,
    )
    freeze = {
        "freeze_version": "v1",
        "frozen_at": datetime.now(UTC).isoformat(),
        "source_sample_version": source_version,
        "review_version": "stratified_eval_review_v1",
        "n_questions": len(accepted_questions),
        "templates": list(TEMPLATES),
        "question_ids": [q["qid"] for q in accepted_questions],
        "artifact": str(accepted_path),
        "checksum": "",
    }
    fp = out_dir / "stratified_eval_baseline_freeze_v1.json"
    raw = open(accepted_path, "rb").read()
    freeze["checksum_sha256"] = hashlib.sha256(raw).hexdigest()
    json.dump(freeze, open(fp, "w", encoding="utf-8"), indent=2)
    return {"accepted_path": str(accepted_path), "freeze_path": str(fp), "n": len(accepted_questions)}


# --------------------------------------------------------------------------- #
# (e) Main
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate template-generated QA samples (enlargement Step 3).")
    parser.add_argument(
        "--mode", choices=["structural", "coverage", "quality", "worksheet", "review", "freeze"], default="structural",
    )
    parser.add_argument("--sample", default="benchmark/stratified_eval_sample_v1.json")
    parser.add_argument("--templates", default="benchmark/gold_provisions_templates_v1.json")
    parser.add_argument("--out", default="evaluation/out")
    args = parser.parse_args()

    template_path = Path(args.templates)
    sample_path = Path(args.sample)
    out_dir = Path(args.out)

    registry = load_gold_registry()
    payload_index = load_payload_index()
    coverage_index = build_coverage_index(payload_index)

    if args.mode == "structural":
        stats = validate_file(template_path)
        print(report_structural(stats))
        return 1 if stats["issues"] else 0

    if args.mode == "coverage":
        if template_path.exists():
            raw = load_template_file(template_path)
            questions = raw["questions"]
        else:
            sample = load_sample_file(sample_path)
            questions = sample["questions"]
        payload_index = load_payload_index()
        covered, registry_only, gap, unresolved = [], [], [], []
        for q in questions:
            cov = coverage_per_question(q, registry, coverage_index)
            lst = (
                covered
                if cov["coverage_status"] == "fully_covered"
                else registry_only
                if cov["coverage_status"] == "registry_only"
                else gap
                if cov["coverage_status"] == "registry_missing"
                else unresolved
            )
            lst.append(q["qid"])
        print(f"questions: {len(questions)}")
        print(f"  fully_covered:        {len(covered)}")
        print(f"  registry_only:        {len(registry_only)}")
        print(f"  registry_missing:     {len(gap)}")
        print(f"  unresolved:           {len(unresolved)}")
        print(f"  uncovered (covered list): {covered}")
        return 0

    if args.mode == "quality":
        raw = load_template_file(template_path)
        qs = raw["questions"]
        total_deg = 0
        for q in qs:
            qd = quality_per_question(q)
            if qd["degenerate_flags"]:
                total_deg += 1
                print(f"[{q.get('template') or 'None'}] {q['qid']}: {qd['degenerate_flags']}")
        print(f"\ntotal_questions={len(qs)} degenerate={total_deg}")
        return 0

    if args.mode == "worksheet":
        sample = load_sample_file(sample_path)
        worksheet = build_review_worksheet(sample, registry, coverage_index)
        ws_path = out_dir / "stratified_eval_review_worksheet_v1.md"
        emit_markdown_worksheet(worksheet, ws_path)
        json.dump(
            worksheet, open(out_dir / "stratified_eval_review_worksheet_v1.json", "w", encoding="utf-8"), indent=2,
        )
        print(f"worksheet written: {ws_path}")
        return 0

    if args.mode == "review":
        ws_path = out_dir / "stratified_eval_review_worksheet_v1.json"
        sheet = json.load(open(ws_path, encoding="utf-8"))
        results = consume_review(sheet)
        json.dump(results, open(out_dir / "stratified_eval_review_results_v1.json", "w", encoding="utf-8"), indent=2)
        print(
            f"accepted={results['counts']['accepted']} rejected={results['counts']['rejected']} "
            f"accept_rate={results['counts']['accept_rate']} target_met={results['counts']['target_met']}",
        )
        return 0 if results["counts"]["target_met"] else 1

    if args.mode == "freeze":
        ws_path = out_dir / "stratified_eval_review_worksheet_v1.json"
        sheet = json.load(open(ws_path, encoding="utf-8"))
        accepted = [r for r in sheet["rows"] if r["reviewer_verdict"] == "ACCEPT"]
        res = freeze_baseline(accepted, Path(out_dir), sheet["version"])
        print(f"accepted={len(accepted)} artifact={res['accepted_path']} freeze={res['freeze_path']}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
