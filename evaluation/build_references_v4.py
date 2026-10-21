"""WF-4 — versioned multi-reference bundle for evaluator v4 (0 LLM calls).

One frozen, checksummed reference bundle per benchmark question — the single
source the v4 components consume for C1/C2/C3 (design review F7; workflow
WF-4). Replaces three disconnected reference shapes (v1 single
``acceptable_conclusion``, v2 overlay one-``add`` widening, separate
``gold_concepts``) with one versioned artifact:

    {qid: {
        "conclusions":  [<v1 conclusion>?, <overlay adds>?...],   # ordered, deduped
        "concepts":     [gold_concepts...],
        "expected_abstain": <insufficient_evidence>,
        "provenance":   {"v1_conclusion": bool, "v2_overlay": bool, "add_notes": {...}},
    }}

plus the carried-over ``banned_abstain_markers`` section (provenance: v2
overlay) — C3's lexicon refinement (WF-5) edits no other place.

Discipline
----------
- **Additive merge**: v2 ``widened_conclusions`` entries are migrated as-is;
  existing entries are never narrowed or dropped (``step0_dual_score_widen.merge_overlay``
  rule). A mechanically-violating entry is reported with reasons, never
  silently removed.
- **Deterministic build**: sorted keys, sorted lists, canonical JSON, no
  timestamps — byte-identical across rebuilds; the only change to the bundle
  content ever comes from an authored widening (WF-6) or an explicit bundle
  version bump (``evaluation/ruler`` R2 boot rule).
- **Two-level checksum**: ``meta.checksum_sha256`` hashes the canonical body
  (checksum field excluded) — verified on read; the file-level SHA-256 is
  computed by ``evaluation.ruler`` and printed in every publication header.

Usage:
    python -m evaluation.build_references_v4          # build + verify + report
    python -m evaluation.build_references_v4 --verify # verify only, no write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BUNDLE_PATH = PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json"
OVERLAY_PATH = PROJECT_ROOT / "evaluation" / "evaluator_v2_overlay.json"

BUNDLE_NAME = "evaluator_v4_references"
BUNDLE_VERSION = "1.0.1"

#: migration gates mirrored from step0_dual_score_widen (line 101-104) so the
#: bundle rejects mechanically-invalid widening with the same thresholds.
MIN_ADD_CHARS = 40
MAX_ADD_CHARS = 2000
V1_DUP_MAX_OVERLAP = 0.95

CHECKSUM_KEY = "checksum_sha256"


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def canonical_bytes(body: dict[str, Any]) -> bytes:
    """The deterministic serialization the content checksum is taken over."""
    return json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")


def content_sha256(bundle: dict[str, Any]) -> str:
    """Hash the bundle body with the self-referential checksum field excluded."""
    body = {k: v for k, v in bundle.items() if k != CHECKSUM_KEY}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def verify_bundle(path: Path = BUNDLE_PATH, *args: Any, **kwargs: dict[str, Any]) -> dict[str, Any]:
    """Re-verify the recorded content checksum on read-back (tamper detector)."""
    if args or not (set(kwargs) <= {"bundle"}):
        # signature kept strict for future extensions; fail loudly otherwise
        raise TypeError(f"verify_bundle takes only path/bundle, got {sorted(kwargs)}")
    if not path.exists():
        return {"ok": False, "status": "absent", "path": str(path)}
    bundle = json.loads(path.read_text(encoding="utf-8"))
    recorded = bundle.get(CHECKSUM_KEY) or ""
    computed = content_sha256(bundle)
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "ok": bool(recorded) and recorded == computed,
        "status": "frozen" if recorded == computed else "CORRUPT",
        "recorded_content_sha256": recorded,
        "computed_content_sha256": computed,
        "file_sha256": file_sha,
        "bundle_version": bundle.get("meta", {}).get("bundle_version"),
        "n_qids": len(bundle.get("questions", {})),
        "path": str(path),
    }


def build_bundle(out_path: Path = BUNDLE_PATH) -> dict[str, Any]:
    """Deterministically assemble the bundle from benchmark + v2 overlay."""
    from evaluation.benchmark import load_questions

    questions = {q.raw["question_id"]: q for q in load_questions()}
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    widened = overlay.get("widened_conclusions") or {}
    banned = overlay.get("banned_abstain_markers") or {}

    per_q: dict[str, dict[str, Any]] = {}
    migration = {"n_overlay_entries": len(widened), "migrated": 0, "reported": []}
    for qid in sorted(questions):
        q = questions[qid]
        conclusions: list[str] = []
        provenance = {"v1_conclusion": bool(_clean(q.acceptable_conclusion)), "v2_overlay": False}
        add_notes: dict[str, str] = {}
        if provenance["v1_conclusion"]:
            conclusions.append(_clean(q.acceptable_conclusion))
        entry = widened.get(qid)
        if entry:
            add = _clean(entry.get("add"))
            reasons: list[str] = []
            if len(add) < MIN_ADD_CHARS:
                reasons.append(f"add too short (<{MIN_ADD_CHARS} chars) to state a statutory reading")
            if len(add) > MAX_ADD_CHARS:
                reasons.append(f"add too long (>{MAX_ADD_CHARS} chars)")
            if add and conclusions:
                from evaluation.experiment_b_topk_eval import token_overlap

                ov = token_overlap(add, conclusions[0]).get("correctness", 0.0)
                if ov > V1_DUP_MAX_OVERLAP:
                    reasons.append(f"add duplicates the v1 reference (overlap {ov:.2f}) — nothing widened")
            if reasons:
                migration["reported"].append({"qid": qid, "reasons": reasons})
            if add and not reasons:
                if all(_normalized(add) != _normalized(c) for c in conclusions):
                    conclusions.append(add)
                    provenance["v2_overlay"] = True
                    if entry.get("note"):
                        add_notes[add[:48] + "…"] = entry["note"]
                migration["migrated"] += 1
        per_q[qid] = {
            "conclusions": conclusions,
            "concepts": [_clean(c) for c in (q.gold_concepts or [])],
            "expected_abstain": bool(q.insufficient_evidence),
            "provenance": provenance,
            "add_notes": add_notes,
        }

    body: dict[str, Any] = {
        "bundle": BUNDLE_NAME,
        "meta": {
            "bundle_version": BUNDLE_VERSION,
            "sources": {
                "benchmark_conclusions": "evaluation.benchmark.load_questions (v1 freeze)",
                "widened_overlay": str(OVERLAY_PATH.relative_to(PROJECT_ROOT)),
                "rule": "additive merge; entries never narrowed or dropped (step0_dual_score_widen discipline)",
            },
            "n_qids": len(per_q),
            "n_widened_migrated": migration["migrated"],
            "n_ie_questions": sum(1 for r in per_q.values() if r["expected_abstain"]),
            # audit record: mechanically-violating entries are kept unstamped on
            # the overlay side, never dropped — the bundle records the rejection.
            "migration_reported": migration["reported"],
        },
        "questions": per_q,
    }
    if banned:
        body["banned_abstain_markers"] = banned
    body[CHECKSUM_KEY] = content_sha256(body)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(body)
    out_path.write_bytes(payload + b"\n")
    return {**verify_bundle(out_path), "migration": migration, "written_bytes": len(payload) + 1}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="verify the existing bundle, do not write")
    args = parser.parse_args()

    if args.verify:
        result = verify_bundle()
    else:
        result = build_bundle()

    from evaluation.ruler import format_publication_header, publication_header, reference_bundle_checksum

    print(format_publication_header(publication_header()))
    print()
    for key, value in result.items():
        if key == "migration" and isinstance(value, dict):
            print(f"migration: {json.dumps({k: v for k, v in value.items() if k != 'reported'}, indent=None)}")
            for rep in value.get("reported") or []:
                print(f"  REPORTED (kept unstamped, not dropped): {rep['qid']} -> {rep['reasons']}")
        else:
            print(f"{key}: {value}")
    print()

    if not result.get("ok"):
        print(f"VERIFY FAILED ({result.get('status')})", file=sys.stderr)
        return 1  # never silently publish a corrupt bundle
    print(f"bundle verified: {result['n_qids']} qids, content sha256 {result['recorded_content_sha256'][:16]}…")

    # report the ruler header's file-level view of the live bundle
    live = reference_bundle_checksum()
    print(f"ruler sees the bundle as: {live['status']} (file sha256 {str(live['sha256'])[:16]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
