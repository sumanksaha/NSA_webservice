"""Step 0 — freeze the scorer and label the residual (plan sec 5.2). No model calls.

Residual set (from ``step0_residual_set.json``): D2 still incorrect on III, plus
F1 recovered-but-still-wrong (union of 124). Every residual qid must receive
exactly one of:

  reference_narrow | evidence_missing | model_wrong

Modes (combinable):

  --preannotate     deterministic evidence_missing candidates from C-manifest O3
                    payload + corpus index + F1 justified-abstention signals
                    (writes step0_preannotations.json; does NOT assign final labels)
  --worksheet       write residual-only review worksheet
                    (step0_residual_worksheet.md) with pre-annotations shown
  --labels PATH     apply a JSON map {qid: label} (or {"labels": {...}} records)
  --from-worksheet  parse step0_residual_worksheet_reviewed.md (or --reviewed PATH)
  --publish         validate completeness and write step0_residual_labels.json
                    with step0_complete + per-label counts + gate target files
  --require-complete  exit 2 if not all residual qids have a valid label

Outputs (evaluation/out/ceiling_v5/):
  step0_preannotations.json
  step0_residual_worksheet.md
  step0_residual_labels.json          (with step0_complete)
  step0_corpus_fill_targets.json
  step0_contrastive_targets.json
  step0_dual_score_targets.json

Scorer is frozen: this module never imports generation code and never calls an LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"
CACHE = ROOT / "evaluation" / "out" / "cache"

STEP0_ENUM = ("reference_narrow", "evidence_missing", "model_wrong")
RESIDUAL_SET = OUT / "step0_residual_set.json"
C_MANIFEST = OUT / "experiment_C_context_manifest.json"
PAYLOAD_INDEX = CACHE / "payload_index.jsonl"
PREANNO = OUT / "step0_preannotations.json"

# torch / sentence_transformers are not installed in ./venv; stub them so the
# frozen scorer's import chain resolves without altering scoring behavior.
for _mod in ("torch", "sentence_transformers"):
    if _mod not in sys.modules:
        import types

        class _AnyStub:
            def __init__(self, *a, **k):
                pass

            def __call__(self, *a, **k):
                return _AnyStub()

            def __getattr__(self, name):
                return _AnyStub()

            def __iter__(self):
                return iter(())

        _stub = types.ModuleType(_mod)
        _stub.__getattr__ = lambda name: _AnyStub()
        sys.modules[_mod] = _stub

WORKSHEET = OUT / "step0_residual_worksheet.md"
LABELS_OUT = OUT / "step0_residual_labels.json"

# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


def load_residual_qids(path: Path = RESIDUAL_SET) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    qids = data.get("qids")
    if not qids:
        raise SystemExit(f"no qids in {path}")
    return sorted(qids)


def load_payload_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not PAYLOAD_INDEX.exists():
        return index
    with PAYLOAD_INDEX.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
    return index


def load_c_manifest() -> dict:
    if not C_MANIFEST.exists():
        return {}
    return json.loads(C_MANIFEST.read_text(encoding="utf-8"))


def load_f_statuses() -> dict[str, str]:
    path = OUT / "experiment_F_per_question.jsonl"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("f1_status"):
                out[r["qid"]] = r["f1_status"]
    return out


def load_questions() -> dict[str, Any]:
    from evaluation.benchmark import load_questions as _lq

    return {q.question_id: q for q in _lq()}


# --------------------------------------------------------------------------- #
# Deterministic pre-annotation (evidence_missing candidates only)
# --------------------------------------------------------------------------- #


def _chunk_text(payload: dict) -> str:
    return str(payload.get("chunk_text") or payload.get("text") or "")


def _section_tokens_present_in_o3(
    qid: str,
    question: Any,
    manifest: dict,
    payload_index: dict[str, dict],
    family_map: Any,
    gold_index: tuple,
) -> dict:
    """Check whether each primary gold unit's section text appears in the O3 payload.

    Manifest layout: ``manifest["questions"][qid]["conditions"]["O3_full_support"]
    ["context_chunk_ids"]`` (a list).

    A unit is missing from O3 when it resolved to no corpus chunks (not in index)
    or when none of its corpus chunks appear in the O3 context list.
    """
    from evaluation.experiment_b_topk_eval import gold_chunk_ids_for_units

    entry = (manifest.get("questions") or {}).get(qid) or {}
    if entry.get("unresolved"):
        return {
            "o3_unresolved": True,
            "units": {},
            "any_primary_missing_from_o3": True,
            "reason": entry.get("error") or "unresolved in C manifest",
        }
    o3_cond = (entry.get("conditions") or {}).get("O3_full_support") or {}
    o3_ids = set(o3_cond.get("context_chunk_ids") or [])
    o3_texts = [
        _chunk_text(payload_index[cid])
        for cid in o3_ids
        if cid in payload_index and _chunk_text(payload_index[cid]).strip()
    ]
    o3_blob = "\n".join(o3_texts).lower()

    # Primary-unit resolution from C manifest when available (includes fallbacks)
    unit_res = entry.get("gold_unit_resolution") or {}

    units_out: dict[str, dict] = {}
    any_missing = False
    for u in question.primary_units():
        res_u = unit_res.get(u.provision_id) or {}
        corpus_hits = list(res_u.get("chunk_ids") or [])
        if not corpus_hits:
            corpus_hits = sorted(gold_chunk_ids_for_units([u], gold_index))
        in_o3 = sorted(cid for cid in corpus_hits if cid in o3_ids)
        # Distinctive probe: "section <n>" or bare section number near act name
        probes: list[str] = []
        if u.section:
            probes.append(f"section {u.section}".lower())
            probes.append(f"s.{u.section}".lower())
            probes.append(str(u.section))
        if u.act:
            # first significant words of the act title
            act_probe = re.sub(r"[^a-z ]", " ", u.act.lower())
            act_probe = " ".join(act_probe.split()[:4])
            if act_probe:
                probes.append(act_probe)
        probe_hits = sum(1 for p in probes if p and p in o3_blob)
        body_in_o3 = bool(in_o3) and (
            probe_hits >= 2 or (u.section is None and bool(in_o3))
        )
        # Unresolved in corpus OR resolved but zero O3 membership => missing from payload
        missing = (not corpus_hits) or (not in_o3)
        if missing:
            any_missing = True
        units_out[u.provision_id] = {
            "role": u.role,
            "section": u.section,
            "act": u.act,
            "resolution_method": res_u.get("method"),
            "corpus_chunk_ids_n": len(corpus_hits),
            "in_o3_n": len(in_o3),
            "in_corpus": bool(corpus_hits),
            "in_o3": bool(in_o3),
            "section_probe_in_o3": probe_hits,
            "body_in_o3": body_in_o3,
            "missing_from_o3": missing,
        }
    return {
        "o3_unresolved": False,
        "o3_chunk_ids_n": len(o3_ids),
        "units": units_out,
        "any_primary_missing_from_o3": any_missing,
    }


def preannotate(
    residual_qids: list[str],
    questions: dict[str, Any],
    manifest: dict,
    payload_index: dict[str, dict],
    f_statuses: dict[str, str],
) -> dict[str, dict]:
    """Deterministic pre-annotations. Never assigns a final Step 0 label.

    Strong evidence_missing signals:
      - primary gold unit absent from corpus index or from O3 payload
      - C-manifest unresolved
    Weak corroborating signal (still requires human confirm):
      - F1 justified_abstention_by_model (model named a missing element)
    """
    from evaluation.experiment_b_topk_eval import build_gold_index
    from evaluation.resolution import FamilyMap

    family_map = FamilyMap()
    gold_index = build_gold_index(payload_index, family_map) if payload_index else ({}, {})
    out: dict[str, dict] = {}
    for qid in residual_qids:
        q = questions.get(qid)
        if q is None:
            out[qid] = {"error": "question not found"}
            continue
        signals: list[str] = []
        evidence_missing_candidate = False
        if payload_index and manifest:
            check = _section_tokens_present_in_o3(
                qid, q, manifest, payload_index, family_map, gold_index
            )
            if check.get("o3_unresolved"):
                evidence_missing_candidate = True
                signals.append("c_manifest_unresolved")
            elif check.get("any_primary_missing_from_o3"):
                evidence_missing_candidate = True
                signals.append("primary_gold_absent_from_o3")
            out[qid] = {
                "primary_units": check.get("units", {}),
                "any_primary_missing_from_o3": check.get("any_primary_missing_from_o3"),
            }
        else:
            out[qid] = {"primary_units": {}, "any_primary_missing_from_o3": None}
            signals.append("payload_or_manifest_unavailable")

        fst = f_statuses.get(qid)
        if fst == "justified_abstention_by_model":
            signals.append("f1_justified_abstention")
            # corroborating only — do not alone set the candidate flag
        elif fst:
            signals.append(f"f1_{fst}")

        if q.insufficient_evidence:
            signals.append("benchmark_insufficient_evidence")

        rec = out[qid]
        rec.update(
            {
                "evidence_missing_candidate": evidence_missing_candidate,
                "signals": signals,
                "suggested_label": "evidence_missing" if evidence_missing_candidate else None,
                "needs_human_label": not evidence_missing_candidate,
                "note": (
                    "machine pre-annotation only; human must confirm final Step 0 label"
                ),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Residual-only worksheet
# --------------------------------------------------------------------------- #


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "correct" if v else "incorrect"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def answers_for(qid: str, d_perq: dict, e_perq: dict, c_perq: dict) -> dict[str, str | None]:
    d = d_perq.get(qid, {})
    e1 = e_perq.get(qid, {}).get("E1") or {}
    return {
        "C-O3": ((c_perq.get(qid, {}).get("oracle_conditions", {}).get("O3_full_support") or {}).get("answer")),
        "D2": (d.get("D2") or {}).get("answer"),
        "D3": (d.get("D3") or {}).get("answer"),
        "E1": e1.get("answer"),
    }


def build_residual_worksheet(
    residual_qids: list[str],
    questions: dict[str, Any],
    preanno: dict[str, dict],
    d_perq: dict,
    e_perq: dict,
    c_perq: dict,
    v2_perq: dict,
    widened: dict,
    out_path: Path = WORKSHEET,
) -> Path:
    conditions = ("C-O3", "D2", "D3", "E1")
    labels = ["A. C-O3", "B. D2", "C. D3", "D. E1"]
    lines: list[str] = [
        "# Step 0 Residual Worksheet (124 packets)",
        "",
        "Label **every** packet with exactly one verdict:",
        "",
        "1. `reference_narrow` — evidence supports a defensible reading the reference does not accept",
        "2. `evidence_missing` — operative statutory text absent from the O3 payload / index",
        "3. `model_wrong` — operative text present; model applied it wrongly",
        "",
        "No new model calls. Scorer is frozen. Machine pre-annotations are **suggestions only**.",
        "",
        "---",
        "",
    ]
    for qid in residual_qids:
        q = questions[qid]
        raw = q.raw
        refs = [q.acceptable_conclusion or ""]
        w = widened.get(qid)
        if w:
            refs.append(w["add"])
        pre = preanno.get(qid) or {}
        ans = answers_for(qid, d_perq, e_perq, c_perq)
        v2row = v2_perq.get(qid, {})
        sug = pre.get("suggested_label") or ""
        flags = ["[STEP0-RESIDUAL]"]
        if pre.get("evidence_missing_candidate"):
            flags.append("[PREANNO:evidence_missing]")
        lines += [
            f"### {qid} - {raw.get('difficulty', '?')} | {', '.join(raw.get('question_type', [])) or 'n/a'} | "
            + " | ".join(flags),
            "",
            f"**Question:** {d_perq.get(qid, {}).get('question') or raw.get('question')}",
            "",
        ]
        if w:
            lines += [
                f"**Reference (v2, widened):** {w['add']}",
                "",
                f"*(v1 reference: {refs[0] or '(none)'})*",
                "",
            ]
        else:
            lines += [f"**Reference:** {refs[0] or '(none recorded)'}", ""]
        if pre.get("signals"):
            lines += [f"**Pre-annotation signals:** `{', '.join(pre['signals'])}`", ""]
        if sug:
            lines += [f"**Machine suggestion (confirm or override):** `{sug}`", ""]
        for label, cond in zip(labels, conditions):
            a = ans.get(cond)
            rec = v2row.get(cond) or {}
            if rec.get("status") != "ok":
                lines += [f"**{label}:** *(not run)*", ""]
                continue
            v2, v1 = rec.get("v2") or {}, rec.get("v1") or {}
            lines += [
                f"**{label}:** *(v2: {_fmt(v2.get('correct'))}, soft {_fmt(v2.get('soft'))} | "
                f"v1: {_fmt(v1.get('correct'))}, soft {_fmt(v1.get('answer_correctness'))})*",
                "",
                "```",
                (a or "(empty)").strip(),
                "```",
                "",
            ]
        if sug:
            verdict_line = (
                f"- verdict: {sug}  # pre-annotated — confirm or override "
                "(reference_narrow | evidence_missing | model_wrong)"
            )
        else:
            verdict_line = (
                "- verdict: # required: reference_narrow | evidence_missing | model_wrong"
            )
        lines += [
            "**Your judgment:**",
            "",
            "- human_correct: ",
            verdict_line,
            "- model_action:  # fix_reference | add_instrument_text | contrastive_repair",
            "- category: ",
            "- notes: ",
            "",
            "---",
            "",
        ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# Label ingestion + validation + publish
# --------------------------------------------------------------------------- #


def normalize_labels(raw: Any, residual_qids: list[str]) -> dict[str, str]:
    """Accept {qid: label}, {\"labels\": {qid: label}}, or [{qid, verdict|label}, ...]."""
    if isinstance(raw, dict) and "labels" in raw and isinstance(raw["labels"], dict):
        raw = raw["labels"]
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                lab = str(v.get("verdict") or v.get("label") or "").strip()
            else:
                lab = str(v).strip()
            if k in residual_qids and lab:
                out[k] = lab
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            qid = item.get("qid") or item.get("question_id")
            lab = str(item.get("verdict") or item.get("label") or "").strip()
            if qid in residual_qids and lab:
                out[qid] = lab
    return out


def parse_residual_worksheet(path: Path) -> dict[str, str]:
    """Parse verdict lines from a residual worksheet (same grab rules as tabulate)."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n### (Q\d{3}) ", text)
    out: dict[str, str] = {}
    for i in range(1, len(blocks), 2):
        qid, body = blocks[i], blocks[i + 1]
        jz = re.search(
            r"\*\*(?:Your judgment|Prior judgment.*?):\*\*(.*?)(?=\n---|\Z)", body, re.S
        )
        j = jz.group(1) if jz else ""
        m = re.search(r"^[ \t]*-\s*verdict\s*(?:\([^)]*\))?\s*:[ \t]*(.*)$", j, re.I | re.M)
        if not m:
            continue
        lab = m.group(1).split("#", 1)[0].strip()
        if lab:
            out[qid] = lab
    return out


def validate_labels(
    labels: dict[str, str], residual_qids: list[str]
) -> dict[str, Any]:
    residual_set = set(residual_qids)
    missing = sorted(residual_set - set(labels))
    extra = sorted(set(labels) - residual_set)
    invalid = {qid: lab for qid, lab in labels.items() if lab not in STEP0_ENUM}
    valid = {qid: lab for qid, lab in labels.items() if qid in residual_set and lab in STEP0_ENUM}
    counts = dict(Counter(valid.values()))
    complete = not missing and not invalid and len(valid) == len(residual_set)
    return {
        "enum": list(STEP0_ENUM),
        "n_residual_total": len(residual_set),
        "n_labeled_valid": len(valid),
        "n_missing": len(missing),
        "n_invalid": len(invalid),
        "n_extra": len(extra),
        "missing_qids": missing,
        "invalid_labels": invalid,
        "extra_qids": extra,
        "labels": dict(sorted(valid.items())),
        "label_counts": counts,
        "step0_complete": complete,
    }


def publish_gate_targets(labels: dict[str, str]) -> dict[str, list[str]]:
    buckets = {
        "evidence_missing": sorted(q for q, v in labels.items() if v == "evidence_missing"),
        "model_wrong": sorted(q for q, v in labels.items() if v == "model_wrong"),
        "reference_narrow": sorted(q for q, v in labels.items() if v == "reference_narrow"),
    }
    meta = {
        "evidence_missing": (
            OUT / "step0_corpus_fill_targets.json",
            "add missing instrument text to corpus; re-retrieve qid only; one answer call",
            "gold span now in payload AND binary correctness rises on that id",
            "section not in index — stop, do not loop retrieval",
        ),
        "model_wrong": (
            OUT / "step0_contrastive_targets.json",
            "one contrastive span-cut call; options cut from retrieved text with section id/authority/operative sentence",
            "cited span is verbatim substring of context AND operative sentence changed",
            "section-number-only swap, span not in context, or abstains on element absent from new payload — keep D2",
        ),
        "reference_narrow": (
            OUT / "step0_dual_score_targets.json",
            "change the reference or report a second score; do not change the model",
            "both old and new score reported until references fixed",
            "a generation call was spent chasing the narrow reference",
        ),
    }
    for label, qids in buckets.items():
        path, intervention, keep_if, reject_if = meta[label]
        path.write_text(
            json.dumps(
                {
                    "label": label,
                    "n": len(qids),
                    "qids": qids,
                    "intervention": intervention,
                    "keep_if": keep_if,
                    "reject_if": reject_if,
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return buckets


def publish(validation: dict[str, Any], source: str, preanno: dict | None = None) -> Path:
    payload = dict(validation)
    payload["source"] = source
    payload["scorer"] = "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay"
    payload["note"] = (
        "publish per-label counts before any aggregate soft-score claim (plan sec 5.2/Step 3)"
    )
    if preanno is not None:
        payload["n_evidence_missing_candidates"] = sum(
            1 for v in preanno.values() if v.get("evidence_missing_candidate")
        )
    LABELS_OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    publish_gate_targets(payload["labels"])
    return LABELS_OUT


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_jsonl_map(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                out[r["qid"]] = r
    return out


def _load_widened() -> dict:
    overlay_path = ROOT / "evaluation" / "evaluator_v2_overlay.json"
    if not overlay_path.exists():
        return {}
    return json.loads(overlay_path.read_text(encoding="utf-8")).get("widened_conclusions", {})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 0 — label the residual (no model calls)")
    ap.add_argument("--preannotate", action="store_true", help="write step0_preannotations.json")
    ap.add_argument("--worksheet", action="store_true", help="write residual-only worksheet")
    ap.add_argument(
        "--labels",
        type=Path,
        default=None,
        help="JSON file with {qid: label} or {\"labels\": {...}} or [{qid, verdict}]",
    )
    ap.add_argument(
        "--from-worksheet",
        action="store_true",
        help="parse reviewed residual worksheet for labels",
    )
    ap.add_argument(
        "--reviewed",
        type=Path,
        default=OUT / "step0_residual_worksheet_reviewed.md",
        help="reviewed residual worksheet path",
    )
    ap.add_argument("--publish", action="store_true", help="validate + write step0_residual_labels.json")
    ap.add_argument(
        "--require-complete",
        action="store_true",
        help="exit 2 when residual labels are incomplete/invalid",
    )
    args = ap.parse_args(argv)

    residual_qids = load_residual_qids()
    print(f"residual: {len(residual_qids)} qids")

    preanno: dict[str, dict] = {}
    if args.preannotate or args.worksheet:
        questions = load_questions()
        manifest = load_c_manifest()
        payload_index = load_payload_index()
        f_statuses = load_f_statuses()
        if args.preannotate:
            preanno = preannotate(residual_qids, questions, manifest, payload_index, f_statuses)
            PREANNO.write_text(
                json.dumps(
                    {
                        "n": len(preanno),
                        "n_evidence_missing_candidates": sum(
                            1 for v in preanno.values() if v.get("evidence_missing_candidate")
                        ),
                        "method": "C-manifest O3 payload ∩ corpus gold units + F1 justified signals",
                        "note": "suggestions only; human confirms final Step 0 labels",
                        "annotations": preanno,
                    },
                    indent=1,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            n_cand = sum(1 for v in preanno.values() if v.get("evidence_missing_candidate"))
            print(f"preannotations: {len(preanno)} ({n_cand} evidence_missing candidates) -> {PREANNO.name}")
        if args.worksheet:
            if not preanno:
                if PREANNO.exists():
                    preanno = json.loads(PREANNO.read_text(encoding="utf-8")).get("annotations", {})
                else:
                    preanno = preannotate(
                        residual_qids, questions, manifest, payload_index, f_statuses
                    )
            d_perq = _load_jsonl_map(OUT / "experiment_D_per_question.jsonl")
            e_perq = _load_jsonl_map(OUT / "experiment_E_per_question.jsonl")
            c_perq = (
                json.loads((OUT / "experiment_C_per_question.json").read_text(encoding="utf-8"))
                if (OUT / "experiment_C_per_question.json").exists()
                else {}
            )
            v2_perq = _load_jsonl_map(OUT / "evaluator_v2_per_question.jsonl")
            path = build_residual_worksheet(
                residual_qids,
                questions,
                preanno,
                d_perq,
                e_perq,
                c_perq,
                v2_perq,
                _load_widened(),
            )
            print(f"worksheet: {len(residual_qids)} packets -> {path}")

    labels: dict[str, str] = {}
    sources: list[str] = []
    if args.labels:
        if not args.labels.exists():
            raise SystemExit(f"labels file not found: {args.labels}")
        raw = json.loads(args.labels.read_text(encoding="utf-8"))
        labels.update(normalize_labels(raw, residual_qids))
        sources.append(args.labels.name)
    if args.from_worksheet:
        parsed = parse_residual_worksheet(args.reviewed)
        # worksheet labels only for residual qids
        for qid, lab in parsed.items():
            if qid in residual_qids:
                labels[qid] = lab
        sources.append(args.reviewed.name)

    if args.publish or args.require_complete:
        if not labels and LABELS_OUT.exists() and not args.labels and not args.from_worksheet:
            # re-validate previously published labels
            prev = json.loads(LABELS_OUT.read_text(encoding="utf-8"))
            labels = prev.get("labels") or {}
            sources.append(LABELS_OUT.name + "(revalidated)")
        if PREANNO.exists() and not preanno:
            preanno = json.loads(PREANNO.read_text(encoding="utf-8")).get("annotations", {})
        validation = validate_labels(labels, residual_qids)
        src = "+".join(sources) if sources else "none"
        out_path = publish(validation, src, preanno or None)
        print(
            f"step0_complete={validation['step0_complete']} "
            f"labeled={validation['n_labeled_valid']}/{validation['n_residual_total']} "
            f"counts={validation['label_counts']} missing={validation['n_missing']} "
            f"invalid={validation['n_invalid']}"
        )
        print(f"wrote {out_path.name} + gate targets")
        if args.require_complete and not validation["step0_complete"]:
            print(
                "INCOMPLETE: label every residual qid with exactly one of "
                + " | ".join(STEP0_ENUM),
                file=sys.stderr,
            )
            return 2
    elif not (args.preannotate or args.worksheet or labels):
        ap.print_help()
        return 1

    if labels and not (args.publish or args.require_complete):
        validation = validate_labels(labels, residual_qids)
        print(
            f"labels loaded: {validation['n_labeled_valid']}/{validation['n_residual_total']} "
            f"complete={validation['step0_complete']} counts={validation['label_counts']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
