"""Decide which alternatives belong in the amendment predicate, one at a time.

``probe_amendment_predicate`` found the shipped predicate recognises 683
stamped chunks as amendment prose while a looser union recognises 988 -- a 305
false-negative gap. "CLEAN" was therefore true of the predicate, not of the
corpus, and the remediation is incomplete by that margin.

There is an obvious way to close it: add every alternative that makes the number
look better. That is the same Goodhart that produced this defect in the first
place -- a predicate tuned until it agrees with the desired conclusion rather
than with the language. So each candidate alternative is measured separately
and must earn its place on evidence:

* how many stamped chunks it *newly* catches, i.e. what it adds over the union of
  everything already accepted;
* what those chunks actually say, printed, so false positives are visible rather
  than counted;
* whether any gold unit stops resolving, which is the cost side.

An alternative is recommended only if it is amendment language in the samples
and costs no gold unit. A large count is not by itself an argument.

Offline. Usage::

    python -m evaluation.extend_amendment_predicate
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"

#: What the engine and gate_fixes currently accept.
CURRENT = (
    "for the words",
    "shall be substituted",
    "the following section shall be substituted",
    "and after the words",
    "in the marginal heading",
)

#: Candidates, each with the reason it might belong and the risk it carries.
CANDIDATES = {
    "shall be inserted": (
        "gazette phrasing for inserting a proviso/clause; the provision itself "
        "states the text rather than saying it is being inserted",
        "an operative provision could in principle instruct a future insertion",
    ),
    "shall be omitted": (
        "gazette phrasing for repeal; unambiguously about amending, not being",
        "none apparent",
    ),
    "for the word": (
        "singular of an accepted alternative; the plural form is already in",
        "marginal -- most such chunks already match on 'shall be substituted'",
    ),
    "for the clause": (
        "gazette phrasing for replacing a clause",
        "an operative provision could contain 'for the clause' in different usage",
    ),
    "after the proviso": (
        "anchoring phrase of an insertion instruction",
        "an operative proviso can also contain 'after the proviso'",
    ),
    "in the said section": (
        "cross-reference to another provision",
        "TOO BROAD -- operative text frequently refers to other sections",
    ),
}


def main() -> int:
    import re

    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.gate_fixes import AMENDMENT
    from evaluation.resolution import FamilyMap, payload_to_keys

    mirror = _load_payload_index()
    stamped = {
        cid: p for cid, p in mirror.items() if p.get("section_number")
    }
    print(f"stamped chunks: {len(stamped)}")
    matched = sum(
        1 for p in stamped.values()
        if AMENDMENT.search(p.get("chunk_text") or p.get("text") or "")
    )
    print(f"current predicate matches: {matched}\n")

    from evaluation.benchmark import load_questions

    fm = FamilyMap()

    # Index the corpus ONCE. The first version of this re-walked all 34,745
    # payloads for every gold unit of every candidate -- quadratic, and it hung
    # until it was interrupted. What the gold test actually needs is only: for
    # each (family, section) a gold unit asks for, does any payload still
    # produce it? So build the keys and their contributing ids in one pass;
    # blanking a set then costs a subtraction.
    contributors: dict[tuple[str, str | None], set[str]] = {}
    for cid, p in mirror.items():
        for key in payload_to_keys(p, fm):
            contributors.setdefault(key, set()).add(cid)
    print(f"indexed {len(contributors)} distinct (family, section) keys\n")

    units = [
        (q.question_id, unit)
        for q in load_questions()
        for unit in q.recall_units()
    ]
    print(f"gold units: {len(units)}")

    def unresolved(blanks: set[str]) -> tuple[int, list[str]]:
        """Gold units with no surviving producer once ``blanks`` is blanked."""
        lost: list[str] = []
        for qid, unit in units:
            want = (unit.family, unit.section)
            if not any(key == want and (producers - blanks)
                       for key, producers in contributors.items()):
                lost.append(f"{qid} {unit.provision_id}")
        return len(lost), lost

    base_n, base_lost = unresolved(set())
    print(f"gold unresolved before any change: {base_n}")
    if base_lost:
        print(f"    {base_lost}\n")

    results: dict[str, dict] = {}
    for name, (why, risk) in CANDIDATES.items():
        rx = re.compile(re.escape(name), re.IGNORECASE)
        # Only what this candidate adds over everything already accepted.
        added = [
            (cid, p) for cid, p in stamped.items()
            if not AMENDMENT.search(p.get("chunk_text") or p.get("text") or "")
            and rx.search(p.get("chunk_text") or p.get("text") or "")
        ]
        n_unres, lost = unresolved({cid for cid, _ in added})
        newly_lost = sorted(set(lost) - set(base_lost))
        print(f"--- {name!r}")
        print(f"    rationale : {why}")
        print(f"    risk      : {risk}")
        print(f"    newly catches : {len(added)}")
        print(f"    gold unresolved : {base_n} -> {n_unres}"
              + (f"   LOST {newly_lost}" if newly_lost else "   (no loss)"))
        for cid, p in added[:4]:
            text = (p.get("chunk_text") or p.get("text") or "")[:130]
            print(f"      stamp={p.get('section_number')!r}  {text!r}")
        print()
        results[name] = {
            "rationale": why, "risk": risk,
            "newly_catches": len(added),
            "gold_unresolved_before": base_n,
            "gold_unresolved_after": n_unres,
            "gold_newly_lost": newly_lost,
        }

    recommend = [
        n for n, r in results.items()
        if not r["gold_newly_lost"] and r["newly_catches"] > 0
    ]
    print("=" * 70)
    print(f"recommended additions (no gold cost, non-zero coverage): {recommend}")
    print("NOT recommended:", [n for n in results if n not in recommend])
    print("\nReview the printed samples before accepting any of these. A large")
    print("newly_catches with amendment-looking text is the argument; anything")
    print("else is Goodhart.")

    (OUT / "predicate_extension.json").write_text(json.dumps({
        "current_alternatives": list(CURRENT),
        "gold_unresolved_before": base_n,
        "gold_unresolved_ids": base_lost,
        "candidates": results,
        "recommended": recommend,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'predicate_extension.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())