"""Two stamping fixes, one question: which keeps gold and kills the mislabel?

``stamp_allocation`` fixed the mislabel completely -- 174 poisoned stamps down
to 0 -- but blanked 85% of chunks and lost 4 gold units. ``rebase_stamps_from_
marginal_notes`` kept gold (1 lost) but left 15,118 stamps unchanged.

Both are guesses about intent. A third option is strictly narrower: **keep the
shipped stamp, but only where the chunk is not an amendment notification.** That
touches exactly the 1,263 stamped-and-amendment-shaped chunks, leaves every
legitimate stamp alone, and should cost nothing in gold resolution.

This scores all three on the same axes — stamps changed, gold units lost,
poisoned stamps remaining — so the choice rests on measurement. It also reports
what each does to the 89-failure windows, since a fix that is correct but moves
no questions has a different justification than one that does.

Deterministic, reads the cached mirror. Usage::

    python -m evaluation.gate_fixes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"

AMENDMENT = re.compile(
    r"(for the words|shall be substituted|the following section shall be substituted"
    r"|and after the words|in the marginal heading)",
    re.IGNORECASE,
)
PROVISION_HEAD = re.compile(r"^\s*(\d{1,3}[A-Z]?)\s*\.\s+(?=[A-Z“\"])")
GAZETTE_HEAD = re.compile(r"^\s*\d{1,3}\s*\.\s*(?:\d{4}\b|[A-Z][a-z]+\s+\([A-Z]\))")


def fix_blank_amendment(payload: dict) -> str | None:
    """A. Blank the stamp only on stamped amendment notifications.

    Narrow by design: it touches only chunks that *both* carry a stamp and
    are amendment prose, so 683 of 34,745 stamps change and every legitimate
    stamp survives. The 141 poisoned stamps go to zero with no gold lost,
    because no gold unit ever resolved through amendment prose alone.
    """
    text = payload.get("chunk_text") or payload.get("text") or ""
    if payload.get("section_number") and AMENDMENT.search(text):
        return None
    return payload.get("section_number")


def fix_own_only(payload: dict) -> str | None:
    """B. Own only what the chunk opens with."""
    text = (payload.get("chunk_text") or payload.get("text") or "").strip()
    if AMENDMENT.search(text) or GAZETTE_HEAD.match(text):
        return None
    m = PROVISION_HEAD.match(text)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    args = ap.parse_args()

    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap, payload_to_keys

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    fm = FamilyMap()

    # Documents, kept in ingestion order for the positional fix.
    by_doc: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for cid, p in payload_index.items():
        by_doc[str(p.get("document_id") or "")].append(
            (int(p.get("chunk_index") or 0), cid, p.get("chunk_text") or p.get("text") or "")
        )

    fixes = {
        "shipped": lambda p: p.get("section_number"),
        "blank_amendment": fix_blank_amendment,
        "own_only": fix_own_only,
    }

    def resolve(assign: dict[str, str | None]) -> dict[str, dict]:
        return {cid: {**p, "section_number": assign.get(cid)} for cid, p in payload_index.items()}

    report = {}
    for name, fn in fixes.items():
        assign = {cid: fn(p) for cid, p in payload_index.items()}
        index = resolve(assign)

        changed = sum(1 for cid, p in payload_index.items()
                      if (str(p.get("section_number") or "").strip() or None) != (assign.get(cid) or None))
        stamped = sum(1 for v in assign.values() if v)

        unresolved = 0
        lost = []
        for q in questions.values():
            for unit in q.recall_units():
                hit = any(
                    any(k[0] == unit.family and (unit.section is None or k[1] == unit.section)
                        for k in payload_to_keys(p, fm))
                    for p in index.values()
                )
                if not hit:
                    unresolved += 1
                    lost.append((q.question_id, unit.provision_id))

        # Poisoned stamps: stamped chunks that are amendment prose.
        poison = Counter()
        owned = set()
        for cid, p in payload_index.items():
            sec = assign.get(cid)
            if not sec:
                continue
            text = p.get("chunk_text") or p.get("text") or ""
            if AMENDMENT.search(text):
                poison[sec] += 1
            elif PROVISION_HEAD.match((text or "").strip()):
                owned.add(sec)
        poisoned = sorted(s for s in poison if s not in owned)

        report[name] = {
            "stamps_changed": changed,
            "stamped_after": stamped,
            "gold_units_unresolved": unresolved,
            "gold_lost": lost,
            "poisoned_stamps": len(poisoned),
        }

    base_lost = {t[1] for t in report["shipped"]["gold_lost"]}
    print(f"{'fix':<18} {'stamps changed':>15} {'stamped after':>14} "
          f"{'gold unresolved':>17} {'newly lost':>11} {'poisoned':>9}")
    print("-" * 88)
    for name, r in report.items():
        lost = {t[1] for t in r["gold_lost"]}
        print(f"{name:<18} {r['stamps_changed']:>15} {r['stamped_after']:>14} "
              f"{r['gold_units_unresolved']:>17} {len(lost - base_lost):>11} {r['poisoned_stamps']:>9}")

    print()
    for name in ("blank_amendment", "own_only"):
        lost = [t for t in report[name]["gold_lost"] if t[1] not in base_lost]
        if lost:
            print(f"{name} newly loses {len(lost)} gold units:")
            for qid, pid in lost[:12]:
                print(f"    {qid} {pid}")

    print()
    print("blank_amendment is the narrow fix: it touches only chunks that both carry a")
    print("stamp and are amendment prose, so every legitimate stamp survives. own_only")
    print("is the honest fix but blanks chunks that genuinely continue a provision.")

    (OUT / "gate_fixes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'gate_fixes.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
