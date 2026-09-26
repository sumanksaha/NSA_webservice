"""Step 0 — interactive human review walker (no model calls, no network).

Walks the residual packets from ``step0_residual_worksheet.md`` inputs one at a
time so a human can assign exactly one Step 0 label per qid:

  r  reference_narrow   — evidence supports a defensible reading the reference rejects
  e  evidence_missing   — operative statutory text absent from the O3 payload / index
  m  model_wrong        — operative text present; the model applied it wrongly

Session state is written after every verdict, so interrupting loses nothing:

  evaluation/out/ceiling_v5/step0_review_progress.json   (full state, resumable)
  evaluation/out/ceiling_v5/step0_human_labels.json      (clean {qid: label})

When every residual qid is labeled, publish with the existing gate machinery:

  SKIP_SCHEMA_CHECK=1 .venv/Scripts/python.exe evaluation/step0_label_residual.py \
      --labels evaluation/out/ceiling_v5/step0_human_labels.json \
      --publish --require-complete

Usage:
  .venv/Scripts/python.exe evaluation/step0_human_review.py
  .venv/Scripts/python.exe evaluation/step0_human_review.py --short
  .venv/Scripts/python.exe evaluation/step0_human_review.py --candidates-first
  .venv/Scripts/python.exe evaluation/step0_human_review.py --start Q068
  .venv/Scripts/python.exe evaluation/step0_human_review.py --from-reviewed

In-session commands: r/e/m verdict, s skip, b back, n note, t trim, j QID jump,
u undo, l stats, ? help, q save+quit.

CSV import (--from-csv) accepts the worksheet review schema as exported to
spreadsheets: packet_no,question_id,human_correct,verdict,model_action,category,
notes. Verdict + notes are kept in the session; human_correct / model_action /
category are preserved verbatim in step0_human_records.json for the audit trail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path

warnings.filterwarnings("ignore")

# The question loader's import chain boots the Flask app, which runs a schema
# check against the local SQLite file. Step 0 never touches the database; skip
# the check exactly like the --preannotate run does (see step0_label_residual).
os.environ.setdefault("SKIP_SCHEMA_CHECK", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

import evaluation.step0_label_residual as s0

OUT = s0.OUT
PREANNO = s0.PREANNO
WORKSHEET = s0.WORKSHEET
PROGRESS = OUT / "step0_review_progress.json"
LABELS_OUT = OUT / "step0_human_labels.json"

VERDICT_BY_KEY = {"r": "reference_narrow", "e": "evidence_missing", "m": "model_wrong"}
SHORT_LEN = 700
CONDITIONS = ("C-O3", "D2", "D3", "E1")
CONDITION_LETTERS = ("A", "B", "C", "D")


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #


def parse_verdict(raw: str) -> str | None:
    """Map 'r'/'e'/'m' or a full enum word to a Step 0 label; None if invalid."""
    v = raw.strip().lower()
    if v in VERDICT_BY_KEY:
        return VERDICT_BY_KEY[v]
    if v in s0.STEP0_ENUM:
        return v
    return None


def truncate_text(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[:limit].rstrip() + f" ... [+{len(text) - limit} chars]"


def candidates_first_order(residual_qids: list[str], preanno: dict) -> list[str]:
    """Pre-annotated evidence_missing candidates first, then the rest, both by qid."""
    candidates = sorted(
        q for q in residual_qids if (preanno.get(q) or {}).get("evidence_missing_candidate")
    )
    rest = sorted(q for q in residual_qids if q not in set(candidates))
    return candidates + rest


def emit_labels(state: dict) -> dict[str, str]:
    """Clean {qid: label} for step0_label_residual --labels (skips unlabeled)."""
    out: dict[str, str] = {}
    for qid in state.get("order", []):
        rec = state.get("verdicts", {}).get(qid) or {}
        lab = rec.get("verdict")
        if lab in s0.STEP0_ENUM:
            out[qid] = lab
    return out


def load_state(residual_qids: list[str], path: Path = PROGRESS) -> dict:
    """Load a previous session if present; reconcile its order with the residual set."""
    state: dict = {"order": list(residual_qids), "verdicts": {}}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
        kept = [q for q in (prev.get("order") or []) if q in set(residual_qids)]
        for q in residual_qids:
            if q not in kept:
                kept.append(q)
        state["order"] = kept
        residual = set(residual_qids)
        state["verdicts"] = {
            q: rec for q, rec in (prev.get("verdicts") or {}).items() if q in residual
        }
        state["started"] = prev.get("started")
    return state


def save_state(state: dict, progress_path: Path = PROGRESS, labels_path: Path = LABELS_OUT) -> None:
    """Atomic write of session state + clean labels file."""
    state["updated"] = now()
    state.setdefault("started", state["updated"])
    for path, payload in (
        (progress_path, state),
        (labels_path, emit_labels(state)),
    ):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Packet assembly (same JSON sources as the worksheet builder)
# --------------------------------------------------------------------------- #


def load_preannotations() -> dict:
    if PREANNO.exists():
        return json.loads(PREANNO.read_text(encoding="utf-8")).get("annotations", {})
    return {}


def load_csv_records(path: Path, residual_qids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Parse a filled review CSV (packet_no,question_id,human_correct,verdict,...).

    Returns (records keyed by residual qid, skipped rows described as strings).
    Verdicts outside the Step 0 enum or qids outside the residual set are skipped.
    """
    import csv as _csv

    residual = set(residual_qids)
    records: dict[str, dict] = {}
    skipped: list[str] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(_csv.DictReader(f), start=2):
            qid = (row.get("question_id") or "").strip()
            verdict = (row.get("verdict") or "").strip().lower()
            if qid not in residual:
                skipped.append(f"row {i}: qid {qid or '(empty)'} not in residual set")
                continue
            if verdict not in s0.STEP0_ENUM:
                skipped.append(f"row {i} ({qid}): invalid verdict {verdict!r}")
                continue
            records[qid] = {
                "verdict": verdict,
                "notes": (row.get("notes") or "").strip(),
                "human_correct": (row.get("human_correct") or "").strip().lower(),
                "model_action": (row.get("model_action") or "").strip().lower(),
                "category": (row.get("category") or "").strip(),
            }
    return records, skipped


def load_score_rows() -> tuple[dict, dict, dict, dict]:
    d_perq = s0._load_jsonl_map(OUT / "experiment_D_per_question.jsonl")
    e_perq = s0._load_jsonl_map(OUT / "experiment_E_per_question.jsonl")
    c_path = OUT / "experiment_C_per_question.json"
    c_perq = json.loads(c_path.read_text(encoding="utf-8")) if c_path.exists() else {}
    v2_perq = s0._load_jsonl_map(OUT / "evaluator_v2_per_question.jsonl")
    return d_perq, e_perq, c_perq, v2_perq


def build_packets(residual_qids: list[str]) -> tuple[list[dict], dict]:
    questions: dict = {}
    try:
        questions = s0.load_questions()
    except Exception as exc:  # difficulty/question_type are optional context
        print(f"(note: benchmark questions unavailable: {exc})")
    d_perq, e_perq, c_perq, v2_perq = load_score_rows()
    widened = s0._load_widened()
    preanno = load_preannotations()
    packets: list[dict] = []
    for qid in residual_qids:
        q = questions.get(qid)
        raw = getattr(q, "raw", {}) or {}
        w = widened.get(qid)
        pre = preanno.get(qid) or {}
        packets.append(
            {
                "qid": qid,
                "difficulty": raw.get("difficulty", "?"),
                "question_type": ", ".join(raw.get("question_type", []) or []) or "n/a",
                "question": (d_perq.get(qid, {}).get("question") or raw.get("question") or "?"),
                "reference": (w or {}).get("add") or (getattr(q, "acceptable_conclusion", None) if q else None)
                or "(none recorded)",
                "v1_reference": (w or {}).get("v1_reference") or (q.acceptable_conclusion if q else None),
                "signals": pre.get("signals", []),
                "suggestion": pre.get("suggested_label"),
                "answers": s0.answers_for(qid, d_perq, e_perq, c_perq),
                "v2row": v2_perq.get(qid, {}),
            }
        )
    return packets, preanno


# --------------------------------------------------------------------------- #
# Display
# --------------------------------------------------------------------------- #


def format_packet(packet: dict, short: bool, note: str | None) -> str:
    qid = packet["qid"]
    limit = SHORT_LEN if short else None
    lines = [
        "=" * 78,
        f"[{qid}] {packet['difficulty']} | {packet['question_type']}",
        "=" * 78,
        f"QUESTION:\n{packet['question']}\n",
        f"REFERENCE (v2 widened):\n{truncate_text(packet['reference'], limit)}\n",
    ]
    if packet.get("v1_reference") and packet["v1_reference"] != packet["reference"]:
        lines += [f"(v1 reference: {packet['v1_reference']})\n"]
    if packet["signals"]:
        lines.append(f"signals: {', '.join(packet['signals'])}")
    if packet["suggestion"]:
        lines.append(f"machine suggestion: {packet['suggestion']}  [suggestion only — confirm or override]")
    if packet["signals"] or packet["suggestion"]:
        lines.append("")
    for letter, cond in zip(CONDITION_LETTERS, CONDITIONS, strict=False):
        rec = packet["v2row"].get(cond) or {}
        if rec.get("status") != "ok":
            lines.append(f"[{letter}. {cond}] (not run)")
            continue
        v2, v1 = rec.get("v2") or {}, rec.get("v1") or {}
        lines.append(
            f"[{letter}. {cond}] (v2: {s0._fmt(v2.get('correct'))}, soft {s0._fmt(v2.get('soft'))}"
            f" | v1: {s0._fmt(v1.get('correct'))}, soft {s0._fmt(v1.get('answer_correctness'))})"
        )
        body = (packet["answers"].get(cond) or "(empty)").strip()
        lines.append(truncate_text(body, limit))
        lines.append("")
    if note:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def stats_line(state: dict) -> str:
    verdicts = state["verdicts"]
    done = [v["verdict"] for v in verdicts.values() if v.get("verdict")]
    counts = dict(sorted(Counter(done).items()))
    remaining = len(state["order"]) - len(done)
    return f"done {len(done)}/{len(state['order'])} {counts} | remaining {remaining}"


HELP = (
    "commands: r=reference_narrow e=evidence_missing m=model_wrong | "
    "s skip | b back | n note | t trim | j QID jump | u undo | l stats | ? help | q save+quit"
)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 0 — interactive human review walker")
    ap.add_argument("--short", action="store_true", help="truncate answer text to ~700 chars")
    ap.add_argument("--start", default=None, metavar="QID", help="begin at a specific qid")
    ap.add_argument("--candidates-first", action="store_true", help="review pre-annotated evidence_missing candidates first")
    ap.add_argument(
        "--from-reviewed",
        action="store_true",
        help="import verdicts already written into the reviewed worksheet",
    )
    ap.add_argument(
        "--reviewed",
        type=Path,
        default=OUT / "step0_residual_worksheet_reviewed.md",
        help="reviewed worksheet path for --from-reviewed",
    )
    ap.add_argument(
        "--from-csv",
        type=Path,
        default=None,
        metavar="PATH",
        help="import verdicts from a filled review CSV (question_id,verdict,notes,...)",
    )
    args = ap.parse_args(argv)

    residual_qids = s0.load_residual_qids()
    state = load_state(residual_qids)
    order = state["order"]
    verdicts = state["verdicts"]

    if args.from_reviewed:
        imported = s0.parse_residual_worksheet(args.reviewed)
        n0 = len(emit_labels(state))
        for qid, lab in imported.items():
            if qid in set(residual_qids) and lab in s0.STEP0_ENUM:
                verdicts[qid] = {"verdict": lab, "notes": "", "ts": now()}
        save_state(state)
        print(f"imported from {args.reviewed.name}: {len(emit_labels(state)) - n0} new verdicts")

    if args.from_csv:
        if not args.from_csv.exists():
            print(f"csv not found: {args.from_csv}", file=sys.stderr)
            return 1
        records, skipped = load_csv_records(args.from_csv, residual_qids)
        n0 = len(emit_labels(state))
        for qid, rec in records.items():
            verdicts[qid] = {**rec, "ts": now()}
        save_state(state)
        records_path = OUT / "step0_human_records.json"
        records_path.write_text(
            json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"imported from {args.from_csv.name}: {len(emit_labels(state)) - n0} new verdicts"
            f" ({len(records)} rows parsed, {len(skipped)} skipped)"
        )
        for line in skipped:
            print(f"  skipped: {line}")
        print(f"audit records -> {records_path.name}")

    packets, _ = build_packets(residual_qids)
    by_qid = {p["qid"]: p for p in packets}

    if args.candidates_first and not PROGRESS.exists():
        order = candidates_first_order(residual_qids, load_preannotations())
        state["order"] = order
        save_state(state)

    print(f"Step 0 human review — {stats_line(state)}")
    print(HELP)

    if args.start:
        if args.start not in set(order):
            print(f"unknown qid: {args.start}", file=sys.stderr)
            return 1
        cursor = order.index(args.start)
    else:
        cursor = next((i for i, q in enumerate(order) if not (verdicts.get(q) or {}).get("verdict")), len(order))

    short = bool(args.short)
    history: list[tuple[str, dict | None]] = []
    saved_cursor = cursor

    def next_unlabeled(i: int) -> int:
        return next((j for j in range(i, len(order)) if not (verdicts.get(order[j]) or {}).get("verdict")), len(order))

    while 0 <= cursor < len(order):
        qid = order[cursor]
        rec = verdicts.get(qid) or {}
        if rec.get("verdict") and cursor == saved_cursor and not args.start:
            cursor = next_unlabeled(cursor)
            continue
        packet = by_qid.get(qid)
        if packet is None:
            print(f"(no packet data for {qid}; skipping)")
            cursor += 1
            continue
        print(format_packet(packet, short, rec.get("notes")))
        try:
            raw = input(f"[{stats_line(state)} | #{cursor + 1} {qid}] verdict > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n(save + exit)")
            break
        saved_cursor = -1

        if raw in ("q", "quit", "exit"):
            break
        if raw in ("?", "h", "help") or raw == "":
            print(HELP)
            continue
        key = raw.split()[0].lower() if raw else ""
        if key in VERDICT_BY_KEY or raw.lower() in s0.STEP0_ENUM:
            lab = parse_verdict(raw)
            if lab is None:
                print(f"invalid verdict: {raw}")
                continue
            history.append((qid, verdicts.get(qid)))
            verdicts[qid] = {"verdict": lab, "notes": rec.get("notes", ""), "ts": now()}
            save_state(state)
            cursor = next_unlabeled(cursor + 1)
        elif key == "s":
            cursor = next_unlabeled(cursor + 1)
        elif key == "b":
            cursor = max(0, cursor - 1)
        elif key == "n":
            try:
                note = input("note: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("")
                continue
            history.append((qid, verdicts.get(qid)))
            verdicts[qid] = {"verdict": rec.get("verdict"), "notes": note, "ts": now()}
            save_state(state)
        elif key == "t":
            short = not short
            print(f"trim mode: {'ON (~700 chars)' if short else 'OFF (full text)'}")
        elif key == "j":
            target = raw.split()[1] if len(raw.split()) > 1 else ""
            if target in set(order):
                cursor = order.index(target)
            else:
                print(f"unknown qid: {target}")
        elif key == "u":
            if not history:
                print("nothing to undo")
                continue
            uqid, prev = history.pop()
            if prev is None:
                verdicts.pop(uqid, None)
            else:
                verdicts[uqid] = prev
            save_state(state)
            cursor = order.index(uqid)
            print(f"undid {uqid}")
        elif key == "l":
            print(stats_line(state))
        else:
            print(f"unknown command: {raw!r} — {HELP}")

    save_state(state)
    print("\n" + stats_line(state))
    labels_rel = LABELS_OUT.relative_to(ROOT)
    complete = len(emit_labels(state)) == len(order)
    if complete:
        print("\nAll residual qids labeled. Publish the Step 0 gates with:")
        print(
            f"  SKIP_SCHEMA_CHECK=1 .venv/Scripts/python.exe evaluation/step0_label_residual.py"
            f" --labels {labels_rel} --publish --require-complete"
        )
    else:
        remaining = [q for q in order if not (verdicts.get(q) or {}).get("verdict")]
        print(f"\nResume later with: evaluation/step0_human_review.py --start {remaining[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
