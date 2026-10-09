"""P0-2 scope: which benchmark questions have no in-corpus evidence?

The analysis doc lists missing statutes (Water Act, WB Meat Order, KMC
water rules, PCA schedules) from prose. This measures the gap from the data
side instead: for each of the 150 benchmark questions, do its gold
provisions actually appear in the indexed corpus?

Three outcomes per question:

  ``covered``      gold provision resolves against the payload cache
  ``not_in_corpus`` gold provision resolves nowhere — a corpus gap, and the
                   single most actionable bucket (no model can answer from
                   text that isn't there)
  ``no_gold``      the question declares no gold units (e.g. open-ended or
                   abstention items) — excluded from the gap rate

``evaluation/coverage_audit.py`` answers a different question (does a chunk
carry a retrievable identity), so this is deliberately separate.

Usage:
    python -m evaluation.corpus_gap_scope
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions, load_gold_registry
from evaluation.resolution import FamilyMap, matches_gold

CACHE_DIR = PROJECT_ROOT / "evaluation" / "out" / "cache"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "corpus_gap_scope.json"


def _load_payloads() -> list[dict[str, Any]]:
    path = CACHE_DIR / "payload_index.jsonl"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> int:
    payloads = _load_payloads()
    if not payloads:
        print(f"missing payload cache: {CACHE_DIR / 'payload_index.jsonl'}", file=sys.stderr)
        return 1

    plist = [p["payload"] for p in payloads if "payload" in p]
    questions = load_questions()
    registry = load_gold_registry()
    family_map = FamilyMap()

    per_q: list[dict[str, Any]] = []
    missing_by_act: dict[str, set[str]] = defaultdict(set)
    covered_units = missing_units = 0

    for q in questions:
        units = q.recall_units()
        if not units:
            per_q.append({"qid": q.question_id, "status": "no_gold"})
            continue
        missing_here = []
        for u in units:
            hit = any((lambda pay: _safe_match(pay, u, family_map))(pay) for pay in plist)
            if hit:
                covered_units += 1
            else:
                missing_units += 1
                missing_here.append(u)
                missing_by_act[u.act or "(unknown act)"].add(u.provision_id)
        per_q.append({
            "qid": q.question_id,
            "status": "not_in_corpus" if missing_here else "covered",
            "question": q.question,
            "missing_provisions": [
                {"provision_id": u.provision_id, "act": u.act, "section": u.section} for u in missing_here
            ],
        })

    counts = Counter(r["status"] for r in per_q)
    graded = counts["covered"] + counts["not_in_corpus"]
    gap_rate = counts["not_in_corpus"] / max(graded, 1)

    # Distinguish "the statute was never ingested" from "it was ingested but
    # the chunk lost its section stamp".  The two need opposite remedies
    # (procurement vs. chunker/backfill), so collapsing them into one
    # ``not_in_corpus`` bucket sent the original P0-2 diagnosis wrong.
    untagged_detail = _untagged_evidence(missing_by_act, plist, family_map)
    for q in per_q:
        for prov in q.get("missing_provisions") or []:
            pid = prov.get("provision_id")
            if pid in untagged_detail:
                prov["gap_kind"] = "present_but_untagged"

    out = {
        "version": "corpus-gap-scope-1",
        "date": "2026-10-04",
        "corpus": {"payloads_indexed": len(plist), "gold_registry_provisions": len(registry)},
        "summary": {
            "questions_total": len(questions),
            "covered": counts["covered"],
            "not_in_corpus": counts["not_in_corpus"],
            "no_gold": counts["no_gold"],
            "question_gap_rate": round(gap_rate, 4),
            "gold_units_total": covered_units + missing_units,
            "gold_units_missing": missing_units,
            "unit_gap_rate": round(missing_units / max(covered_units + missing_units, 1), 4),
        },
        "missing_by_act": {act: sorted(ids) for act, ids in sorted(missing_by_act.items())},
        "present_but_untagged": {pid: untagged_detail[pid] for pid in sorted(untagged_detail)},
        "per_question": per_q,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    s = out["summary"]
    print("=" * 74)
    print("P0-2 corpus gap scope")
    print("=" * 74)
    print(f"payloads indexed : {out['corpus']['payloads_indexed']}")
    print(
        f"questions        : {s['questions_total']}  (covered {s['covered']}, "
        f"not_in_corpus {s['not_in_corpus']}, no_gold {s['no_gold']})",
    )
    print(f"question gap rate: {s['question_gap_rate']:.1%}")
    print(f"gold units       : {s['gold_units_missing']}/{s['gold_units_total']} missing ({s['unit_gap_rate']:.1%})")
    print()
    if missing_by_act:
        print("MISSING PROVISIONS BY ACT")
        for act, ids in sorted(missing_by_act.items(), key=lambda kv: -len(kv[1])):
            listed = sorted(ids)
            print(f"  {len(listed):>3}  {act}")
            print(f"       {', '.join(listed[:8])}{' ...' if len(listed) > 8 else ''}")
    else:
        print("No missing provisions detected — every gold unit resolves in-corpus.")

    if untagged_detail:
        # Distinguishes "never ingested" from "ingested but unstamped".  The
        # two need opposite fixes, and conflating them is what made the
        # original P0-2 row prescribe procurement for a chunker bug.
        print()
        print("GAP KIND: PRESENT BUT UNTAGGED (already ingested, no section stamp)")
        for pid, d in sorted(untagged_detail.items()):
            print(
                f"  {pid:<10} section {d['section']:>3}  "
                f"{d['chunks_opening_with_section']} chunk(s) open with the section "
                f"of {d['untagged_chunks_in_act']} unstamped in this act",
            )
        print("  -> remedy is re-OCR + section backfill, NOT procurement.")
        print("  -> the chunk text may still be corrupt OCR; verify before trusting a match.")
    print()
    print(f"written: {OUT_FILE}")
    return 0


def _safe_match(payload: dict[str, Any], unit: Any, family_map: Any) -> bool:
    try:
        return bool(matches_gold(payload, unit, family_map))
    except Exception:
        return False


def _untagged_evidence(
    missing_by_act: dict[str, set[str]],
    plist: list[dict[str, Any]],
    family_map: Any,
) -> dict[str, dict[str, Any]]:
    """Classify unresolved gold units as *present but untagged*.

    A unit fails :func:`matches_gold` when no payload carries its
    ``(family, section)`` key.  That happens for two very different reasons:

    * the instrument was never ingested — remedy is procurement; or
    * the instrument **is** in the index, but the chunk holding this
      provision has ``section_number=None``, so the resolver cannot match
      it — remedy is re-chunking / backfilling the section stamp.

    Reporting only the first sends the fix in the wrong direction, which is
    exactly what happened to the original P0-2 row: the PCRA Rules 2017 were
    already ingested (1,100 chunks) and the three unresolved gold rules were
    present but unstamped.

    For each unresolved unit this returns the untagged chunks belonging to
    its act whose text opens with its section number, i.e. the evidence that
    the text is present.  It deliberately does **not** claim the text is
    usable — the PCRA chunks are corrupt OCR, and a count here means
    "re-OCR before stamping", not "stamp and the gap closes".
    """
    from evaluation.resolution import norm_section

    registry = load_gold_registry()
    out: dict[str, dict[str, Any]] = {}
    for act, ids in missing_by_act.items():
        act_norms = {norm(act)}
        for pid in ids:
            rec = registry.get(pid) or {}
            section = norm_section(str(rec.get("section") or ""))
            if not section:
                continue
            # Only look at this act's chunks, matched loosely: PCRA chunks
            # carry act_name = the parent 1960 Act, not the 2017 Rules.
            cands = [
                p for p in plist if act_norms & {norm(str(p.get(k) or "")) for k in ("act_name", "document_title")}
            ]
            untagged = [
                p for p in cands if not p.get("section_number") and not p.get("sections_covered")
            ]  # Exclude a longer number ("40" for rule 4) and a dotted sub-rule
            # ("4.1").  ``\b`` alone is not enough: it matches between "4" and
            # the "." of "4.1".  A trailing digit after the separator is the
            # discriminator, so a real rule body ("4 The owner ...") still
            # matches via the whitespace branch.
            pattern = rf"\s*{re.escape(section)}(?![0-9])(?:\.(?![0-9])|\s)"
            starts = [p for p in untagged if re.match(pattern, str(p.get("chunk_text") or ""))]
            if starts:
                out[pid] = {
                    "act": act,
                    "section": section,
                    "untagged_chunks_in_act": len(untagged),
                    "chunks_opening_with_section": len(starts),
                    "verdict": "text present but carries no section stamp; re-OCR, then backfill",
                }
    return out


def norm(value: str) -> str:
    """Lowercase, collapse whitespace, drop punctuation used in titles."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


if __name__ == "__main__":
    raise SystemExit(main())
