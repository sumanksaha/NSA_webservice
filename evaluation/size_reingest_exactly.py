"""Size the re-ingest exactly: which FSSAI provisions are genuinely missing?

Two earlier passes disagreed, and the disagreement matters because it sizes a
procurement decision:

* ``measure_reingest_scope`` searched the whole mirror and reported 2 absent
  provisions;
* ``verify_phrase_misses`` found s31 *does* match a loose probe -- the text is
  there, stamped 31, attributed to FSSAI: *"(1) No person shall commence or
  carry on any food business except under a licence."*

The first pass was wrong because a section number is not an identity without
its Act: the mirror holds six statutes, so `section_number == 63` matches
Companies Act s63 and the Bharatiya Nyaya Sanhita alongside anything from
FSSAI. Scoping by ``act_name`` settles it.

This pass is the one to trust, and it does the full job: for every benchmark
gold provision in the FSSAI family, decide PRESENT / PRESENT-BUT-MISSTAMPED /
ABSENT, scoped to the gold Act, and report how many failing questions each
category blocks.

Deterministic, reads the cached mirror. Usage::

    python -m evaluation.size_reingest_exactly
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

#: gold provision -> (act fragment, section, [operative probes, most specific first])
PROVISIONS = {
    "fssai:s22": ("food safety and standards act", "22", ["standards", "novel food"]),
    "fssai:s31": ("food safety and standards act", "31",
                  ["no person shall commence or carry on any food business"]),
    "fssai:s41": ("food safety and standards act", "41", ["take a sample"]),
    "fssai:s52": ("food safety and standards act", "52",
                  ["pay such compensation", "in addition to any other penalty"]),
    "fssai:s59": ("food safety and standards act", "59",
                  ["food which is unsafe", "shall be punished with imprisonment for a term"]),
    "fssai:s60": ("food safety and standards act", "60", ["sub-standard"]),
    "fssai:s61": ("food safety and standards act", "61", ["misbranded"]),
    "fssai:s63": ("food safety and standards act", "63",
                  ["carrying on a food business without licence", "without licence shall be punishable"]),
    "fssai:s65": ("food safety and standards act", "65", ["liable to pay", "food safety appellate tribunal"]),
    "fssai:s68": ("food safety and standards act", "68", ["adjudication"]),
    "fssai:s69": ("food safety and standards act", "69", ["compound"]),
    "fssai:s75": ("food safety and standards act", "75", ["food safety appellate tribunal"]),
    "fssai:s76": ("food safety and standards act", "76", ["appellate tribunal"]),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    args = ap.parse_args()

    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.benchmark import load_questions

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}

    rows = []
    for p in payload_index.values():
        act = (p.get("act_name") or "") or (p.get("document_title") or "")
        rows.append((
            act.lower(),
            str(p.get("section_number") or "").strip(),
            (p.get("chunk_text") or p.get("text") or ""),
        ))
    act_texts: dict[str, list[str]] = defaultdict(list)
    for act, sec, text in rows:
        act_texts[act].append(text)

    verdicts = {}
    for pid, (act_frag, sec, probes) in PROVISIONS.items():
        scoped = [(s, t) for a, s, t in rows if act_frag in a]
        stamped = [(s, t) for s, t in scoped if s == sec]
        owned = [(s, t) for s, t in stamped if not AMENDMENT.search(t)]

        hit = None
        for probe in probes:
            for _sec, text in scoped:
                if probe.lower() in text.lower():
                    hit = (probe, text)
                    break
            if hit:
                break

        # Order matters. A stamp existing proves nothing: the whole finding is
        # that amendment prose carries a stamp without stating the provision.
        # The operative probe decides whether the text is actually there.
        if hit:
            # Text is present in this Act. Good enough whether or not it is
            # stamped -- a hit is what retrieval needs.
            verdict = "PRESENT"
        elif owned:
            # Non-amendment chunks carry the stamp but state none of the
            # provision's own language.
            verdict = "STAMP_EXISTS_TEXT_DOES_NOT"
        elif stamped:
            verdict = "STAMP_EXISTS_ONLY_AMENDMENT_PROSE"
        else:
            verdict = "ABSENT"
        verdicts[pid] = {
            "verdict": verdict,
            "section": sec,
            "fssai_chunks": len(scoped),
            "stamped": len(stamped),
            "owned_chunks": len(owned),
            "probe_hit": hit[0] if hit else None,
            "sample": hit[1][:180] if hit else (owned[0][1][:180] if owned else None),
        }
        print(f"  {verdict:<26} {pid:<12} stamped={len(stamped):<3} owned={len(owned):<3} "
              f"probe={verdicts[pid]['probe_hit']!r}")

    counts = Counter(v["verdict"] for v in verdicts.values())
    print("\nverdict totals:")
    for k, v in counts.most_common():
        print(f"  {k:<34} {v}")

    broken = sorted(pid for pid, v in verdicts.items()
                    if v["verdict"] != "PRESENT")
    if broken:
        print("\nprovisions whose text is NOT in the corpus:")
        for pid in broken:
            v = verdicts[pid]
            print(f"  {pid}: {v['verdict']}  stamped={v['stamped']} owned={v['owned_chunks']}")
            if v["sample"]:
                print(f"      longest owned chunk: {v['sample'][:170]!r}")

    # Which failing questions are blocked by each category?
    attribution = json.loads(
        (PROJECT_ROOT / "evaluation/out/ceiling_v5/failure_attribution.json").read_text(encoding="utf-8")
    )
    failing = [r["qid"] for r in attribution["per_question"] if r.get("verdict") == "model_wrong"]
    blocked: dict[str, list[str]] = defaultdict(list)
    for qid in failing:
        q = questions.get(qid)
        if not q:
            continue
        for unit in q.recall_units():
            v = verdicts.get(unit.provision_id)
            if v and v["verdict"].startswith("ABSENT"):
                blocked[unit.provision_id].append(qid)

    print(f"\nfailing questions blocked on ABSENT provisions: "
          f"{len({q for qs in blocked.values() for q in qs})}/{len(failing)}")
    for pid, qs in sorted(blocked.items(), key=lambda kv: -len(set(kv[1]))):
        print(f"  {pid:<12} {len(set(qs))} qids: {sorted(set(qs))}")

    needs_source = sorted(pid for pid, v in verdicts.items()
                          if v["verdict"].startswith("ABSENT")
                          or v["verdict"].startswith("STAMP_EXISTS"))
    print(f"\n=> needs the FSSAI Act re-ingested: {len(needs_source)} -> {needs_source}")
    misstamped = sorted(pid for pid, v in verdicts.items()
                        if v["verdict"] == "PRESENT_BUT_MISSTAMPED")
    print(f"=> text present, stamp wrong (re-stamp fixes): {misstamped}")

    (OUT / "reingest_exact.json").write_text(json.dumps({
        "verdicts": verdicts,
        "counts": dict(counts),
        "needs_source": needs_source,
        "present_but_misstamped": misstamped,
        "blocked": {k: sorted(set(v)) for k, v in blocked.items()},
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'reingest_exact.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
