"""Phase 2: gold-labelling audit (offline, 0 LLM calls).

Two live rounds (KG, retry) plus two prompt variants all convert to ~zero, and
every round points the same way: references draw on more than the labelled
gold provisions (gold covers 0.369 of the reference's distinctive mass vs the
answer's 0.442). Before any more LLM spend, establish per question WHERE the
reference's content actually lives in the corpus:

* For each incorrect failure QID, rank every corpus provision by distinctive
  coverage of the reference. If the top-covering provisions are not the
  labelled gold ones, that is a BENCHMARK labelling gap (fix the labels, not
  the pipeline). If nothing covers well, that is a CORPUS gap (procurement,
  in the s52/s63 mould).
* For each incorrect failure QID, group its top-20 retrieved chunks by
  stamped provision and score each group's distinctive coverage of the
  reference. Verdicts:
  - GOLD_OK: the gold provisions' union covers >=0.5 — the labels suffice.
  - SYNTHESIS: gold does not, but some other in-window provision covers
    >=0.5. The content is IN THE WINDOW under a different provision's name:
    this is exactly what a multi-provision-synthesis prompt can use, and
    these QIDs are its validation set.
  - RETRIEVAL: nothing in-window covers >=0.5 but the whole-corpus union
    does — a ranking problem (TargetPlan territory, already measured).
  - CORPUS_GAP: even the whole corpus covers <0.5 — procurement/label
    decision only, no prompt variant can work.

Why window-constrained, not whole-corpus: ranking all 2362 provisions by
recall crowns mega-provisions by mass (fssai:498 owns 762 chunks, comp:1
owns 1822 — they top nearly every reference). Mass is not topicality, and a
"synthesize s498" instruction would be nonsense. Constraining to the
retrieved window asks the only Phase-1-actionable question: is the
reference's content already in front of the model under another name?

Efficiency: an inverted index (distinctive token -> provisions) so each
question scores only provisions sharing a distinctive token with its
reference, not all 2362. IDF weights and the boilerplate set are imported
from decide_gate_or_answers (single source, no drift).

Usage: python -m evaluation.gold_labelling_audit [--limit N]
Writes out/.../reeval_89/gold_labelling_audit.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from math import log
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from evaluation.answer_scoring import _tokens
    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.benchmark import load_questions
    from evaluation.decide_gate_or_answers import BOILERPLATE
    from evaluation.resolution import FamilyMap, payload_to_keys

    mirror = _load_payload_index()
    qs = {q.question_id: q for q in load_questions()}
    retr = {json.loads(l)["qid"]: json.loads(l)
            for l in (OUT / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()}
    gen = {json.loads(l)["qid"]: json.loads(l)
           for l in (OUT / "gen_C_hybrid.jsonl").read_text(encoding="utf-8").splitlines()
           if l.strip()}

    # --- corpus IDF + provision token sets + inverted index ----------------
    print("indexing corpus ...")
    df: dict[str, int] = {}
    n_docs = 0
    prov_toks: dict[tuple[str, str | None], set[str]] = {}
    fm = FamilyMap()
    for p in mirror.values():
        text = p.get("chunk_text") or p.get("text") or ""
        if not text:
            continue
        n_docs += 1
        toks = _tokens(text)
        for t in toks:
            df[t] = df.get(t, 0) + 1
        try:
            keys = payload_to_keys(p, fm)
        except Exception:
            continue
        for k in keys:
            prov_toks.setdefault(k, set()).update(toks)
    idf = {t: log(n_docs / c) for t, c in df.items()}
    inv: dict[str, set] = defaultdict(set)
    for k, toks in prov_toks.items():
        for t in toks:
            inv[t].add(k)
    print(f"  {n_docs} docs, {len(prov_toks)} provisions indexed")

    incorrect = [q for q, r in gen.items()
                 if (retr.get(q) or {}).get("cohort") == "failure" and not r.get("binary_correct")]
    incorrect.sort()
    if args.limit:
        incorrect = incorrect[: args.limit]
    print(f"auditing {len(incorrect)} incorrect failures\n")

    rows = []
    for qi, qid in enumerate(incorrect, 1):
        q = qs[qid]
        ref = str(getattr(q, "acceptable_conclusion", "") or "")
        rt = _tokens(ref)
        weighted = {t: idf.get(t, log(n_docs)) for t in rt}
        w_total = sum(v for t, v in weighted.items() if t not in BOILERPLATE) or 1.0

        def cov(text_toks: set[str]) -> float:
            hit = sum(weighted[t] for t in (text_toks & rt) if t not in BOILERPLATE)
            return hit / w_total

        # In-window provisions: group the top-20 retrieved chunks by stamp.
        win_ids = list(((retr.get(qid) or {}).get("C_hybrid") or {}).get("chunk_ids") or [])[:20]
        win_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
        for cid in win_ids:
            p = mirror.get(str(cid)) or {}
            text = p.get("chunk_text") or p.get("text") or ""
            if not text:
                continue
            win_groups[(str(p.get("act_name") or ""),
                        str(p.get("section_number") or ""))].update(_tokens(text))
        win_scored = sorted(((cov(t), k) for k, t in win_groups.items()),
                            key=lambda pair: -pair[0])

        # Gold union coverage (whole corpus — gold text wherever it lives).
        gold_keys = {(u.family, u.section) for u in q.recall_units()}
        gold_toks: set[str] = set()
        for k in gold_keys:
            gold_toks.update(prov_toks.get(k, set()))
        gold_cov = cov(gold_toks)

        # Whole-corpus union of top-3 (mass-biased, reported only as ceiling).
        cands: set = set()
        for t in rt:
            if t not in BOILERPLATE:
                cands.update(inv.get(t, set()))
        scored = []
        for k in cands:
            if k[1] is None:
                continue
            hit = sum(weighted[t] for t in (prov_toks[k] & rt) if t not in BOILERPLATE)
            if hit > 0:
                scored.append((hit / w_total, k))
        scored.sort(key=lambda pair: -pair[0])
        union_toks: set[str] = set()
        for _, k in scored[:3]:
            union_toks.update(prov_toks[k] & rt)
        union_cov = (sum(weighted[t] for t in union_toks if t not in BOILERPLATE) / w_total)

        best_non_gold = [(s, k) for s, k in win_scored
                         if k not in gold_keys and s >= 0.5]
        # Gold keys use (family, section-id) while window groups use
        # (act_name, section stamp): compare on normalised section equality.
        def _seck(key) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(key[1] or "").lower())
        gold_secs = {_seck((f, s)) for f, s in gold_keys}
        win_gold_cov = max([s for s, k in win_scored if _seck(k) in gold_secs] or [0.0])

        if gold_cov >= 0.5 or win_gold_cov >= 0.5:
            verdict = "GOLD_OK"
        elif best_non_gold:
            verdict = "SYNTHESIS"
        elif union_cov >= 0.5:
            verdict = "RETRIEVAL"
        else:
            verdict = "CORPUS_GAP"
        rows.append({
            "qid": qid,
            "gold_union_cov": round(gold_cov, 3),
            "gold_in_window_cov": round(win_gold_cov, 3),
            "best_window": ([{"key": list(k), "cov": round(s, 3)}
                             for s, k in win_scored[:3]]),
            "synthesis_candidates": [{"key": list(k), "cov": round(s, 3)}
                                     for s, k in best_non_gold[:3]],
            "corpus_union_top3": round(union_cov, 3),
            "verdict": verdict,
        })
        if qi % 25 == 0:
            print(f"  {qi}/{len(incorrect)}")

    from collections import Counter
    verdicts = Counter(r["verdict"] for r in rows)
    print("\n=== AUDIT VERDICT (window-constrained) ===")
    for v, n in verdicts.most_common():
        print(f"  {v:<14} {n:>3}")
        if v in ("SYNTHESIS", "CORPUS_GAP", "RETRIEVAL"):
            print(f"    {[r['qid'] for r in rows if r['verdict'] == v]}")

    syn = [r for r in rows if r["verdict"] == "SYNTHESIS"]
    print(f"\n  SYNTHESIS QIDs ({len(syn)}): reference covered >=0.5 by non-gold "
          f"in-window provisions — the Phase-1 modification set.")
    print(f"  GOLD_OK: labels suffice; RETRIEVAL: ranking problem; CORPUS_GAP: "
          f"procurement/label decision, no prompt variant can work.")

    (OUT / "gold_labelling_audit.json").write_text(
        json.dumps({"n": len(rows), "verdicts": dict(verdicts), "rows": rows}, indent=2),
        encoding="utf-8")
    print(f"\nwrote {OUT / 'gold_labelling_audit.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
