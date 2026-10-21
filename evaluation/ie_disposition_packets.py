"""IE disposition packets — Lane A of the missed-refusal queue (0 LLM).

The 11 IE qids (`Q042 Q060 Q090 Q098 Q119 Q128 Q132 Q133 Q134 Q136 Q137`)
carry `expected_abstain=True` in the frozen bundle — the ruler side is
already correct. What remains open is the GENERATOR side (it answered where
the human audit says it should have refused), and the machine cannot author
that decision. This module prepares the per-qid evidence and records the
human's disposition, mirroring the WF-6 widening architecture:

**Stage 1 - build packets** (``--build-packets``)
    Per qid, one human-editable markdown packet carrying the recorded facts:
    the audit expectation, the audited-arm behaviour (answered? refusal
    phrasing present?), the K-curve checkpoint behaviour, and the WF-5
    verdict note. The machine proposes nothing — it reports.

**Stage 2 - validate packets** (``--validate-packets``)
    The `IED-DISPOSITION` block must name a legal disposition (A/B/C) and a
    human-written rationale; template packets are reported ``not_filled``.

**Stage 3 - merge dispositions** (``--merge-from-packets``)
    Additive-only: stamps ``questions[qid]["ie_disposition"]`` (a
    non-scoring field — no rule reads it), bumps the bundle version
    (1.2.1 → 1.3.0-style minor bump) and recomputes/verifies the content
    checksum. Idempotent; conflicting re-decisions are refused (no silent
    overwrite); the human tabulation is never touched.

Usage:
    python -m evaluation.ie_disposition_packets --build-packets
    python -m evaluation.ie_disposition_packets --validate-packets
    python -m evaluation.ie_disposition_packets --merge-from-packets
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
IE_DIR = OUT / "ie_disposition"
PACKET_DIR = IE_DIR / "packets"
STATE_PATH = IE_DIR / "ie_state.json"
MERGE_REPORT = IE_DIR / "ie_merge_report.json"

IE_QIDS = ("Q042", "Q060", "Q090", "Q098", "Q119", "Q128", "Q132", "Q133", "Q134", "Q136", "Q137")
AUDIT_ARMS = ("C-O3", "D2", "D3", "E1")  # same audited-arm names as WF-5's measurement

CHECKPOINT = OUT / "experiment_B_llm_checkpoint.jsonl"
WF5_NOTES = OUT / "abstention_calibration_v2.json"

DISPOSITIONS = {
    "A": "generator_gating_defect",
    "B": "question_or_corpus_insufficiency",
    "C": "closed_accepted_limitation",
}

EXCERPT = 120


class _Ctx(dict):
    pass


def load_context() -> _Ctx:
    """Bundle, WF-5 notes, worksheet answer texts, checkpoint behaviour."""
    import evaluation.v3_component_rescore as v3
    import evaluation.widen_references_v4 as w4
    from evaluation.build_references_v4 import verify_bundle
    from evaluation.calibrate_abstention import load_lexicon, matched_phrases

    verdict = verify_bundle()
    if not verdict.get("ok"):
        raise SystemExit(f"bundle checksum FAIL — refusing to build on a tampered bundle: {verdict}")
    bundle = json.loads((PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json").read_text(encoding="utf-8"))
    qinfo = bundle.get("questions") or {}
    lexicon = load_lexicon()
    bans_by_qid = {
        q: set(rule.get("banned_phrases") or [])
        for q, rule in ((bundle.get("banned_abstain_markers") or {}).get("rules_by_qid") or {}).items()
    }

    notes: dict[str, str] = {}
    if WF5_NOTES.exists():
        wf5 = json.loads(WF5_NOTES.read_text(encoding="utf-8"))
        for rec in (wf5.get("missed_abstention_notes") or {}).get("additions_proposed") or []:
            if isinstance(rec, dict) and rec.get("qid"):
                notes[str(rec["qid"])] = str(rec.get("proposal") or "")

    audited: dict[str, dict[str, Any]] = {}
    try:
        worksheet_arms = v3.parse_worksheet(w4.WORKSHEET)
    except Exception as exc:  # worksheet optional at build time — degrade, don't die
        print(f"worksheet unavailable ({exc!r}) — audited-arm facts omitted")
        worksheet_arms = {}
    for qid in IE_QIDS:
        per_arm = {}
        for arm, text in sorted((worksheet_arms.get(qid) or {}).items()):
            if arm not in AUDIT_ARMS or not (text or "").strip():
                continue
            hits = matched_phrases(text, lexicon, bans_by_qid.get(qid, set()))
            per_arm[arm] = {
                "answered": True,
                "chars": len(text),
                "refusal_phrasing": bool(hits),
                "excerpt": " ".join(text.split())[:EXCERPT],
            }
        audited[qid] = per_arm

    curve: dict[str, dict[str, Any]] = {qid: {"answered": 0, "total": 0} for qid in IE_QIDS}
    k10: dict[str, dict[str, Any]] = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            qid = str(row.get("question_id"))
            if qid not in curve:
                continue
            st = curve[qid]
            st["total"] += 1
            if (row.get("answer") or "").strip() and not row.get("error"):
                st["answered"] += 1
            if str(row.get("k")) == "10":
                k10[qid] = {"gold_present": row.get("gold_present"), "ce_gold_rank": row.get("ce_gold_rank")}

    return _Ctx(
        bundle=bundle,
        qinfo=qinfo,
        notes=notes,
        audited=audited,
        curve=curve,
        k10=k10,
        bundle_verdict=verdict,
    )


# ------------------------------------------------------------------ packets --


def build_packets(out_dir: Path = PACKET_DIR, state_path: Path | None = None) -> dict[str, Any]:
    ctx = load_context()
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {"stage": "built", "lane": "IE-disposition-1", "packets": {}}

    readme = [
        "# IE disposition wave — reviewer guide",
        "",
        "The machine prepared one evidence packet per qid; the decision is yours.",
        "Edit ONLY the `### IED-DISPOSITION` block at the end of each packet:",
        "",
        "    disposition: A | B | C",
        "    rationale:   1-3 sentences in your own words",
        "",
        "  A = generator_gating_defect (the answer pipeline should have refused;",
        "      becomes a service-side target, tested later as a live arm)",
        "  B = question_or_corpus_insufficiency (retrievable material is legally",
        "      insufficient despite gold; documented, question escalated)",
        "  C = closed_accepted_limitation (accept and disclose; no further action)",
        "",
        "Then: `--validate-packets` (mechanical gates), then `--merge-from-packets`",
        "(additive-only; the bundle version bumps and the checksum re-verifies).",
        "The human tabulation is ground truth and is never modified by this lane.",
    ]
    (out_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    for qid in IE_QIDS:
        info = ctx["qinfo"].get(qid) or {}
        aud = ctx["audited"].get(qid) or {}
        cur = ctx["curve"].get(qid) or {}
        k10 = ctx["k10"].get(qid) or {}
        line: list[str] = []
        line.append(f"# IE disposition packet - {qid}")
        line.append("")
        line.append("Machine-prepared facts only - the disposition is a human decision.")
        line.append("")
        line.append("## Recorded facts")
        line.append(f"- bundle v{ctx['bundle'].get('meta', {}).get('bundle_version')}:")
        line.append(
            f"  expected_abstain={bool(info.get('expected_abstain'))} (human audit ground truth - refusal expected)"
        )
        line.append(
            f"  references frozen: conclusions={len(info.get('conclusions') or [])}, "
            f"concepts={len(info.get('concepts') or [])}"
        )
        aud_parts = []
        for arm, v in sorted(aud.items()):
            if v["refusal_phrasing"]:
                aud_parts.append(f"{arm}: REFUSAL-phrased")
            else:
                aud_parts.append(f"{arm}: ANSWERED no-refusal ({v['chars']} chars)")
        aud_desc = ", ".join(aud_parts) or "no audited arm text available"
        line.append(f"- audited arms: {aud_desc}")
        n_ans, n_tot = cur.get("answered", 0), cur.get("total", 0)
        line.append(
            f"- K-curve checkpoint: answered {n_ans}/{n_tot} arms"
            + (f"; K=10 gold_present={k10.get('gold_present')}, ce_gold_rank={k10.get('ce_gold_rank')}" if k10 else "")
        )
        if ctx["notes"].get(qid):
            line.append(f"- WF-5 verdict note: {ctx['notes'][qid]}")
        line.append("")
        line.append("## Disposition options")
        for key, label in DISPOSITIONS.items():
            line.append(f"  {key} = {label}")
        line.append("")
        line.append("## Your decision (edit only this block)")
        line.append("### IED-DISPOSITION")
        line.append("disposition: <A|B|C>")
        line.append("rationale: <human-written, 1-3 sentences, >=20 chars>")

        packet = out_dir / f"{qid}.md"
        packet.write_text("\n".join(line) + "\n", encoding="utf-8")
        state["packets"][qid] = {
            "packet": str(packet),
            "status": "pending_human",
            "class": "A1_answered" if n_ans else "A2_no_trace",
            "audited_arms": sorted(aud),
        }

    target_state = Path(state_path) if state_path is not None else STATE_PATH
    target_state.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nbuilt {len(state['packets'])} disposition packets under {out_dir}")
    print("NEXT: human legal review edits the `### IED-DISPOSITION` blocks; then --validate-packets.")
    return state


def parse_disposition(md_text: str) -> dict[str, str]:
    """Extract the single IED-DISPOSITION block from a packet."""
    fields: dict[str, str] = {}
    in_block = False
    for raw in md_text.splitlines():
        line = raw.strip()
        if line == "### IED-DISPOSITION":
            in_block = True
            continue
        if in_block and line.startswith("### "):
            break
        if not in_block or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    return {k: v for k, v in fields.items() if k in ("disposition", "rationale")}


def is_template(entry: dict[str, str]) -> bool:
    """Untouched packets carry the instruction placeholders - not a decision."""
    return entry.get("disposition", "").startswith("<") or entry.get("rationale", "").startswith("<")


def validate_disposition(qid: str, entry: dict[str, str]) -> dict[str, Any]:
    if entry.get("disposition") not in DISPOSITIONS:
        return {
            "qid": qid,
            "status": "invalid",
            "reason": f"disposition must be one of A/B/C, got: {entry.get('disposition')!r}",
        }
    rationale = entry.get("rationale") or ""
    if len(rationale.strip()) < 20:
        return {
            "qid": qid,
            "status": "invalid",
            "reason": f"rationale must be human-written (>=20 chars), got {len(rationale.strip())}",
        }
    return {"qid": qid, "status": "valid", "disposition": DISPOSITIONS[entry["disposition"]]}


# -------------------------------------------------------------------- merge --


def merge_from_packets(state_path: Path | None = None) -> dict[str, Any]:
    from evaluation.build_references_v4 import BUNDLE_PATH, canonical_bytes, content_sha256, verify_bundle

    state_path = Path(state_path or STATE_PATH)
    bundle_path = Path(BUNDLE_PATH)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    results, merged, blocked = [], [], []
    for qid, rec in sorted((state.get("packets") or {}).items()):
        text = Path(rec["packet"]).read_text(encoding="utf-8")
        entry = parse_disposition(text)
        if not entry or is_template(entry):
            results.append({"qid": qid, "status": "not_filled"})
            continue
        verdict = validate_disposition(qid, entry)
        results.append(verdict)
        if verdict["status"] != "valid":
            blocked.append(qid)
            continue
        record = {
            "disposition": verdict["disposition"],
            "rationale": entry.get("rationale") or "",
            "wave": state.get("lane") or "IE-disposition-1",
            "source": "IE human disposition packet (validated: choice membership, human rationale)",
        }
        existing = bundle["questions"].get(qid, {}).get("ie_disposition")
        if existing is not None:
            if (
                existing.get("disposition") == record["disposition"]
                and existing.get("rationale") == record["rationale"]
            ):
                verdict["status"] = "already_merged"
                merged.append(qid)
                continue
            verdict["status"] = "conflict"
            verdict["reason"] = "a different IE disposition is already recorded - no silent overwrite"
            blocked.append(qid)
            continue
        bundle["questions"].setdefault(qid, {})["ie_disposition"] = record
        bundle.setdefault("meta", {}).setdefault("ie_dispositions", {})[qid] = {
            "disposition": record["disposition"],
            "source": record["source"],
        }
        merged.append(qid)

    newly = [r["qid"] for r in results if r["status"] == "valid"]
    if newly:
        major, minor, patch = (int(p) for p in str(bundle["meta"]["bundle_version"]).split("."))
        bundle["meta"]["bundle_version"] = f"{major}.{minor + 1}.{patch}"
        bundle["checksum_sha256"] = content_sha256(bundle)
        bundle_path.write_bytes(canonical_bytes(bundle) + b"\n")

    for qid in merged:
        rec = state["packets"][qid]
        status = next((r["status"] for r in results if r["qid"] == qid), "")
        if status in ("valid", "already_merged"):
            rec["status"] = "merged"
    if merged:
        state["stage"] = "merged"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "version": "ie-disposition-merge-report-1",
        "bundle_verdict": verify_bundle(bundle_path),
        "n_merged": len(merged),
        "merged_qids": merged,
        "n_blocked": len(blocked),
        "blocked": blocked,
        "results": results,
    }
    MERGE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    MERGE_REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-packets", action="store_true")
    parser.add_argument("--validate-packets", action="store_true")
    parser.add_argument("--merge-from-packets", action="store_true")
    args = parser.parse_args()

    if args.validate_packets:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for qid, rec in sorted(state.get("packets").items()):
            entry = parse_disposition(Path(rec["packet"]).read_text(encoding="utf-8"))
            filled = bool(entry) and not is_template(entry)
            verdict = validate_disposition(qid, entry) if filled else {"qid": qid, "status": "not_filled"}
            print(f"{qid}: {verdict.get('status')} {verdict.get('reason', '')}")
        return 0
    if args.merge_from_packets:
        report = merge_from_packets()
        print(f"merged: {report['n_merged']}  blocked: {report['n_blocked']}")
        print(f"bundle: {report['bundle_verdict']}")
        return 0
    build_packets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
