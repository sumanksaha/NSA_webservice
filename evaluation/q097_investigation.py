"""Q097 deep-dive: why 27 gold chunks were invisible to stamp-only matching.

Q097's single gold unit ``srf:specific_performance`` (family=srf, section=10,
title="Specific performance") resolves to 27 chunks via text fallback but 0
via stamp-based matching.  This script examines every retrieved chunk for Q097
and categorises the *reason* stamp matching failed for the 27 text-fallback
hits:

  - wrong_section  : section_number stamp present but mismatched
  - missing_section: section_number is null / absent
  - wrong_family   : act_name / document_title does not map to the srf family
  - sections_covered: chunk carries ``sections_covered`` but not section 10
  - amendment      : document_title mentions an Amendment that overrides
                     the parent Act's section numbering

Output: evaluation/out/ceiling_v5/q097_investigation.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions, load_gold_registry
from evaluation.bench_p0_1_packing import _load_payload_index, _load_jsonl
from evaluation.resolution import FamilyMap, matches_gold, payload_to_keys
from evaluation.gold_text_match import resolve_by_text, _payload_families

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
RAW_DIR = OUT_DIR / "raw"
OUT_FILE = OUT_DIR / "q097_investigation.json"
ARM = "C_hybrid"


def _section_number(payload: dict) -> str | None:
    sn = payload.get("section_number")
    if sn is not None:
        return str(sn)
    return None


def _sections_covered(payload: dict) -> list[str]:
    raw = payload.get("sections_covered") or []
    return [str(s) for s in raw if s is not None]


def _diagnose_rejection(payload: dict, unit, family_map: FamilyMap) -> dict:
    """Return the specific reason(s) stamp matching failed for a text-fallback hit."""
    sections_covered = _sections_covered(payload)
    section_number = _section_number(payload)
    act_name = payload.get("act_name") or ""
    doc_title = payload.get("document_title") or ""
    families = _payload_families(payload, family_map)
    text_title_match = unit.title.lower() in (payload.get("chunk_text", "") or "").lower()

    has_act_name = bool(act_name)
    has_doc_title = bool(doc_title)
    stamps = payload_to_keys(payload, family_map)
    family_in_stamps = any(f == unit.family for f, _ in stamps)

    covered_contains_10 = any(
        s.split("(")[0].strip() == "10" for s in sections_covered
    ) if sections_covered else False

    reasons = []

    if not family_in_stamps:
        if unit.family not in families:
            reasons.append("family_not_resolved")
        else:
            reasons.append("family_resolved_but_no_stamp")

    if section_number and section_number != str(unit.section):
        reasons.append("wrong_section_number")
    elif not section_number:
        reasons.append("missing_section_number")

    if sections_covered and not covered_contains_10:
        reasons.append("sections_covered_missing_10")
    elif sections_covered and covered_contains_10:
        reasons.append("sections_covered_has_10_but_stamp_section_missing")

    if "amendment" in act_name.lower() or "amendment" in doc_title.lower():
        reasons.append("amendment_document")

    if "specific relief" in act_name.lower() or "specific relief" in doc_title.lower():
        reasons.append("srf_parent_act")

    if has_doc_title and not has_act_name:
        reasons.append("act_name_empty_doc_title_only")

    return {
        "section_number": section_number,
        "sections_covered": sections_covered,
        "act_name": act_name,
        "document_title": doc_title,
        "payload_families": sorted(families),
        "stamp_keys": stamps,
        "family_in_stamp_keys": family_in_stamps,
        "text_contains_title_phrase": text_title_match,
        "rejected_reasons": reasons,
    }


def main() -> int:
    payload_index = _load_payload_index()
    family_map = FamilyMap()
    registry = load_gold_registry()
    questions = {q.question_id: q for q in load_questions()}

    qid = "Q097"
    arm_ids: list[str] = []
    fpath = RAW_DIR / f"{ARM}.jsonl"
    if fpath.exists():
        with open(fpath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("question_id") == qid:
                    arm_ids = [str(c) for c in rec.get("chunk_ids", [])]
                    break

    q = questions.get(qid)
    if not q:
        print(f"Q097 not found in questions")
        return 1

    unit = q.recall_units()[0]

    print(f"Q097 gold unit: {unit.provision_id}")
    print(f"  family={unit.family}, section={unit.section}, title='{unit.title}'")
    print(f"  Retrieved chunks: {len(arm_ids)}")

    stamp_hits: list[str] = []
    text_only_hits: list[str] = []
    diagnoses: list[dict] = []

    for cid in arm_ids:
        payload = payload_index.get(cid)
        if payload is None:
            continue
        stamp_match = matches_gold(payload, unit, family_map, text_fallback=False)
        text_match = resolve_by_text(payload, unit, family_map)

        if stamp_match:
            stamp_hits.append(cid)
        elif text_match:
            text_only_hits.append(cid)
            diag = _diagnose_rejection(payload, unit, family_map)
            diag["chunk_id"] = cid
            diagnoses.append(diag)

    reason_counter: Counter = Counter()
    for d in diagnoses:
        for r in d["rejected_reasons"]:
            reason_counter[r] += 1

    section_values: Counter = Counter()
    act_names: Counter = Counter()
    doc_titles: Counter = Counter()
    for d in diagnoses:
        section_values[d["section_number"]] += 1
        act_names[d["act_name"]] += 1
        doc_titles[d["document_title"]] += 1

    result = {
        "qid": qid,
        "gold_unit": {
            "provision_id": unit.provision_id,
            "family": unit.family,
            "section": unit.section,
            "title": unit.title,
            "registry_entry": registry.get(unit.provision_id),
        },
        "retrieved_chunk_count": len(arm_ids),
        "stamp_based_matches": len(stamp_hits),
        "text_fallback_only_matches": len(text_only_hits),
        "reason_distribution": dict(sorted(reason_counter.items(), key=lambda x: -x[1])),
        "section_number_values_in_text_only_chunks": {
            str(k): v for k, v in sorted(section_values.items(), key=lambda x: -x[1])
        },
        "act_name_values": {k: v for k, v in sorted(act_names.items(), key=lambda x: -x[1]) if v > 0},
        "document_title_values": {k: v for k, v in sorted(doc_titles.items(), key=lambda x: -x[1]) if v > 0},
        "full_diagnoses": diagnoses,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUT_FILE}")
    print(f"  Stamp-based matches: {len(stamp_hits)}")
    print(f"  Text-fallback-only matches: {len(text_only_hits)}")
    print(f"  Reason distribution:")
    for reason, count in sorted(reason_counter.items(), key=lambda x: -x[1]):
        print(f"    {reason}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
