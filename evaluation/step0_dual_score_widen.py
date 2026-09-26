"""Step 0b — draft widened references from the statute for `reference_narrow`
targets (plan sec 5.2 Step 1). No model calls.

The registered `reference_narrow` gate forbids spending generation calls and
requires dual reporting: "Both the old and the new score are reported until the
references are fixed." This module implements the fix-the-reference procedure:

  1. --worksheet  write a statute worksheet for the dual-score targets that do
                  not yet have a widened conclusion: the v1 reference, the D2
                  candidate answer, and the O3 evidence anchor (verbatim chunk
                  text of the primary gold units, cut from the C-manifest O3
                  payload via the corpus gold index).
  2. --from-csv   read drafts (question_id, add, note, anchor_quote) filled by
                  the human from the statute text in the worksheet.
  3. --validate   mechanical gates per draft (also run inside --merge):
       - anchor_quote must be a verbatim (whitespace-normalized) substring of
         the O3 evidence anchor  -> else the qid is evidence_missing, not
         reference_narrow, and goes back for re-audit;
       - the widening must NOT paraphrase the stored D2 answer (independence,
         token_overlap > 0.85) — widening by copying the candidate is scorer
         gaming and contaminates the 89 non-residual qids' comparability;
       - the widening must not duplicate the v1 reference (>0.95);
       - widenings never narrow: v1 text always stays a valid alternative.
  4. --merge      merge valid drafts into evaluator_v2_overlay.json
                  (widened_conclusions), additively: existing entries are never
                  modified or removed. Bumps a 'revisions' provenance record.
  5. --rescore    rerun evaluation/rescore_evaluator_v2.py (0 LLM calls) so
                  evaluator_v2_per_question.jsonl carries v1 and v2 side by side.
  6. --report     write step0_dual_score_report.{json,md}: per-target v1/v2 soft
                  and binary for every condition + flip counts. Both scores are
                  reported, as the gate requires.

Scorer is frozen: token_overlap / thresholds imported unchanged from
experiment_b_topk_eval; only the reference side of the comparison moves.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import warnings
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

os.environ.setdefault("SKIP_SCHEMA_CHECK", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

# torch / sentence_transformers stubs (same shim as step0_label_residual) so the
# frozen scorer's import chain resolves without altering scoring behavior.
for _mod in ("torch", "sentence_transformers"):
    if _mod not in sys.modules:
        import types

        class _AnyStub:
            def __init__(self, *a, **k):
                pass

            def __call__(self, *a, **k):
                return _AnyStub()

            def __getattr__(self, name):
                return _AnyStub()

            def __iter__(self):
                return iter(())

        _stub = types.ModuleType(_mod)
        _stub.__getattr__ = lambda name: _AnyStub()
        sys.modules[_mod] = _stub

import evaluation.step0_label_residual as s0
from evaluation.experiment_b_topk_eval import token_overlap
from evaluation.step1_preregister_gates import quote_in_evidence

OUT = s0.OUT
C_MANIFEST = s0.C_MANIFEST
OVERLAY_PATH = ROOT / "evaluation" / "evaluator_v2_overlay.json"
DUAL_TARGETS = OUT / "step0_dual_score_targets.json"
LABELS_PUBLISHED = OUT / "step0_residual_labels.json"

WORKSHEET = OUT / "step0_dual_score_worksheet.md"
CSV_TEMPLATE = OUT / "step0_dual_score_draft.csv"
DRAFTS_DEFAULT = OUT / "step0_dual_score_draft_filled.csv"
REPORT_JSON = OUT / "step0_dual_score_report.json"
REPORT_MD = OUT / "step0_dual_score_report.md"
RESCORE_SCRIPT = ROOT / "evaluation" / "rescore_evaluator_v2.py"

# 16k: O3 chunks run 1-6k chars each, so a 6k cap fit ~1.5 chunks and cut the
# operative provision out of the anchor even when it ranked #2 (Q087's
# "single-use plastic" was chunk #2-3). The cap only bounds worksheet size.
ANCHOR_TEXT_CAP = 16000
INDEPENDENCE_MAX_OVERLAP = 0.85  # widening vs stored D2 answer
V1_DUP_MAX_OVERLAP = 0.95  # widening vs v1 reference
MIN_ADD_CHARS = 40
MAX_ADD_CHARS = 2000
CONDITIONS = ("C-O3", "D2", "D3", "E1")

# Payload-wide anchor ranking: the gold-unit resolution can point at the wrong
# section chunk (amendment tables, adjacent sections) while the operative
# provision text sits elsewhere in the same payload. When that happens the
# gold-derived anchor cannot be quoted from, and a widening drafted on it is
# impossible even though the evidence IS present.
ANCHOR_RANKED_TOP_K = 4
ANCHOR_GOLD_MIN_SCORE = 0.12   # gold text must cover at least this of the query
ANCHOR_GOLD_RELATIVE = 1.5     # ranked must beat gold by this factor to take over
_STOPWORDS = frozenset(
    ["the", "a", "an", "of", "and", "or", "to", "in", "for", "is", "are", "be", "shall", "must", "may", "with", "on", "by", "that", "this", "it", "as", "at", "from", "any", "its", "not", "no", "if", "or", "other", "under", "within", "act", "section", "any", "all", "being", "having", "here", "there", "when", "where", "which", "who", "whom", "what", "how", "why", "does", "do", "done"]
)


def _anchor_toks(text: str) -> set[str]:
    import re as _re

    return {
        t
        for t in _re.findall(r"[a-z0-9]+", (text or "").lower())
        if t not in _STOPWORDS and len(t) > 2
    }


def _anchor_overlap(query: set[str], text: str, weights: dict[str, float] | None = None) -> float:
    if not query:
        return 0.0
    toks = _anchor_toks(text)
    if weights:
        total = sum(weights.values())
        if total <= 0:
            return 0.0
        return sum(w for t, w in weights.items() if t in toks) / total
    return len(query & toks) / len(query)


def _anchor_idf_weights(o3_ids: list[str], payload_index: dict[str, dict], query: set[str]) -> dict[str, float]:
    """Rare query tokens weigh more, so a chunk carrying the operative term
    ("noxious", "single-use plastic") beats chunks with boilerplate overlap
    ("Corporation", "premises") that appear in every section."""
    import math

    df: dict[str, int] = {}
    n = 0
    for cid in o3_ids:
        toks = _anchor_toks(s0._chunk_text(payload_index.get(cid) or {}))
        n += 1
        for t in query & toks:
            df[t] = df.get(t, 0) + 1
    return {
        t: math.log((n + 1) / (df.get(t, 0) + 1)) + 0.1 for t in query
    }


def _anchor_query(question: Any) -> set[str]:
    raw = getattr(question, "raw", {}) or {}
    return _anchor_toks(
        " ".join(
            filter(
                None,
                [
                    getattr(question, "acceptable_conclusion", None) or "",
                    raw.get("question") or "",
                ],
            )
        )
    )


def _rank_o3_chunks(
    o3_ids: list[str],
    payload_index: dict[str, dict],
    query: set[str],
    weights: dict[str, float] | None = None,
) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for cid in o3_ids:
        text = s0._chunk_text(payload_index.get(cid) or {})
        if not text.strip():
            continue
        scored.append((_anchor_overlap(query, text, weights), cid))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return scored


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Evidence anchors
# --------------------------------------------------------------------------- #


def dual_score_targets() -> list[str]:
    data = json.loads(DUAL_TARGETS.read_text(encoding="utf-8"))
    qids = data.get("qids") or []
    if not qids:
        raise SystemExit(f"no qids in {DUAL_TARGETS.name}")
    return sorted(qids)


def load_o3_chunk_ids(manifest: dict, qid: str) -> list[str]:
    entry = (manifest.get("questions") or {}).get(qid) or {}
    o3 = (entry.get("conditions") or {}).get("O3_full_support") or {}
    return list(o3.get("context_chunk_ids") or [])


def _fill_anchor(anchor_ids: list[str], ranked: list[tuple[float, str]], payload_index: dict[str, dict]) -> list[str]:
    """Append best-ranked payload chunks not already anchored until the cap is
    roughly reached. Order within ``anchor_ids`` is preserved (gold/priority
    first), so the fill only ever adds headroom text."""
    chosen = set(anchor_ids)
    total = sum(len(s0._chunk_text(payload_index.get(cid) or {})) for cid in anchor_ids)
    if total >= ANCHOR_TEXT_CAP:
        return anchor_ids
    out = list(anchor_ids)
    for _score, cid in ranked:
        if total >= ANCHOR_TEXT_CAP:
            break
        if cid in chosen:
            continue
        text = s0._chunk_text(payload_index.get(cid) or {})
        if not text.strip():
            continue
        out.append(cid)
        chosen.add(cid)
        total += len(text)
    return out


def collect_evidence_anchor(
    qid: str,
    question: Any,
    manifest: dict,
    payload_index: dict[str, dict],
    gold_index: tuple,
) -> dict:
    """Verbatim O3 payload text for the question's primary gold units.

    Returns anchor chunk ids + text (capped) + provenance flags. Anchor source:

      gold_units      the gold-unit chunks that are in the payload (default)
      payload_ranked  gold chunks present but irrelevant — re-rank ALL O3 chunks
                      by question/reference overlap and anchor on the best ones
      first_o3_chunks nothing usable — anchored on the first chunks and flagged
                      ``anchor_is_fallback`` so a draft on such a qid is suspect
                      (may actually be evidence_missing)

    The payload_ranked path exists because gold resolution can land on the wrong
    section chunk while the operative text is in the same payload: quoting from
    the gold anchor would then be impossible even though the evidence is there.
    """
    from evaluation.experiment_b_topk_eval import gold_chunk_ids_for_units

    o3_ids = load_o3_chunk_ids(manifest, qid)
    o3_set = set(o3_ids)
    gold_cids = sorted(gold_chunk_ids_for_units(list(question.primary_units()), gold_index))
    anchor_ids = [cid for cid in gold_cids if cid in o3_set]
    fallback = False

    # relevance of the gold-derived anchor vs the whole payload (IDF-weighted)
    query = _anchor_query(question)
    weights = _anchor_idf_weights(o3_ids, payload_index, query)
    # The gold anchor is capped at ANCHOR_TEXT_CAP, so chunk ORDER decides which
    # text a drafter can quote from. Alphabetical order buries the operative
    # provision past the cap ("noxious", "single-use plastic" never reached the
    # anchor even though their chunks are gold). Rank gold chunks by IDF-weighted
    # query overlap so the rare, operative terms fill the cap first.
    anchor_ids = [
        cid
        for _score, cid in _rank_o3_chunks(anchor_ids, payload_index, query, weights)
    ]
    gold_text = "\n".join(
        s0._chunk_text(payload_index.get(cid) or {}) for cid in anchor_ids
    )
    gold_score = _anchor_overlap(query, gold_text, weights)
    ranked = _rank_o3_chunks(o3_ids, payload_index, query, weights)
    top_ids = [cid for _score, cid in ranked[:ANCHOR_RANKED_TOP_K]]
    ranked_best = _anchor_overlap(
        query,
        "\n".join(
            s0._chunk_text(payload_index.get(cid) or {}) for cid in top_ids
        ),
        weights,
    )
    anchor_source = "gold_units" if anchor_ids else "first_o3_chunks"
    # same-size comparison: gold chunks (capped) vs the top-K ranked chunks
    if anchor_ids and ranked_best >= ANCHOR_GOLD_MIN_SCORE and ranked_best > gold_score * ANCHOR_GOLD_RELATIVE:
        anchor_ids = top_ids
        anchor_source = "payload_ranked"
    if not anchor_ids:
        anchor_ids = o3_ids[:3]
        fallback = True
        anchor_source = "first_o3_chunks"
    # Gold units can resolve to the wrong section chunk while the operative
    # provision sits in another chunk of the SAME payload (Q055: the s25 consent
    # duties live in a chunk no gold unit points at — the gold anchor contained
    # zero occurrences of "consent"). Gold wins the front of the cap, then the
    # best-ranked remaining payload chunks fill whatever headroom is left, so a
    # drafter can always quote the operative text when it is in the payload.
    anchor_ids = _fill_anchor(anchor_ids, ranked, payload_index)
    texts: list[str] = []
    used_ids: list[str] = []
    total = 0
    for cid in anchor_ids:
        payload = payload_index.get(cid) or {}
        text = s0._chunk_text(payload).strip()
        if not text:
            continue
        if total + len(text) > ANCHOR_TEXT_CAP:
            text = text[: max(0, ANCHOR_TEXT_CAP - total)]
            if not text:
                break
        texts.append(f"[{cid}] {text}")
        used_ids.append(cid)
        total += len(text) + 8
        if total >= ANCHOR_TEXT_CAP:
            break
    units = [
        {"provision_id": u.provision_id, "section": u.section, "act": u.act}
        for u in question.primary_units()
    ]
    return {
        "anchor_chunk_ids": used_ids,
        "anchor_text": "\n\n".join(texts),
        "anchor_source": anchor_source,
        "anchor_gold_score": round(gold_score, 4),
        "anchor_ranked_best_score": round(ranked_best, 4),
        "anchor_from_gold_units": anchor_source == "gold_units",
        "anchor_is_fallback": fallback,
        "n_gold_corpus_chunks": len(gold_cids),
        "n_gold_in_o3": len([c for c in gold_cids if c in o3_set]),
        "n_o3_chunks": len(o3_ids),
        "primary_units": units,
    }


def build_anchor_map(residual_needed: bool = True) -> tuple[dict[str, dict], list[str]]:
    """Anchors for every dual-score target. Returns (map, errors)."""
    questions = s0.load_questions()
    manifest = s0.load_c_manifest()
    payload_index = s0.load_payload_index()
    if not payload_index or not manifest:
        return {}, ["payload_index.jsonl or C manifest unavailable — cannot cut statute anchors"]
    from evaluation.experiment_b_topk_eval import build_gold_index
    from evaluation.resolution import FamilyMap

    gold_index = build_gold_index(payload_index, FamilyMap())
    targets = dual_score_targets()
    anchors: dict[str, dict] = {}
    errors: list[str] = []
    for qid in targets:
        q = questions.get(qid)
        if q is None:
            errors.append(f"{qid}: not in benchmark")
            continue
        try:
            anchors[qid] = collect_evidence_anchor(qid, q, manifest, payload_index, gold_index)
        except Exception as exc:
            errors.append(f"{qid}: anchor failed: {exc}")
    return anchors, errors


# --------------------------------------------------------------------------- #
# Worksheet + CSV template
# --------------------------------------------------------------------------- #


def build_worksheet(anchors: dict[str, dict]) -> tuple[Path, Path]:
    questions = s0.load_questions()
    widened = json.loads(OVERLAY_PATH.read_text(encoding="utf-8")).get("widened_conclusions", {})
    d_perq = s0._load_jsonl_map(OUT / "experiment_D_per_question.jsonl")
    v2_perq = s0._load_jsonl_map(OUT / "evaluator_v2_per_question.jsonl")
    todo = [q for q in dual_score_targets() if q not in widened]
    lines = [
        "# Step 0b — Draft widened references from the statute",
        "",
        f"Targets without a widened conclusion: **{len(todo)}** of {len(dual_score_targets())} "
        "dual-score qids.",
        "",
        "For each packet: read the statute anchor, write a widened reference that states the",
        "**defensible statutory reading** (not a paraphrase of the candidate answer), quote the",
        "operative sentence(s) verbatim into `anchor_quote`, and explain in `note` why the v1",
        "reference was too narrow. Fill these into the CSV and run:",
        "",
        "    step0_dual_score_widen.py --from-csv <filled.csv> --merge --rescore --report",
        "",
        "A draft whose anchor cannot be quoted from the O3 payload is **not** reference_narrow —",
        "send it back for re-audit (it is evidence_missing).",
        "",
        "---",
        "",
    ]
    rows = ["question_id,add,note,anchor_quote"]
    for qid in todo:
        q = questions.get(qid)
        raw = getattr(q, "raw", {}) or {}
        a = anchors.get(qid) or {}
        v2row = v2_perq.get(qid, {})
        rec = (v2row.get("D2") or {})
        v1r = rec.get("v1") or {}
        v2r = rec.get("v2") or {}
        v1_ref = (q.acceptable_conclusion if q else None) or "(none recorded)"
        d2_ans = (d_perq.get(qid, {}).get("D2") or {}).get("answer") or "(not run)"
        lines += [
            f"### {qid} - {raw.get('difficulty', '?')} | {', '.join(raw.get('question_type', []) or []) or 'n/a'}",
            "",
            f"**Question:** {d_perq.get(qid, {}).get('question') or raw.get('question')}",
            "",
            f"**v1 reference (stays a valid alternative — never removed):** {v1_ref}",
            "",
            f"**Stored D2 answer** (v1 soft {v1r.get('answer_correctness', 'n/a')} | v2 soft {v2r.get('soft', 'n/a')}):",
            "",
            "```",
            d2_ans.strip()[:1500],
            "```",
            "",
            f"**O3 statute anchor** (source: {a.get('anchor_source') or 'gold_units'}; "
            f"gold_score {a.get('anchor_gold_score', 'n/a')} vs ranked_best "
            f"{a.get('anchor_ranked_best_score', 'n/a')}; "
            f"chunks: {', '.join(a.get('anchor_chunk_ids', []) or ['-'])}"
            f"{' — FALLBACK, gold unit not in O3: suspect evidence_missing' if a.get('anchor_is_fallback') else ''}):",
            "",
            "```",
            a.get("anchor_text", "(unavailable)").strip(),
            "```",
            "",
            "**Draft:** `add` = widened reference · `note` = why v1 was narrow · `anchor_quote` = verbatim statute",
            "",
            "---",
            "",
        ]
        rows.append(f"{qid},,\"\",")
    WORKSHEET.write_text("\n".join(lines), encoding="utf-8")
    CSV_TEMPLATE.write_text("\n".join(rows), encoding="utf-8")
    return WORKSHEET, CSV_TEMPLATE


# --------------------------------------------------------------------------- #
# Draft loading + validation
# --------------------------------------------------------------------------- #


def load_drafts(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Read filled CSV {question_id, add, note, anchor_quote}; skip empties."""
    import csv as _csv

    records: dict[str, dict] = {}
    skipped: list[str] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(_csv.DictReader(f), start=2):
            qid = (row.get("question_id") or "").strip()
            add = (row.get("add") or "").strip()
            if not qid:
                skipped.append(f"row {i}: empty question_id")
                continue
            if not add:
                skipped.append(f"row {i} ({qid}): empty add — nothing drafted")
                continue
            records[qid] = {
                "add": add,
                "note": (row.get("note") or "").strip(),
                "anchor_quote": (row.get("anchor_quote") or "").strip(),
            }
    return records, skipped


def validate_widening(
    qid: str,
    draft: dict,
    anchor: dict | None,
    v1_reference: str | None,
    d2_answer: str | None,
    existing_overlay: dict,
) -> dict:
    """Mechanical gates for one draft. Returns {qid, status, reasons, entry?}.

    status: valid | invalid | already_widened
    """
    add = draft.get("add", "").strip()
    quote = draft.get("anchor_quote", "").strip()
    reasons: list[str] = []
    if qid in existing_overlay.get("widened_conclusions", {}):
        return {
            "qid": qid,
            "status": "already_widened",
            "reasons": ["overlay already widens this qid — edit the overlay by hand if truly needed"],
        }
    if anchor is None:
        return {"qid": qid, "status": "invalid", "reasons": ["no evidence anchor available"]}
    if len(add) < MIN_ADD_CHARS:
        reasons.append(f"add too short (<{MIN_ADD_CHARS} chars) to state a statutory reading")
    if len(add) > MAX_ADD_CHARS:
        reasons.append(f"add too long (>{MAX_ADD_CHARS} chars) — tighten to the operative reading")
    if not quote:
        reasons.append("anchor_quote missing — the statute anchor is mandatory")
    elif anchor.get("anchor_text") and not quote_in_evidence(quote, anchor["anchor_text"]):
        reasons.append(
            "anchor_quote is NOT a verbatim substring of the O3 payload — if the text truly "
            "is not in the payload, the label is evidence_missing, not reference_narrow"
        )
    if anchor.get("anchor_is_fallback"):
        reasons.append("anchor was fallback (gold unit not in O3) — verify the label is not evidence_missing")
    if v1_reference and add:
        ov = token_overlap(add, v1_reference).get("correctness", 0.0)
        if ov > V1_DUP_MAX_OVERLAP:
            reasons.append(f"add duplicates the v1 reference (overlap {ov:.2f}) — nothing widened")
    if d2_answer and add:
        ov = token_overlap(add, d2_answer).get("correctness", 0.0)
        if ov > INDEPENDENCE_MAX_OVERLAP:
            reasons.append(
                f"add paraphrases the stored D2 answer (overlap {ov:.2f} > {INDEPENDENCE_MAX_OVERLAP}) "
                "— widening must come from the statute, not from the candidate"
            )
    if reasons:
        return {"qid": qid, "status": "invalid", "reasons": reasons}
    return {
        "qid": qid,
        "status": "valid",
        "reasons": [],
        "entry": {
            "add": add,
            "note": draft.get("note", ""),
            "anchor_quote": quote,
            "anchor_chunk_ids": anchor.get("anchor_chunk_ids", []),
            "step0_source": "dual_score_widen (Step 0 reference_narrow label)",
        },
    }


def validate_all(drafts: dict[str, dict], anchors: dict[str, dict]) -> dict:
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    questions = s0.load_questions()
    d_perq = s0._load_jsonl_map(OUT / "experiment_D_per_question.jsonl")
    results: list[dict] = []
    for qid, draft in sorted(drafts.items()):
        q = questions.get(qid)
        v1_ref = (q.acceptable_conclusion if q else None)
        d2 = (d_perq.get(qid, {}).get("D2") or {}).get("answer")
        results.append(validate_widening(qid, draft, anchors.get(qid), v1_ref, d2, overlay))
    return {
        "validated_at": now(),
        "n_drafts": len(drafts),
        "n_valid": sum(1 for r in results if r["status"] == "valid"),
        "n_invalid": sum(1 for r in results if r["status"] == "invalid"),
        "n_already_widened": sum(1 for r in results if r["status"] == "already_widened"),
        "results": results,
    }


# --------------------------------------------------------------------------- #
# Merge + rescore + report
# --------------------------------------------------------------------------- #


def merge_overlay(validation: dict, *, dry_run: bool = False) -> dict:
    """Additively merge valid drafts into widened_conclusions (never narrows)."""
    overlay_path = OVERLAY_PATH
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    widened = overlay.setdefault("widened_conclusions", {})
    added: list[str] = []
    for r in validation.get("results", []):
        if r.get("status") != "valid":
            continue
        qid = r["qid"]
        if qid in widened:
            continue
        if not dry_run:
            widened[qid] = r["entry"]
        added.append(qid)
    revisions = overlay.setdefault("revisions", [])
    rev = {
        "date": now(),
        "n_added": len(added),
        "qids": added,
        "source": "step0_dual_score_widen.py --merge (Step 0 reference_narrow labels; "
        "quote-validated against O3 payload, independence-checked vs stored D2)",
    }
    if added and not dry_run:
        revisions.append(rev)
        tmp = overlay_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(overlay, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, overlay_path)
    return {"added": added, "revision": rev, "dry_run": dry_run}


def run_rescore() -> dict:
    """Rerun the frozen rescoring (0 LLM calls) with the updated overlay."""
    env = dict(os.environ, SKIP_SCHEMA_CHECK="1")
    proc = subprocess.run(  # noqa: S603 — fixed argv: sys.executable + repo script
        [sys.executable, str(RESCORE_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=600,
    )
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-6:])
    return {
        "returncode": proc.returncode,
        "stdout_tail": tail,
        "stderr_tail": "\n".join((proc.stderr or "").strip().splitlines()[-3:]),
        "ok": proc.returncode == 0,
    }


def build_report() -> dict:
    """Dual-score report: v1 AND v2 per condition for every dual-score target."""
    labels = (json.loads(LABELS_PUBLISHED.read_text(encoding="utf-8")) if LABELS_PUBLISHED.exists() else {}).get("labels", {})
    v2_perq = s0._load_jsonl_map(OUT / "evaluator_v2_per_question.jsonl")
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8")).get("widened_conclusions", {})
    targets = dual_score_targets()
    rows: list[dict] = []
    flips = {"d2_v2_flip_to_correct": 0, "d2_still_incorrect": 0}
    for qid in targets:
        rec = v2_perq.get(qid, {})
        d2 = rec.get("D2") or {}
        v1, v2 = d2.get("v1") or {}, d2.get("v2") or {}
        flipped = bool(v2.get("correct")) and not bool(v1.get("correct"))
        if flipped:
            flips["d2_v2_flip_to_correct"] += 1
        elif not bool(v2.get("correct")):
            flips["d2_still_incorrect"] += 1
        rows.append(
            {
                "qid": qid,
                "widened": qid in overlay,
                "widened_by_step0b": bool((overlay.get(qid) or {}).get("step0_source")),
                "human_label": labels.get(qid),
                "v1_soft": v1.get("answer_correctness"),
                "v1_correct": v1.get("correct"),
                "v2_soft": v2.get("soft"),
                "v2_correct": v2.get("correct"),
                "v2_flipped_to_correct": flipped,
                "per_condition_v2_correct": {
                    c: (((rec.get(c) or {}).get("v2") or {}).get("correct")) for c in CONDITIONS
                },
            }
        )
    n_widened = sum(1 for r in rows if r["widened"])
    report = {
        "generated_at": now(),
        "gate": "reference_narrow (plan sec 5.2 Step 1)",
        "keep_if": "Both the old and the new score are reported until the references are fixed.",
        "reject_if": "A generation call was spent to chase the narrow reference.",
        "n_targets": len(targets),
        "n_widened": n_widened,
        "n_unwidened": len(targets) - n_widened,
        "d2_v1_correct": sum(1 for r in rows if r["v1_correct"]),
        "d2_v2_correct": sum(1 for r in rows if r["v2_correct"]),
        **flips,
        "rows": rows,
    }
    lines = [
        "# Step 0b — Dual-score report (reference_narrow targets)",
        "",
        f"Targets: **{report['n_targets']}** | widened: **{report['n_widened']}** | "
        f"not yet widened: **{report['n_unwidened']}**",
        "",
        "D2 binary under v1: "
        f"**{report['d2_v1_correct']}** -> under v2: **{report['d2_v2_correct']}** "
        f"(flips: {flips['d2_v2_flip_to_correct']}, still incorrect: {flips['d2_still_incorrect']})",
        "",
        "Both scores are reported per the registered gate. v1 remains the headline comparator;",
        "v2 is the corrected one. No generation call was spent on these qids.",
        "",
        "| qid | widened | v1 soft | v2 soft | v1 bin | v2 bin | flip |",
        "|---|---|---:|---:|:---:|:---:|:---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['qid']} | {'yes' if r['widened'] else 'no'} "
            f"| {r['v1_soft'] if r['v1_soft'] is not None else 'n/a'} "
            f"| {r['v2_soft'] if r['v2_soft'] is not None else 'n/a'} "
            f"| {'Y' if r['v1_correct'] else 'N'} | {'Y' if r['v2_correct'] else 'N'} "
            f"| {'FLIP' if r['v2_flipped_to_correct'] else ''} |"
        )
    REPORT_JSON.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Step 0b — draft + validate + merge widened references (no LLM)"
    )
    ap.add_argument("--worksheet", action="store_true", help="write statute worksheet + CSV template")
    ap.add_argument("--from-csv", type=Path, default=None, metavar="PATH", help="filled drafts CSV")
    ap.add_argument("--validate", action="store_true", help="validate drafts only (no writes)")
    ap.add_argument("--merge", action="store_true", help="merge valid drafts into the overlay")
    ap.add_argument("--dry-run", action="store_true", help="validate + show merge plan, write nothing")
    ap.add_argument("--rescore", action="store_true", help="rerun rescore_evaluator_v2 (0 LLM calls)")
    ap.add_argument("--report", action="store_true", help="write step0_dual_score_report.{json,md}")
    args = ap.parse_args(argv)

    if args.worksheet:
        anchors, errors = build_anchor_map()
        for e in errors:
            print(f"anchor error: {e}", file=sys.stderr)
        ws, csv_t = build_worksheet(anchors)
        print(f"worksheet: {ws} ({len(ws.read_text(encoding='utf-8').split('### Q')) - 1} qids to draft)")
        print(f"csv template: {csv_t}")
        if not args.validate and not args.merge and not args.rescore and not args.report:
            return 0 if not errors else 1

    if not (args.from_csv or args.rescore or args.report):
        if not args.worksheet:
            ap.print_help()
            return 1
        return 0

    validation: dict | None = None
    if args.from_csv:
        if not args.from_csv.exists():
            print(f"csv not found: {args.from_csv}", file=sys.stderr)
            return 1
        drafts, skipped = load_drafts(args.from_csv)
        for s in skipped:
            print(f"skipped: {s}")
        anchors, errors = build_anchor_map()
        for e in errors:
            print(f"anchor error: {e}", file=sys.stderr)
        validation = validate_all(drafts, anchors)
        print(
            f"validated: {validation['n_drafts']} drafts -> "
            f"valid {validation['n_valid']}, invalid {validation['n_invalid']}, "
            f"already_widened {validation['n_already_widened']}"
        )
        for r in validation["results"]:
            if r["status"] != "valid":
                for reason in r["reasons"]:
                    print(f"  {r['qid']} [{r['status']}]: {reason}")
        (OUT / "step0_dual_score_validation.json").write_text(
            json.dumps(validation, indent=1, ensure_ascii=False), encoding="utf-8"
        )

    if args.merge:
        if validation is None:
            print("--merge requires --from-csv", file=sys.stderr)
            return 1
        merged = merge_overlay(validation, dry_run=args.dry_run)
        verb = "would add" if args.dry_run else "added"
        print(f"overlay {verb}: {len(merged['added'])} widenings {merged['added']}")
        if args.dry_run:
            return 0

    if args.rescore:
        res = run_rescore()
        print(f"rescore rc={res['returncode']}")
        print(res["stdout_tail"])
        if res["stderr_tail"]:
            print(res["stderr_tail"], file=sys.stderr)
        if not res["ok"]:
            return 1

    if args.report:
        rep = build_report()
        print(
            f"dual-score report: targets {rep['n_targets']} | widened {rep['n_widened']} | "
            f"D2 v1 {rep['d2_v1_correct']} -> v2 {rep['d2_v2_correct']} correct "
            f"(flips {rep['d2_v2_flip_to_correct']})"
        )
        print(f"wrote {REPORT_JSON.name} + {REPORT_MD.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
