"""WF-6 — reference widening for the 14 ``widen_overlay`` qids (0 LLM calls).

The triage verdict: 14 qids have NO v2 widening at all (Q012, Q028, Q068,
Q071, Q083, Q090, Q119, Q128, Q132, Q133, Q134, Q135, Q137, Q146). Authoring
gold reference text is a **legal review task and is deliberately left to a
human** (repo rule; design review R3). This module therefore provides the three
machine stages around that task — never the task itself:

**Stage 1 — build packets** (``--build-packets``)
  Per qid, one human-editable markdown packet carrying: the question, the audit
  verdict + reviewer notes + the failing arm scores, the CURRENT frozen
  references (conclusions/concepts), the gold evidence anchors (payload texts —
  the source any widened reading must quote), and the four audited arm answers
  (what failed and how). A labeled ``### WF6-ENTRY`` block tells the reviewer
  exactly what to fill; nothing is machine-drafted.

**Stage 2 — validate packets** (``--validate-packets``)
  Replays the ``step0_dual_score_widen.validate_widening`` gates over the
  filled blocks: add-length bounds, anchor quote verbatim-in-evidence, no v1
  duplicate, no stored-answer paraphrase. Invalid blocks get exact reasons and
  stay unmerged — never silently dropped.

**Stage 3 — merge approved entries** (``--merge-from-packets``)
  Additive-only bundle merge (existing conclusions are never narrowed or
  dropped), provenance recorded per entry, bundle version bumped, checksums
  re-verified — the ``evaluation/ruler`` boot rules, enforced by the tests.

Usage:
    python -m evaluation.widen_references_v4 --build-packets
    python -m evaluation.widen_references_v4 --validate-packets
    python -m evaluation.widen_references_v4 --merge-from-packets

The ``reference_narrow`` partial cohort (Q042, Q043, Q148) is a separate lane;
build its packets with ``--qids``/``--state`` so the default 14-qid run is not
clobbered:

    python -m evaluation.widen_references_v4 --build-packets \
        --qids Q042,Q043,Q148 \
        --state evaluation/out/ceiling_v5/widen_v4/drafts_state_refnarrow.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
PACKET_DIR = OUT / "widen_v4" / "packets"
STATE_PATH = OUT / "widen_v4" / "drafts_state.json"
MERGE_REPORT = OUT / "widen_v4" / "merge_report.json"
BUNDLE_PATH = PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json"
C_PERQ = OUT / "experiment_C_per_question.json"
TABULATION = OUT / "full_review_tabulation.json"
WORKSHEET = OUT / "full_review_worksheet_completed.md"

#: the widener's exact mechanical gates — same thresholds as step0 (r4 line 101-104)
MIN_ADD_CHARS = 40
MAX_ADD_CHARS = 2000
V1_DUP_MAX_OVERLAP = 0.95
INDEPENDENCE_MAX_OVERLAP = 0.85

MAX_ANCHORS = 12
ANCHOR_EXCERPT_CHARS = 1200
ARM_EXCERPT_CHARS = 2400

WIDEN_QIDS = (
    "Q012",
    "Q028",
    "Q068",
    "Q071",
    "Q083",
    "Q090",
    "Q119",
    "Q128",
    "Q132",
    "Q133",
    "Q134",
    "Q135",
    "Q137",
    "Q146",
)

#: 2026-10-20 — the ``reference_narrow`` partial cohort from the full-150 human
#: review. These already carry conclusions, but the reference is too strict, so
#: a defensible model answer is graded partial. They are NOT part of the default
#: ``widen_overlay`` run; build their packets in a separate lane:
#:
#:     python -m evaluation.widen_references_v4 --build-packets \
#:         --qids Q042,Q043,Q148 \
#:         --state evaluation/out/ceiling_v5/widen_v4/drafts_state_refnarrow.json
#:
#: The legal text is still authored by a human (repo rule; design review R3).
REFERENCE_NARROW_QIDS = ("Q042", "Q043", "Q148")

_ENTRY_FIELDS = ("add", "anchor_quote", "note")


class _Ctx(dict):
    pass


def load_context() -> _Ctx:
    """Bundle, tabulation, worksheet answers, gold anchors — the packet material."""
    from evaluation.build_references_v4 import verify_bundle
    from evaluation.v3_component_rescore import parse_worksheet

    verdict = verify_bundle()
    if not verdict.get("ok"):
        raise SystemExit(f"reference bundle {verdict.get('status')!r} — build/verify it first (WF-4)")
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    tab = {r["qid"]: r for r in (json.loads(TABULATION.read_text(encoding="utf-8")).get("records") or [])}
    perq = json.loads(C_PERQ.read_text(encoding="utf-8")) if C_PERQ.exists() else {}
    payload_index: dict[str, dict[str, Any]] = {}
    for line in (
        (PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        if line.strip():
            rec = json.loads(line)
            payload_index[str(rec.get("id"))] = rec.get("payload") or {}
    arms = parse_worksheet(WORKSHEET) if WORKSHEET.exists() else {}
    return _Ctx(
        bundle=bundle,
        tabulation=tab,
        perq=perq,
        payload_index=payload_index,
        worksheet_arms=arms,
    )


def gold_anchors(perq_qid: dict[str, Any], payload_index: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """The gold evidence texts behind a qid — what a widening must quote from.

    ``gold_payload_ids`` maps gold unit → list of retrieval chunk ids; every
    chunk under every gold unit is an anchor. Chunks beyond ``MAX_ANCHORS``
    keep their ids listed so the reviewer can pull the full text by hand.
    """
    chunk_ids: list[str] = []
    for ids in (perq_qid.get("gold_payload_ids") or {}).values():
        for cid in ids or []:
            cid = str(cid)
            if cid not in chunk_ids:
                chunk_ids.append(cid)
    out: list[dict[str, str]] = []
    for cid in chunk_ids[:MAX_ANCHORS]:
        payload = payload_index.get(cid) or {}
        text = payload.get("chunk_text") or ""
        if text.strip():
            out.append({
                "chunk_id": cid,
                "anchor_text": text[:ANCHOR_EXCERPT_CHARS]
                + (
                    f"…[truncated — read full text in evaluation/out/cache/payload_index.jsonl, id {cid}]"
                    if len(text) > ANCHOR_EXCERPT_CHARS
                    else ""
                ),
                "act_name": str(payload.get("act_name") or ""),
                "section_number": str(payload.get("section_number") or ""),
            })
    skipped = chunk_ids[MAX_ANCHORS:]
    if skipped:
        out.append({
            "chunk_id": f"(+{len(skipped)} more gold chunks: {', '.join(skipped)})",
            "anchor_text": f"[ids only — {(perq_qid.get('question') or '').strip()[:0]}pull full texts from evaluation/out/cache/payload_index.jsonl]",
            "act_name": "",
            "section_number": "",
        })
    return out


def build_packets(
    out_dir: Path = PACKET_DIR,
    qids: tuple[str, ...] | None = None,
    state_path: Path = STATE_PATH,
) -> dict[str, Any]:
    ctx = load_context()
    out_dir = Path(out_dir)
    (out_dir).mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {}
    for qid in (qids or WIDEN_QIDS):
        qrec = (ctx["perq"] or {}).get(qid) or {}
        trec = (ctx["tabulation"] or {}).get(qid) or {}
        bref = (ctx["bundle"].get("questions") or {}).get(qid) or {}
        anchors = gold_anchors(qrec, ctx["payload_index"])
        arms = (ctx["worksheet_arms"] or {}).get(qid) or {}
        line = []

        line.append(f"# WF-6 widening packet — {qid}")
        line.append("")
        line.append("Instructions for the human legal reviewer (packets only — no text is authored for you):")
        line.append("1. Read the question, the audit's verdict/notes, and the failing answers below.")
        line.append(
            f"2. Confirm the gold evidence anchors cover the correct statutory reading; quote a VERBATIM phrase as `anchor_quote` (it is mechanically checked, so it must appear exactly as written in the anchor excerpt)."
        )
        line.append(
            "3. Write your widened reading in `add:` — same rules as the v2 overlay widening gates: 40–2000 chars; must NOT duplicate the v1 conclusion; must NOT paraphrase the stored failing answers (those checks run with the same thresholds)."
        )
        line.append(
            "4. Machine gates + merge: `--validate-packets`, then `--merge-from-packets` (additive; bundle version bumps automatically)."
        )
        line.append("")
        line.append("## Question + audit record")
        line.append(f"- question: {((qrec or {}).get('question') or '').strip()}")
        line.append(
            f"- gold units: {', '.join(qrec.get('gold_unit_ids') or [])} (n_gold_chunks_total={qrec.get('n_gold_chunks_total')})"
        )
        line.append(
            f"- audit verdict: {trec.get('verdict')} | human_correct: {trec.get('human_correct')} | model_action: {trec.get('model_action')}"
        )
        if isinstance(trec.get("notes"), str) and trec.get("notes").strip():
            for note_line in str(trec["notes"]).splitlines():
                if note_line.strip():
                    line.append(f"- audit note: {note_line.strip()}")

        machine = trec.get("machine") or {}
        for arm in ("C-O3", "D2", "D3", "E1"):
            row = machine.get(arm) or {}
            line.append(f"- arm {arm}: v2_soft={row.get('v2_soft')} v1={row.get('answer_correctness')}")
        line.append("")
        line.append(
            "## Current frozen references (bundle " + str(ctx["bundle"].get("meta", {}).get("bundle_version")) + ")"
        )
        line.append("- v1_conclusion: " + "; ".join(bref.get("conclusions") or []))
        line.append("- gold_concepts: " + "; ".join(bref.get("concepts") or []))
        line.append(f"- expected_abstain: {bref.get('expected_abstain')}")
        line.append("")
        line.append("## Gold evidence anchors (payload texts — quote verbatim from these)")
        for a in anchors:
            line.append(f"### anchor {a['chunk_id']}  ({a['act_name']} {a['section_number']})")
            line.append(a["anchor_text"])
            line.append("")
        line.append("## The audited failures (what the machine missed)")
        for arm in ("C-O3", "D2", "D3", "E1"):
            text = arms.get(arm) or ""
            line.append(f"<details><summary>{arm} answer ({len(text)} chars)</summary>\n")
            line.append(text[:ARM_EXCERPT_CHARS])
            line.append("</details>")
        line.append("")
        line.append("### WF6-ENTRY (fill ALL three fields; the machine validates them)")
        line.append("add: <your widened reading — one paragraph, 40..2000 chars>")
        line.append("anchor_quote: <verbatim quote from an anchor above>")
        line.append("note: <why this reading belongs to the gold set / what the audit missed>")

        packet = out_dir / f"{qid}.md"
        packet.write_text("\n".join(line), encoding="utf-8")
        state[qid] = {
            "packet": str(packet),
            "status": "pending_human",
            "merged": None,
            "validated": None,
            "anchors": len(anchors),
        }
    Path(state_path).write_text(
        json.dumps({"stage": "built", "packets": state}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    from evaluation.ruler import format_publication_header, publication_header

    print(format_publication_header(publication_header()))
    print(f"\nbuilt packets for {len(state)} qids under {out_dir}")
    for qid, rec in sorted(state.items()):
        print(f"  {qid}: anchors={rec['anchors']} status={rec['status']}")
    print(f"state: {state_path}")
    print("NEXT: human legal review edits the `### WF6-ENTRY` blocks; then --validate-packets.")
    return {"state": state, "out_dir": str(out_dir)}


def parse_entry(md_text: str) -> dict[str, str]:
    """Parse the labeled `### WF6-ENTRY` fields (multi-line-capable, case-insensitive)."""
    section = md_text.split("### WF6-ENTRY", 1)
    if len(section) < 2:
        return {}
    entry: dict[str, str] = {}
    current = None
    for raw in section[1].splitlines():
        line = raw.strip()
        m = re.match(r"^(add|anchor_quote|note)\s*[:：]\s*(.*)$", line, flags=re.IGNORECASE)
        if m:
            current = m.group(1).lower()
            entry.setdefault(current, m.group(2).rstrip())
            continue
        if current and line and not line.startswith("<"):
            entry[current] = (entry.get(current, "") + " " + line).strip()
    return {k: str(v).strip() for k, v in entry.items() if v}


_TEMPLATE_MARKS = ("<your ", "<verbatim", "<why ")


def is_template(entry: dict[str, str]) -> bool:
    """Untouched packets carry the instruction placeholders — not a real entry."""
    add = (entry.get("add") or "") + (entry.get("anchor_quote") or "") + (entry.get("note") or "")
    return any(mark in add.lower() for mark in _TEMPLATE_MARKS)


def validate_entry(qid: str, entry: dict[str, str], ctx: dict[str, Any]) -> dict[str, Any]:
    """Same gates as step0_dual_score_widen.validate_widening, local material."""
    add = str(entry.get("add") or "")
    quote = str(entry.get("anchor_quote") or "")
    reasons: list[str] = []
    if len(add) < MIN_ADD_CHARS:
        reasons.append(f"add too short (<{MIN_ADD_CHARS} chars) to state a statutory reading")
    if len(add) > MAX_ADD_CHARS:
        reasons.append(f"add too long (>{MAX_ADD_CHARS} chars) — tighten to the operative reading")
    if not quote:
        reasons.append("anchor_quote missing — the statute anchor is mandatory")
    else:
        anchors = gold_anchors((ctx["perq"] or {}).get(qid) or {}, ctx["payload_index"])
        if not any(quote in a["anchor_text"] for a in anchors):
            reasons.append(
                "anchor_quote is NOT a verbatim substring of a gold anchor — if truly absent, label is evidence_missing, not reference_narrow"
            )
    v1 = (((ctx["bundle"].get("questions") or {}).get(qid) or {}).get("conclusions") or [""])[0]
    if v1 and add:
        from evaluation.experiment_b_topk_eval import token_overlap

        ov = token_overlap(add, v1).get("correctness", 0.0)
        if ov > V1_DUP_MAX_OVERLAP:
            reasons.append(f"add duplicates the v1 reference (overlap {ov:.2f}) — nothing widened")
    d2text = ((ctx["worksheet_arms"] or {}).get(qid) or {}).get("D2") or ""
    if d2text and add:
        from evaluation.experiment_b_topk_eval import token_overlap

        ov = token_overlap(add, d2text).get("correctness", 0.0)
        if ov > INDEPENDENCE_MAX_OVERLAP:
            reasons.append(
                f"add paraphrases the stored D2 answer (overlap {ov:.2f} > {INDEPENDENCE_MAX_OVERLAP}) — widening must come from the statute"
            )
    if reasons:
        return {"qid": qid, "status": "invalid", "reasons": reasons, "entry": entry}
    return {"qid": qid, "status": "valid", "reasons": [], "entry": entry}


def merge_from_packets(state_path: Path | None = None) -> dict[str, Any]:
    from evaluation.build_references_v4 import canonical_bytes, content_sha256, verify_bundle

    state_path = Path(state_path or STATE_PATH)
    ctx = load_context()
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    results, merged, blocked = [], [], []
    for qid, rec in sorted((state.get("packets") or {}).items()):
        text = Path(rec["packet"]).read_text(encoding="utf-8")
        entry = parse_entry(text)
        if not entry or is_template(entry):
            results.append({"qid": qid, "status": "not_filled"})
            continue
        verdict = validate_entry(qid, entry, ctx)
        results.append(verdict)
        if verdict["status"] != "valid":
            blocked.append(qid)
            continue
        add = entry["add"]
        existing = bundle["questions"][qid]
        if any(" ".join(add.lower().split()) == " ".join(c.lower().split()) for c in existing["conclusions"]):
            results.append({"qid": qid, "status": "already_merged"})
            merged.append(qid)
            continue
        existing["conclusions"].append(add)
        existing.setdefault("provenance", {})["wf6_human_widen"] = True
        existing.setdefault("add_notes", {})[add[:48] + "…"] = entry.get("note") or ""
        bundle.setdefault("meta", {}).setdefault("wf6_widenings", {})[qid] = {
            "note": entry.get("note") or "",
            "anchor_quote": entry.get("anchor_quote") or "",
            "source": "WF-6 human legal review packet (validated: add length, anchor verbatim, v1 dedupe, D2 independence)",
        }
        merged.append(qid)

    if merged:
        major, minor, patch = (int(p) for p in str(bundle["meta"]["bundle_version"]).split("."))
        bundle["meta"]["bundle_version"] = f"{major}.{minor + 1}.{patch}"
        bundle["checksum_sha256"] = content_sha256(bundle)
        BUNDLE_PATH.write_bytes(canonical_bytes(bundle) + b"\n")
        for qid in merged:
            state["packets"][qid]["status"] = "merged"
            state["packets"][qid]["validated"] = "valid"
        state["stage"] = "merged"
        Path(state_path).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "version": "wf6-merge-report-1",
        "bundle_verdict": verify_bundle(),
        "n_merged": len(merged),
        "merged_qids": merged,
        "blocked": blocked,
        "n_blocked": len(blocked),
        "results": results,
    }
    MERGE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    MERGE_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-packets", action="store_true")
    parser.add_argument("--validate-packets", action="store_true")
    parser.add_argument("--merge-from-packets", action="store_true")
    parser.add_argument(
        "--qids",
        default="",
        help="Comma-separated qids to build packets for (default: WIDEN_QIDS).",
    )
    parser.add_argument(
        "--state",
        default="",
        help="Packet-lifecycle state file (default: drafts_state.json). Use a "
        "separate file to build a cohort without clobbering the default run.",
    )
    args = parser.parse_args()

    state_path = Path(args.state) if args.state else STATE_PATH

    if args.validate_packets:
        ctx = load_context()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        counts = {"valid": 0, "invalid": 0, "not_filled": 0}
        for qid, rec in sorted((state.get("packets") or {}).items()):
            entry = parse_entry(Path(rec["packet"]).read_text(encoding="utf-8"))
            verdict = (
                {"status": "not_filled", "reasons": []}
                if (not entry or is_template(entry))
                else validate_entry(qid, entry, ctx)
            )
            counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
            status = verdict.get("status")
            marker = "OK " if status == "valid" else ("--" if status == "not_filled" else "BAD")
            print(
                f"[{marker}] {qid}: {status}" + ("" if status == "valid" else " — " + "; ".join(verdict.get("reasons")))
            )
        print(f"\nvalidation summary: {counts}")
        return 0

    if args.merge_from_packets:
        report = merge_from_packets(state_path=state_path)
        from evaluation.ruler import format_publication_header, publication_header

        print(format_publication_header(publication_header()))
        print(f"\nmerged: {report['n_merged']} {report['merged_qids']}")
        print(f"blocked: {report['n_blocked']} -> {report['blocked']}")
        bv = report.get("bundle_verdict") or {}
        print(
            f"bundle: v{bv.get('bundle_version')} {bv.get('status')} content-sha {str(bv.get('recorded_content_sha256'))[:12]}"
        )
        print(f"report: {MERGE_REPORT}")
        return 0

    build_packets(
        qids=tuple(q.strip() for q in args.qids.split(",") if q.strip()) or None,
        state_path=state_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
