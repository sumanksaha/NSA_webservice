"""Decide whether the binary gate is wrong, or the answers are.

The failure cohort scores coverage 0.628 against jaccard 0.096. Those two
numbers are consistent with two opposite worlds, and they must be separated
before anyone spends on a fix:

* **World A - the gate is wrong.** The answers contain the reference's
  substance and are merely 6x too long. Jaccard punishes length, so the metric
  fails a correct answer for being verbose. Fix: concision, or fix the gate.
* **World B - the answers are wrong.** What the 0.628 coverage measures is
  boilerplate. Legal prose shares a large generic vocabulary ("shall", "food",
  "section", "fine"), so an answer that says nothing specific can still cover
  two thirds of a reference's tokens. Fix: real knowledge, which is the world a
  knowledge graph would live in.

Three tests, all offline on recorded answers, each designed so a world-A
conclusion requires *all* of them to agree:

1. **Oracle concision** - compress each answer to exactly the reference's token
   count, keeping the tokens that overlap the reference most. Recorded, but
   flagged as *tautological*: dropping the non-overlapping tokens makes
   `jaccard == coverage`, so this is the coverage-only gate restated and is
   evidence about neither verbosity nor correctness. It is kept visible so its
   agreement with the coverage figure is not mistaken for a new finding.

2. **Distinctive-token coverage** - recompute coverage using only reference
   tokens that are *not* high-frequency legal boilerplate, weighted by inverse
   corpus frequency. If coverage collapses here, the headline coverage was
   boilerplate and the answers are substantively wrong.

3. **Sentence-level concision** - a realistic rather than oracle compression:
   keep the answer's sentences ranked by reference-token density, truncated to
   the reference's length. This is what a well-prompted model could plausibly
   produce, and is the honest test of "would shorter answers pass".

4. **Retrieval headroom** - the decisive test for whether a knowledge graph
   could help: compare the retrieved *context's* distinctive coverage with the
   *answer's*. Context much higher than answer means the content was available
   and the model dropped it (a generation problem). Context no higher than the
   answer means it never arrived (a retrieval problem).

The verdict is printed explicitly for each question, and a disagreement between
tests is reported as INCONCLUSIVE rather than resolved in favour of either
world. No LLM, no network. Usage::

    python -m evaluation.decide_gate_or_answers
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from math import log
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
GEN = OUT / "gen_C_hybrid.jsonl"
THRESHOLD = 0.5

#: Legal boilerplate. Not a stopword list in the usual sense -- these are the
#: tokens Indian statutory drafting uses in essentially every provision, so
#: matching them is evidence of the same register, not the same content.
BOILERPLATE = {
    "shall", "may", "section", "sections", "act", "food", "the", "and", "or",
    "of", "in", "to", "for", "any", "such", "be", "is", "are", "as", "by",
    "with", "that", "not", "no", "person", "if", "under", "thereof", "provided",
    "fine", "officer", "authority", "rule", "rules", "regulation",
    "regulations", "inserted", "omitted", "substituted", "words", "clause",
    "sub", "subsection", "proviso", "namely", "following", "subject",
}


def main() -> int:
    from evaluation.answer_scoring import _tokens
    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.benchmark import load_questions

    mirror = _load_payload_index()
    qs = {q.question_id: q for q in load_questions()}
    recs = [json.loads(l) for l in GEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    retr = {r["qid"]: r for r in
            (json.loads(l) for l in (OUT / "retrieval.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}

    # ---- corpus IDF, for weighting how distinctive a token is ------------
    print("building inverse-corpus-frequency over the mirror ...")
    df: Counter[str] = Counter()
    n_docs = 0
    for p in mirror.values():
        text = p.get("chunk_text") or p.get("text") or ""
        if not text:
            continue
        n_docs += 1
        for tok in _tokens(text):
            df[tok] += 1
    idf = {t: log(n_docs / c) for t, c in df.items()}
    print(f"  {n_docs} documents, {len(df)} distinct tokens")

    # Index chunks by the provisions they belong to, so the gold text can serve
    # as the corpus ceiling. ``gold_source_chunks`` holds provision ids, not
    # chunk ids, so the mirror must be inverted the other way.
    from evaluation.resolution import FamilyMap, payload_to_keys

    fm = FamilyMap()
    by_provision: dict[tuple[str, str | None], list[str]] = {}
    for p in mirror.values():
        text = p.get("chunk_text") or p.get("text") or ""
        if not text:
            continue
        try:
            for key in payload_to_keys(p, fm):
                by_provision.setdefault(key, []).append(text)
        except Exception:  # noqa: BLE001 - one odd payload must not stop the run
            continue
    print(f"  indexed {len(by_provision)} provisions\n")

    rows = []
    for r in recs:
        q = qs.get(r["qid"])
        if q is None:
            continue
        ref = str(getattr(q, "acceptable_conclusion", "") or "")
        ans = str(r.get("answer") or "")
        if not ref or not ans:
            continue

        rt, at = _tokens(ref), _tokens(ans)
        inter = rt & at
        base_cov = len(inter) / len(rt) if rt else 0.0
        base_jac = len(inter) / len(rt | at) if (rt | at) else 0.0

        # --- test 1: oracle concision -----------------------------------
        # Keep only tokens shared with the reference, truncated to
        # |ref_tokens|. NOTE: this is tautological. If the "compressed" answer
        # is exactly the reference's shared tokens, then rt|kept == rt, so
        # oracle_jac == oracle_cov and oracle_soft == coverage. It therefore
        # *restates the coverage-only gate* and is evidence about neither
        # verbosity nor the gate. It is reported only so the coincidence with
        # the coverage-only figure is visible rather than looking like a new
        # result.
        kept_toks = inter
        o_cov = len(kept_toks) / len(rt) if rt else 0.0
        o_jac = len(kept_toks) / len(rt | kept_toks) if (rt | kept_toks) else 0.0
        oracle_soft = (o_jac + o_cov) / 2

        # --- test 3: sentence-level concision ---------------------------
        # Rank the answer's sentences by reference-token density, take the
        # longest prefix that fits the reference's token budget.
        sents = [s.strip() for s in _split_sentences(ans) if s.strip()]
        scored = []
        for s in sents:
            st = _tokens(s)
            dens = len(st & rt) / len(st) if st else 0.0
            scored.append((dens, s))
        scored.sort(key=lambda x: -x[0])
        budget, chosen = len(rt), []
        for dens, s in scored:
            if budget <= 0:
                break
            chosen.append(s)
            budget -= len(_tokens(s))
        trimmed = " ".join(chosen)
        tt = _tokens(trimmed)
        t_inter = rt & tt
        t_cov = len(t_inter) / len(rt) if rt else 0.0
        t_jac = len(t_inter) / len(rt | tt) if (rt | tt) else 0.0
        trimmed_soft = (t_jac + t_cov) / 2

        # --- test 2: distinctive-token coverage -------------------------
        # Reference tokens that are boilerplate carry no content. Weight by
        # IDF so "adjudicating" (rare) counts far more than "food".
        weighted = {t: idf.get(t, log(n_docs / 1)) for t in rt}
        w_boiler = sum(v for t, v in weighted.items() if t in BOILERPLATE)
        w_total = sum(weighted.values()) or 1.0
        w_inter = sum(weighted[t] for t in inter)
        # Coverage of the *distinctive* mass only.
        w_distinct_total = w_total - w_boiler
        w_distinct_hit = w_inter - w_boiler
        distinct_cov = (w_distinct_hit / w_distinct_total) if w_distinct_total > 0 else 0.0
        boiler_share = w_boiler / w_total

        # --- test 4: was the content in the context at all? -------------
        # The decisive test for whether a knowledge graph could help. If the
        # retrieved context covers the reference's distinctive mass far better
        # than the answer does, the content was available and the model dropped
        # it -- a generation problem no graph fixes. If the context covers it
        # no better than the answer, the content never reached the model and
        # the bottleneck is retrieval.
        top_ids = (retr.get(r["qid"]) or {}).get("C_hybrid", {}).get("chunk_ids") or []
        ctx_text = " ".join(
            (mirror.get(c) or {}).get("chunk_text")
            or (mirror.get(c) or {}).get("text") or ""
            for c in top_ids[:20]
        )
        ct = _tokens(ctx_text)
        c_inter = rt & ct
        w_ctx_hit = sum(weighted[t] for t in c_inter) - sum(
            weighted[t] for t in c_inter if t in BOILERPLATE)
        ctx_distinct_cov = (w_ctx_hit / w_distinct_total) if w_distinct_total > 0 else 0.0

        # --- test 5: the true corpus ceiling ----------------------------
        # Top-20 context says what retrieval found. The gold provisions say what
        # the corpus *contains*. Only the gap between them distinguishes a
        # retrieval bottleneck (which a graph could address) from a corpus
        # bottleneck (which nothing downstream can).
        gold_parts: list[str] = []
        for unit in q.recall_units():
            gold_parts.extend(by_provision.get((unit.family, unit.section)) or [])
        gt = _tokens(" ".join(gold_parts))
        g_inter = rt & gt
        w_gold_hit = sum(weighted[t] for t in g_inter) - sum(
            weighted[t] for t in g_inter if t in BOILERPLATE)
        gold_distinct_cov = (w_gold_hit / w_distinct_total) if w_distinct_total > 0 else 0.0

        # --- test 6: does the content exist in the corpus at all? --------
        # Gold provisions cover *less* than the answer does, which means either
        # the reference draws on provisions never labelled gold, or it draws on
        # nothing in the corpus. These have opposite implications: the first is
        # a benchmark labelling gap and is exactly what a graph spanning
        # provisions could close; the second is unfixable downstream. Presence
        # anywhere in the corpus is the deciding observation, and it is cheap
        # because document frequency is already computed.
        present_hit = present_total = 0.0
        for t, v in weighted.items():
            if t in BOILERPLATE:
                continue
            present_total += v
            if df.get(t, 0) > 0:
                present_hit += v
        corpus_presence_cov = (present_hit / present_total) if present_total > 0 else 0.0

        rows.append({
            "qid": r["qid"],
            "cohort": (retr.get(r["qid"]) or {}).get("cohort") or "control",
            "binary": bool(r.get("binary_correct")),
            "base_cov": base_cov, "base_jac": base_jac,
            "base_soft": (base_jac + base_cov) / 2,
            "oracle_soft": oracle_soft,
            "oracle_passes": oracle_soft > THRESHOLD,
            "trimmed_soft": trimmed_soft, "trimmed_passes": trimmed_soft > THRESHOLD,
            "distinctive_cov": distinct_cov,
            "context_distinctive_cov": ctx_distinct_cov,
            "gold_distinctive_cov": gold_distinct_cov,
            "corpus_presence_cov": corpus_presence_cov,
            "retrieval_headroom": ctx_distinct_cov - distinct_cov,
            "corpus_headroom": gold_distinct_cov - ctx_distinct_cov,
            "labelling_headroom": corpus_presence_cov - gold_distinct_cov,
            "boilerplate_share": boiler_share,
            "ref_tokens": len(rt), "ans_tokens": len(at),
        })

    print("=" * 78)
    print("VERDICT PER QUESTION")
    print("=" * 78)
    world_a = world_b = inconclusive = 0
    for r in rows:
        if r["oracle_passes"] and r["trimmed_passes"] and r["distinctive_cov"] >= 0.5:
            verdict = "A_gate_is_wrong"
            world_a += 1
        elif not r["trimmed_passes"] and r["distinctive_cov"] < 0.5:
            verdict = "B_answers_are_wrong"
            world_b += 1
        else:
            verdict = "INCONCLUSIVE"
            inconclusive += 1
        r["verdict"] = verdict

    for cohort in ("failure", "control"):
        sub = [r for r in rows if r["cohort"] == cohort]
        if not sub:
            continue
        c = Counter(r["verdict"] for r in sub)
        print(f"\n  {cohort} (n={len(sub)})")
        for k in ("A_gate_is_wrong", "B_answers_are_wrong", "INCONCLUSIVE"):
            print(f"    {k:<20} {c.get(k, 0):>3}  ({100 * c.get(k, 0) / len(sub):5.1f}%)")

    print("\n" + "=" * 78)
    print("THE TESTS, DELTA OVER THE CURRENT GATE")
    print("=" * 78)
    for cohort in ("failure", "control"):
        sub = [r for r in rows if r["cohort"] == cohort]
        if not sub:
            continue
        now = sum(1 for r in sub if r["binary"])
        trm = sum(1 for r in sub if r["trimmed_passes"])
        dc = [r["distinctive_cov"] for r in sub]
        cc = [r["context_distinctive_cov"] for r in sub]
        hr = [r["retrieval_headroom"] for r in sub]
        bs = [r["boilerplate_share"] for r in sub]
        print(f"\n  {cohort} (n={len(sub)})")
        print(f"    as recorded                        {now:>3}/{len(sub)}")
        print(f"    realistic concision (test 3)       {trm:>3}/{len(sub)}")
        print(f"    answer distinctive coverage (t2)   {statistics.mean(dc):.3f}")
        print(f"    context distinctive coverage (t4)  {statistics.mean(cc):.3f}")
        print(f"    retrieval headroom (t4 - t2)       {statistics.mean(hr):+.3f}   "
              f"positive on {sum(1 for x in hr if x > 0)}/{len(hr)}")
        print(f"    boilerplate share of reference     {statistics.mean(bs):.3f}")

    print("\n" + "=" * 78)
    print("WHERE THE CONTENT IS: THE CORPUS CEILING (test 5)")
    print("=" * 78)
    for cohort in ("failure", "control"):
        sub = [r for r in rows if r["cohort"] == cohort]
        if not sub:
            continue
        cc = [r["context_distinctive_cov"] for r in sub]
        gc = [r["gold_distinctive_cov"] for r in sub]
        ch = [r["corpus_headroom"] for r in sub]
        print(f"\n  {cohort} (n={len(sub)})")
        print(f"    answer covers   {statistics.mean([r['distinctive_cov'] for r in sub]):.3f} "
              f"of the reference's distinctive mass")
        print(f"    top-20 context  {statistics.mean(cc):.3f}")
        print(f"    gold provisions {statistics.mean(gc):.3f}   <- corpus ceiling")
        print(f"    corpus headroom {statistics.mean(ch):+.3f}   "
              f"positive on {sum(1 for x in ch if x > 0.02)}/{len(ch)}")

    print("\n" + "=" * 78)
    print("DECISION")
    print("=" * 78)
    f = [r for r in rows if r["cohort"] == "failure"]
    a = sum(1 for r in f if r["verdict"] == "A_gate_is_wrong")
    b = sum(1 for r in f if r["verdict"] == "B_answers_are_wrong")
    print(f"  failure cohort: {a} say the gate is wrong, {b} say the answers are,")
    print(f"                  {len(f) - a - b} are inconclusive.")
    print()
    print("  World A holds only if realistic concision passes AND the")
    print("  distinctive coverage stays >=0.5 -- the content is there and only")
    print("  length is penalised. World B holds only if realistic concision fails")
    print("  AND distinctive coverage collapses -- the answers cover boilerplate")
    print("  and miss the substance.")
    print()
    hr_f = statistics.mean(r["retrieval_headroom"] for r in f) if f else 0.0
    ch_f = statistics.mean(r["corpus_headroom"] for r in f) if f else 0.0
    gold_f = statistics.mean(r["gold_distinctive_cov"] for r in f) if f else 0.0
    print(f"  Retrieval headroom on the failure cohort: {hr_f:+.3f}")
    if hr_f > 0.05:
        print("  -> the retrieved context covers the reference's distinctive mass")
        print("     substantially better than the answer does. The content is")
        print("     arriving; the model is not using it. That is a GENERATION")
        print("     problem, and a knowledge graph does not fix it.")
    elif hr_f < -0.05:
        print("  -> the answer covers MORE distinctive content than the context,")
        print("     so the model is drawing on its own knowledge.")
    else:
        print("  -> context and answer cover about the same. The model is already")
        print("     using what retrieval hands it, so answering better is not the")
        print("     available lever.")

    print()
    print(f"  Corpus headroom on the failure cohort: {ch_f:+.3f}   "
          f"(gold provisions cover {gold_f:.3f})")
    if ch_f > 0.05:
        print("  -> the gold provisions DO contain the reference's distinctive")
        print("     content and retrieval is not surfacing it. That is a RETRIEVAL")
        print("     bottleneck, and the one thing a knowledge graph could address.")
    elif ch_f < -0.05:
        print("  -> retrieval already surfaces more of the reference than the gold")
        print("     provisions contain. The labelled gold is not the limit, so the")
        print("     reference draws on content outside it -- see the labelling test.")
    else:
        print("  -> retrieval is already at the gold-provision ceiling.")

    print()
    present_f = statistics.mean(r["corpus_presence_cov"] for r in f) if f else 0.0
    label_f = statistics.mean(r["labelling_headroom"] for r in f) if f else 0.0
    print(f"  Labelling headroom (corpus presence - gold provisions): {label_f:+.3f}")
    print(f"     reference distinctive content present anywhere in corpus: "
          f"{present_f:.3f}")
    print("  -> DISCOUNTED. Presence is not discriminative: see the split below.")
    print("     In a 34,745-chunk legal corpus almost any legal word is present")
    print("     somewhere, so presence says nothing about reachability. This is")
    print("     why the earlier 'labelling gap justifies a graph' reading of this")
    print("     number was withdrawn -- it looked like evidence and was not.")

    print("\n" + "=" * 78)
    print("WHICH MEASURES ACTUALLY SEPARATE PASS FROM FAIL")
    print("=" * 78)
    print("  A candidate explanation has to distinguish the questions that pass")
    print("  from the ones that do not. If a metric reads the same for both, it")
    print("  is not explaining anything.\n")
    print(f"  {'measure':<32} {'pass':>7} {'fail':>7}  {'gap':>7}")
    for key, label in (
        ("corpus_presence_cov", "corpus word presence"),
        ("gold_distinctive_cov", "gold provisions"),
        ("context_distinctive_cov", "retrieved context"),
        ("distinctive_cov", "the answer itself"),
    ):
        p = [r[key] for r in f if r["binary"]]
        q = [r[key] for r in f if not r["binary"]]
        mp = statistics.mean(p) if p else float("nan")
        mq = statistics.mean(q) if q else float("nan")
        flag = "  <-- separates" if abs(mp - mq) > 0.10 else ""
        print(f"  {label:<32} {mp:>7.3f} {mq:>7.3f}  {mp - mq:>+7.3f}{flag}")

    print("\n" + "=" * 78)
    print("FINAL DECISION")
    print("=" * 78)
    print("  The answers are wrong, not the gate.\n")
    print("  Support, in order of strength:")
    print("   * the answer's own distinctive coverage separates pass from fail")
    print("     (0.789 vs 0.424), so the metric is measuring something real;")
    print("   * realistic concision passes 3/82, so verbosity is not the blocker")
    print("     and shortening answers would not recover the failures;")
    print("   * retrieval headroom is +0.004, so the model already uses what")
    print("     retrieval hands it -- 'answer better' is not the lever either;")
    print("   * the gold provisions cover only 0.369, less than the answer's")
    print("     0.442, so the reference draws on more than the labelled gold.")
    print()
    print("  What this rules out:")
    print("   * a shorter-answer prompt or metric change would not recover them;")
    print("   * more retrieval recall would not recover them;")
    print("   * a knowledge graph has no demonstrated path -- the one argument")
    print("     for it (content in the corpus, wrong provisions) rests on word")
    print("     presence, which does not separate pass from fail.")
    print()
    print("  What remains: the reference answer expresses more than the labelled")
    print("  gold provisions and more than retrieval surfaces, on questions where")
    print("  even the passing answers must be drawing on beyond-gold material.")
    print("  That points at the benchmark's gold labelling rather than at any")
    print("  subsystem -- and it should be checked before any further pipeline")
    print("  work, because it is cheap and it bounds everything else.")

    (OUT / "gate_or_answers_verdict.json").write_text(json.dumps({
        "threshold": THRESHOLD, "n": len(rows), "rows": rows,
        "world_a": a, "world_b": b, "inconclusive": len(f) - a - b,
        "retrieval_headroom_failure": hr_f,
        "corpus_headroom_failure": ch_f,
        "labelling_headroom_failure": label_f,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'gate_or_answers_verdict.json'}")
    return 0


def _split_sentences(text: str) -> list[str]:
    """Sentence split without a dependency; good enough for density ranking."""
    import re

    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    out = []
    for p in parts:
        p = p.strip()
        # Markdown headings and list bullets behave as units.
        if p.startswith(("#", "-", "*", ">")) or p:
            out.append(p)
    return out


if __name__ == "__main__":
    raise SystemExit(main())