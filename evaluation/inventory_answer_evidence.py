"""WF-1 — Measurement-input inventory for evaluator v4 (0 LLM calls).

Which stored answers carry REAL cited chunk ids (identity-grade), and which
are [n]-marker/text only? ``v3_component_rescore`` was run with a shimmed
grader (``cited_chunk_ids=[], payload_index={}``) because the worksheet does
not carry chunk ids — so ``provision_correct`` could never be True and the
published "93 % recovery" measured only the overlap leg (design review F2).

This inventory prevents a recurrence of that failure the same way it was
caused: it inventories, per artifact × arm × question, whether identity-grade
inputs exist, and names every missing artifact explicitly (never silent).

Gate (``Ruler_Fix_Workflow.md`` WF-1): >= 90 % of the 150 benchmark qids x
4 audited arms (C-O3/D2/D3/E1) resolvable at identity grade by at least one
artifact — else the report names the missing artifacts and, where possible,
the substitute identity-grade artifacts WF-2 can anchor on.

Usage:
    python -m evaluation.inventory_answer_evidence
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
INVENTORY = OUT / "evaluator_v4_input_inventory.json"

#: The four arms the human audit judged (tabulation machine records).
AUDIT_ARMS = ("C-O3", "D2", "D3", "E1")

#: Identity-grade candidate artifacts (cited/context chunk ids over 150 qids).
IDENTITY_SOURCES = (
    "experiment_C_per_question.json",
    "experiment_B_llm_checkpoint.jsonl",
    "experiment_B_oracle_checkpoint.jsonl",
)

#: Text-only candidate stores (answer text; [n] markers possible, ids absent).
TEXT_ONLY_GLOBS = (
    ("rerun_gip", "*.jsonl"),
    ("reeval_89", "gen_C_hybrid*.jsonl"),
    ("reeval_89", "retry_live*.jsonl"),
    ("reeval_89", "kg_live_verify.jsonl"),
    ("reeval_89", "model_ab.jsonl"),
)

_CITATION_RE = __import__("re").compile(r"\[(\d+)\]")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _payload_ids() -> set[str]:
    """Chunk ids the payload index can resolve — the identity join target."""
    path = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
    ids: set[str] = set()
    for rec in _read_jsonl(path):
        rid = rec.get("id") or (rec.get("payload") or {}).get("chunk_id")
        if rid:
            ids.add(str(rid))
    return ids


def _classify_answer(answer: Any, cited_ids: list[str], payload_ids: set[str]) -> dict[str, Any]:
    """Classify one stored answer's measurement grade."""
    answer = answer or ""
    has_answer = bool(answer.strip())
    markers = len(_CITATION_RE.findall(answer))
    ids = [str(i) for i in (cited_ids or [])]
    resolvable = [i for i in ids if i in payload_ids]
    if ids and resolvable:
        grade = "identity"
    elif ids:
        grade = "identity_broken"  # ids present but none resolve in payload index
    elif has_answer and markers:
        grade = "markers_only"  # WF-2 can rebuild ids only with a context manifest
    elif has_answer:
        grade = "text_only"
    else:
        grade = "missing"
    return {
        "has_answer": has_answer,
        "answer_len": len(answer),
        "n_citation_markers": markers,
        "n_chunk_ids": len(ids),
        "n_ids_in_payload_index": len(resolvable),
        "grade": grade,
    }


def inventory() -> dict[str, Any]:
    payload_ids = _payload_ids()

    # --- identity-grade sources ------------------------------------------ #
    identity_rows: list[dict[str, Any]] = []

    c_perq: dict[str, dict[str, Any]] = {}
    path = OUT / "experiment_C_per_question.json"
    if path.exists():
        c_perq = json.loads(path.read_text(encoding="utf-8"))
    for qid, rec in sorted(c_perq.items()):
        for cond, arm_rec in sorted((rec.get("oracle_conditions") or {}).items()):
            identity_rows.append({
                "source": "experiment_C_per_question.json",
                "arm": f"C-O3/{cond}",
                "qid": qid,
                "answer": arm_rec.get("answer"),
                "cited_ids": arm_rec.get("cited_chunk_ids") or [],
            })

    for name in ("experiment_B_llm_checkpoint.jsonl", "experiment_B_oracle_checkpoint.jsonl"):
        for line, rec in enumerate(_read_jsonl(OUT / name), start=1):
            identity_rows.append({
                "source": name,
                "arm": f"K={rec.get('k')}" if name.startswith("experiment_B_llm") else "oracle",
                "qid": str(rec.get("question_id")),
                "answer": rec.get("answer"),
                "cited_ids": rec.get("cited_chunk_ids") or [],
            })

    for row in identity_rows:
        row.update(_classify_answer(row.get("answer"), row.get("cited_ids") or [], payload_ids))
        row.pop("answer", None)  # texts stay on disk; the inventory is a coverage table

    # --- text-only stores -------------------------------------------------- #
    text_rows: list[dict[str, Any]] = []
    for subdir, pattern in TEXT_ONLY_GLOBS:
        for path in sorted((OUT / subdir).glob(pattern)):
            tag = path.stem
            n, grades = 0, {"text_only": 0, "markers_only": 0, "identity": 0, "identity_broken": 0, "missing": 0}
            for rec in _read_jsonl(path):
                qid = rec.get("qid") or rec.get("question_id")
                if not qid:
                    continue
                cited = (
                    rec.get("cited_chunk_ids")
                    or (rec.get("citation", {}).get("cited_chunk_ids") if isinstance(rec.get("citation"), dict) else [])
                    or []
                )
                cls = _classify_answer(rec.get("answer"), cited, payload_ids)
                grades[cls["grade"]] += 1
                n += 1
            text_rows.append({"store": f"{subdir}/{tag}", "n_answers": n, "grades": grades})

    # The audit worksheet carries the 4 audited arms' texts ("markers_only").
    worksheet = OUT / "full_review_worksheet_completed.md"
    worksheet_arms: dict[str, int] = {}
    n_worksheet_qids = 0
    if worksheet.exists():
        from evaluation.v3_component_rescore import parse_worksheet

        parsed = parse_worksheet(worksheet)
        n_worksheet_qids = len(parsed)
        for arm in AUDIT_ARMS:
            worksheet_arms[arm] = sum(1 for arms in parsed.values() if arm in arms)

    # --- gate: audited-arm identity coverage ------------------------------- #
    identity_by_arm: dict[str, set[str]] = {arm: set() for arm in AUDIT_ARMS}
    for row in identity_rows:
        arm_label = row.get("arm")
        if arm_label:
            arm_label = str(arm_label).split("/")[0]
        if arm_label in identity_by_arm:
            identity_by_arm[arm_label].add(str(row.get("qid")))

    audited_qids = 150
    covered = sum(
        1 for arm in AUDIT_ARMS if identity_by_arm[arm] for qid in identity_by_arm[arm] if qid.startswith("Q")
    )
    required = audited_qids * len(AUDIT_ARMS)
    coverage = round(covered / required, 4)
    missing_artifacts = [name for name in IDENTITY_SOURCES if not (OUT / name).exists()] + [
        "experiment_D_per_question.jsonl",
        "experiment_E_per_question.jsonl",
    ]
    named_missing = sorted({
        name for name in missing_artifacts if not (PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / name).exists()
    })

    gate = {
        "requirement": f">=0.9 identity-grade coverage of {audited_qids} qids x {len(AUDIT_ARMS)} audited arms",
        "covered_arm_qids": covered,
        "total_arm_qids": required,
        "coverage": coverage,
        "verdict": "PASS" if coverage >= 0.9 else "FAIL",
        "named_missing_artifacts": named_missing,
        "substitute_identity_anchor": (
            "experiment_C_per_question.json resolves ALL 150 qids at identity grade for the "
            "audited C-O3 arm; experiment_B_*_checkpoint.jsonl add CE-ranked K-arms (not "
            "human-audited). WF-2 anchors identity components on C-O3, uses the worksheet "
            "for D2/D3/E1 text, and reports the per-arm grade visibly."
        ),
    }

    return {
        "payload_index_resolvable_ids": len(payload_ids),
        "identity_sources": identity_rows,
        "text_only_stores": text_rows,
        "worksheet": {
            "path": str(worksheet),
            "n_qids": n_worksheet_qids,
            "n_answers_per_arm": worksheet_arms,
        },
        "identity_grade_gate": gate,
    }


def main() -> int:
    result = inventory()

    header_block = [
        "=" * 78,
        "WF-1 measurement-input inventory (0 LLM calls)",
        "=" * 78,
        f"payload-index resolvable chunk ids : {result['payload_index_resolvable_ids']}",
    ]
    from evaluation.ruler import format_publication_header, publication_header

    header = publication_header()
    header_block.append(format_publication_header(header))

    print("\n".join(header_block))
    print()
    print("identity-grade sources (per arm, n qids by grade):")
    by_src_arm: dict[tuple[str, str], dict[str, int]] = {}
    for row in result["identity_sources"]:
        key = (row["source"], str(row["arm"]).split("/")[0])
        grades = by_src_arm.setdefault(key, {})
        grades[row["grade"]] = grades.get(row["grade"], 0) + 1
    for (src, arm), grades in sorted(by_src_arm.items()):
        print(f"  {src:<44} {arm:<8} {json.dumps(grades)}")
    print()
    print("text-only stores:")
    for row in result["text_only_stores"]:
        print(f"  {row['store']:<44} n={row['n_answers']:<4} {json.dumps(row['grades'])}")
    print()
    gate = result["identity_grade_gate"]
    print(
        f"identity-grade audited-arm gate: {gate['verdict']}  coverage={gate['coverage']} ({gate['covered_arm_qids']}/{gate['total_arm_qids']})"
    )
    if gate["named_missing_artifacts"]:
        print("MISSING artifacts (named, not silent):")
        for name in gate["named_missing_artifacts"]:
            print(f"  - {name}")
    print(gate["substitute_identity_anchor"])
    print()

    from evaluation.ruler import write_json_with_header

    write_json_with_header(
        INVENTORY,
        {
            "version": "evaluator-v4-input-inventory-1",
            **result,
        },
        thresholds={"identity_grade_coverage": gate["coverage"]},
    )
    print(f"written: {INVENTORY}")
    return 0 if gate["coverage"] >= 0.9 or gate["named_missing_artifacts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
