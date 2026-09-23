"""Experiment E — Evidence-Binding / Citation Verification (E1 repair, E2 removal-only).

Design: Experiment_E_Citation_Verification_Design.md (repo root).

Architecture (no auditor layer, per the Experiment D §24 outcome):
    O3 evidence (unchanged, hash-verified)  +  D2 structured analysis (REUSED
    checkpoint — 0 new reasoning calls)
        -> deterministic citation check (no LLM)
        -> [E1 only] ONE constrained verify-and-repair call
        -> final answer

Conditions:
    D2 (baseline) : reused verbatim from the Experiment D checkpoint.
    E1            : check -> repair call -> repaired answer (1 new call/question).
    E2            : check -> drop unsupported citation markers -> D2 answer (0 calls).

Fixed-call budget: 150 new successful generations (E1 only); E2 is free.
The D3 failure modes are explicitly guarded against:
    - repair may only re-bind citations or remove claims; new legal positions are
      forbidden by the repair prompt;
    - repair-regression rate on D2-correct answers is reported as a first-class
      metric (must stay ~0 for the layer to be recommendable).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

# Experiment D's module installs the matplotlib stub (if needed) and exports the
# metric/evaluator stack; E imports FROM D so the scoring path stays identical.
from evaluation.experiment_d_reasoning_eval import (
    D_D2_CKPT,
    D_D2_CKPT_STUB,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_CTX_CHARS,
    O3_COND,
    _COracleContextBuilder,
    _DNoRetryClient,
    _DResp,
    _load_ckpt,
    _log_call,
    _score_like_service,
    extract_json_object,
    load_c_questions,
    to_retrieved_chunk,
)
from evaluation.experiment_d_reasoning_eval import (
    _CALL_COUNT as D_CALL_COUNT,
    _ACTIVE_CAP as D_ACTIVE_CAP,
)
from evaluation.eval_e2e_v2 import load_payload_index
from evaluation.benchmark import load_questions
from evaluation.resolution import FamilyMap
from evaluation.experiment_b_topk_eval import (
    _mean,
    _pctl,
    build_gold_index,
    compute_metrics,
)
from app.rag.generation.llm_client import GroundedLLMResponse

# --------------------------------------------------------------------------- #
# Paths + constants
# --------------------------------------------------------------------------- #
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_DIR.mkdir(parents=True, exist_ok=True)

E_VERIFICATION = OUT_DIR / "experiment_E_verification.jsonl"  # deterministic check details
E_VERIFICATION_STUB = OUT_DIR / "experiment_E_verification_stub.jsonl"
E_REPAIR = OUT_DIR / "experiment_E_repair.jsonl"  # E1 repair records
E_CKPT_E1 = OUT_DIR / "experiment_E_e1_checkpoint.jsonl"
E_CKPT_E2 = OUT_DIR / "experiment_E_e2_checkpoint.jsonl"
E_CKPT_E1_STUB = OUT_DIR / "experiment_E_e1_checkpoint_stub.jsonl"
E_CKPT_E2_STUB = OUT_DIR / "experiment_E_e2_checkpoint_stub.jsonl"
E_CALLS_JSONL = OUT_DIR / "experiment_E_calls.jsonl"  # per-call accounting
E_CALLS_STUB_JSONL = OUT_DIR / "experiment_E_calls_stub.jsonl"
E_RESULTS = OUT_DIR / "experiment_E_results.json"
E_PER_QUESTION = OUT_DIR / "experiment_E_per_question.jsonl"
E_ERROR_TAX = OUT_DIR / "experiment_E_error_taxonomy.json"
E_TRANSITIONS = OUT_DIR / "experiment_E_transition_matrix.json"
E_ACCOUNTING = OUT_DIR / "experiment_E_call_accounting.json"
E_SUMMARY = OUT_DIR / "experiment_E_summary.md"
E_PLOT_DIR = OUT_DIR / "plots"

E_CONDS = ["D2", "E1", "E2"]
PLANNED_CALLS_E = 150  # hard cap: ONE repair call per question (E1); E2 = 0
BUDGET_STOP_EXIT = 3
REPAIR_MAX_TOKENS = 8192  # 4096 truncated the repair JSON on large-context questions (Q016/Q045: exactly-4096-token outputs, unparseable); same transport-level fix as D's structured cap
MAX_REPAIR_ATTEMPTS = 3  # hard-fail a qid after this many generation attempts

# --------------------------------------------------------------------------- #
# Deterministic citation check (design sec 4) — NO LLM
# --------------------------------------------------------------------------- #
_SRC_RE = re.compile(r"\[(\d{1,2})\]")
_SEC_RE = re.compile(r"\bsection\s+(\d+[A-Z]?(?:\([0-9A-Za-z]+\))*)", re.I)
_SEC_BARE_RE = re.compile(
    r"\b(\d{1,3}[A-Z]?(?:\([0-9A-Za-z]+\))+)"
)  # e.g. 42(5), 68(1)(b); no trailing \b — ')\b' never matches
_HEADING_NUM_RE = re.compile(
    r"(?:^|(?<=[.\n]))\s*(\d{1,3})\.\s+[A-Z]"
)  # numbered headings: "49. 69. Power to compound offences."
_GAZETTE_HEADER_RE = re.compile(
    r"PART\s+[IVXLC]+\s*[—\-]\s*Section\s+\d+", re.I
)  # Gazette page headers, not Act sections
_CLAUSE_MARKER_RE = re.compile(
    r"(?:^|\n)\s*(\([0-9A-Za-z]+\))"
)  # definition/clause markers at line start: "(za) \"licence\" means ..."


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _section_numbers_in(text: str) -> set[str]:
    """Extract section identifiers like 42(5), section 68, 26(1)(a) from text.

    Three capture classes:
      - 'section 42(5)' explicit references;
      - bare subsection references like 42(5) / 68(1)(b);
      - numbered section HEADINGS ("69. Power to compound offences.") — common
        in the statute chunks, where the section body follows the bare number.
    """
    found: set[str] = set()
    cleaned = _GAZETTE_HEADER_RE.sub(" ", text or "")
    for m in _SEC_RE.finditer(cleaned):
        found.add(m.group(1).lower().replace(" ", ""))
    for m in _SEC_BARE_RE.finditer(cleaned):
        found.add(m.group(1).lower())
    for m in _HEADING_NUM_RE.finditer(cleaned):
        found.add(m.group(1).lower())
    return found


def _sections_match(claim_secs: set[str], chunk_secs: set[str], chunk_text: str) -> bool:
    """Hierarchical section matching to avoid over-strict convictions.

    A claim of '17' matches a chunk that cites '17(3)' (the chunk IS section
    17), and a claim of '425(2)(d)' matches a chunk keyed '425' (the chunk is
    the parent section).  Exact intersection always wins; hierarchy applies
    only when exact intersection fails.
    """
    if claim_secs & chunk_secs:
        return True

    def _parents(ident: str) -> set[str]:
        parts = re.split(r"\(|\)", ident)
        parts = [p for p in parts if p]
        return {"".join(parts[:i]).lower() for i in range(1, len(parts))}

    for cs in claim_secs:
        cp = _parents(cs)
        for hs in chunk_secs:
            if hs in cp or cs in _parents(hs):
                return True

    # Definition-clause marker: claim '2(za)' matches a chunk whose text opens
    # a clause '(za)' (definition items are keyed by the clause letter only).
    clause_markers = {m.group(1).lower() for m in _CLAUSE_MARKER_RE.finditer(chunk_text or "")}
    for cs in claim_secs:
        m = re.fullmatch(r"\d+\(([0-9A-Za-z]+)\)", cs)
        if m and f"({m.group(1).lower()})" in clause_markers:
            return True
    return False


def _act_names_in(text: str) -> set[str]:
    """Extract statute names with a YEAR (e.g. 'fss act 2006', '...act 3-2023').

    Requiring a year keeps this signal high-precision: without it the regex
    captures whole clauses that merely END in 'act' ("... permissible under
    this act"), which produced flagrant false mismatches on the D2 corpus.
    Relative references ("this Act") are deliberately not matched.
    """
    t = _norm_ws(text)
    names: set[str] = set()
    for m in re.finditer(r"([a-z][a-z &\-]{2,60}(?:act|regulation|rules|ordinance)\s*\d{2,4}(?:-[a-z]?\d{1,4})?)", t):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(name.split()) <= 10:
            names.add(name)
    return names


def _marker_span(text: str, marker: str) -> str:
    """The clause citing a [n] marker: sentence- AND semicolon-bounded window.

    Legal analyses frequently join independent attributions with semicolons
    ("Source [14] defines X appointed under section 37; [16] provides the
    appointment mechanism; [15] lists ...").  Splitting on sentence ends only
    would drag a section mention from a neighbouring clause into this marker's
    window and produce false misattribution flags.
    """
    idx = text.find(marker)
    if idx < 0:
        return ""
    bounds = [
        pos + 1 for pos in (text.rfind(".", 0, idx), text.rfind("\n", 0, idx), text.rfind(";", 0, idx)) if pos >= 0
    ]
    start = max(bounds) if bounds else 0
    ends = [
        pos
        for pos in (
            text.find(".", idx + len(marker)),
            text.find("\n", idx + len(marker)),
            text.find(";", idx + len(marker)),
        )
        if pos >= 0
    ]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end]


def check_claim_support(
    claim: str,
    marker: str,
    chunk_text: str,
) -> dict:
    """Heuristic support check for one (claim, [n], chunk) triple.

    Labels:
      supported              - section/act/keyword signals agree
      misattributed          - concrete mismatch (section number or act name)
      unverifiable-by-heuristics - no signal either way (never 'failed'; E2
                               keeps these, the repair prompt may re-check them)
    """
    claim_l, chunk_l = _norm_ws(claim), _norm_ws(chunk_text)
    claim_secs = _section_numbers_in(claim)
    chunk_secs = _section_numbers_in(chunk_text)

    # 1) Section-number cross-check (highest-value rule: the D taxonomy showed
    #    wrong-subsection/wrong-act claims dominate the misattribution family).
    # POSITIVE-MISMATCH-ONLY policy (precision first, per the D3 lesson):
    #   - both sides carry section numbers and they are disjoint -> misattributed
    #   - claim carries numbers but the chunk shows none -> NOT demonstrable
    #     (chunks are mid-section fragments; their headings are often stripped),
    #     so label unverifiable-by-heuristics and let the repair call judge it.
    if claim_secs and chunk_secs:
        if _sections_match(claim_secs, chunk_secs, chunk_text):
            sec_ok = True
        else:
            return {
                "label": "misattributed",
                "reason": f"claim cites section(s) {sorted(claim_secs)} but chunk contains {sorted(chunk_secs)}",
                "signals": {"claim_sections": sorted(claim_secs), "chunk_sections": sorted(chunk_secs)},
            }
    elif claim_secs and not chunk_secs:
        return {
            "label": "unverifiable-by-heuristics",
            "reason": f"claim cites section(s) {sorted(claim_secs)} but the chunk excerpt shows no section identifier",
            "signals": {"claim_sections": sorted(claim_secs)},
        }

    # 2) Act-name cross-check (Amendment-vs-Principal-Act confusion).
    # Year-bearing names only (see _act_names_in); mismatch is asserted only
    # when BOTH sides name acts with years and they are disjoint.
    claim_acts = _act_names_in(claim)
    chunk_acts = _act_names_in(chunk_text)
    if claim_acts and chunk_acts and not (claim_acts & chunk_acts):
        return {
            "label": "misattributed",
            "reason": f"claim names act(s) {sorted(claim_acts)} but the chunk is from {sorted(chunk_acts)}",
            "signals": {"claim_acts": sorted(claim_acts), "chunk_acts": sorted(chunk_acts)},
        }

    # 3) Keyword-overlap support signal (weak but directional).  Reached only
    # when no section/act mismatch was demonstrated; it can confirm ('supported')
    # but never convict ('misattributed') — misattribution requires a concrete
    # section/act mismatch from checks 1-2.
    stop = {
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "or",
        "in",
        "under",
        "shall",
        "be",
        "is",
        "for",
        "with",
        "by",
        "any",
        "such",
        "provided",
        "section",
        "act",
    }
    claim_kw = {w for w in re.findall(r"[a-z]+", claim_l) if w not in stop and len(w) > 3}
    chunk_kw = {w for w in re.findall(r"[a-z]+", chunk_l) if w not in stop and len(w) > 3}
    overlap = len(claim_kw & chunk_kw) / max(1, len(claim_kw))
    if overlap >= 0.25:
        label = "supported"
    else:
        label = "unverifiable-by-heuristics"
    return {
        "label": label,
        "reason": f"keyword overlap {overlap:.2f} on {len(claim_kw)} content words",
        "signals": {"keyword_overlap": round(overlap, 3)},
    }


def build_claim_index(analysis: dict | None) -> list[dict]:
    """Collect every (claim, marker) pair from the analysis's source-bearing fields.

    Two bindings are recognised:
      inline  - the marker occurs inside a text value ("... under section 31 [2].")
      sibling - a dict carries a source pointer next to a text value
                ({"condition": "licence required", "source": "[3]"}); the
                pointer is paired with the sibling text, which is how the D2
                prompt formats structured sources.  Bare source values
                ("source": "[1]") are NOT claims and are skipped.
    """
    if not isinstance(analysis, dict):
        return []
    pairs: list[tuple[str, str, str]] = []  # (claim_text, marker, field)

    _TEXT_VALUE_KEYS = (
        "claim",
        "condition",
        "rule",
        "exception",
        "description",
        "reason",
        "definition",
        "fact",
        "relevance",
        "resolution",
    )

    def _source_markers(src: Any) -> list[str]:
        if isinstance(src, str) and _SRC_RE.fullmatch(src.strip()):
            return [src.strip()]
        if isinstance(src, list):
            return [s.strip() for s in src if isinstance(s, str) and _SRC_RE.fullmatch(s.strip())]
        return []

    def _walk(node: Any, field: str) -> None:
        if isinstance(node, str):
            for m in _SRC_RE.finditer(node):
                pairs.append((node, m.group(0), field))
        elif isinstance(node, list):
            for item in node:
                _walk(item, field)
        elif isinstance(node, dict):
            # sibling binding: a pure source pointer next to a text value
            srcs = _source_markers(node.get("source"))
            if srcs:
                for tk in _TEXT_VALUE_KEYS:
                    tv = node.get(tk)
                    if isinstance(tv, str) and tv.strip():
                        for sm in srcs:
                            pairs.append((tv, sm, f"{field}.source"))
            for v in node.values():
                _walk(v, field)

    for key in (
        "legal_conclusion",
        "issue",
        "governing_provisions",
        "definitions",
        "legal_rules",
        "conditions",
        "exceptions_and_provisos",
        "fact_condition_mapping",
        "cross_references",
        "conflicts_or_hierarchy",
        "supporting_evidence",
        "uncertainties",
    ):
        _walk(analysis.get(key), key)

    # Deduplicate (claim, marker) pairs; keep field provenance of first sighting.
    seen: dict[tuple[str, str], dict] = {}
    for claim, marker, field in pairs:
        # Skip marker-only "claims": a value that is just the pointer ("[1]")
        # is not a claim and would only add unverifiable noise pairs.
        residue = re.sub(r"[^a-z]+", " ", claim.replace(marker, " ").lower()).strip()
        if len(residue) < 3:
            continue
        k = (claim, marker)
        if k not in seen:
            seen[k] = {"claim": claim, "marker": marker, "field": field}
    return list(seen.values())


def deterministic_citation_check(
    analysis: dict | None,
    citations: list[dict],
) -> dict:
    """Run the heuristic check over all claim/marker pairs (design sec 4).

    The claim window is the SENTENCE containing the marker (not the whole field
    value) so multi-marker fields are checked per attribution, not over-flagged.
    """
    by_idx = {c["index"]: c for c in (citations or [])}
    results: list[dict] = []
    counts = Counter()
    for pair in build_claim_index(analysis):
        marker = pair["marker"]
        try:
            idx = int(marker.strip("[]"))
        except ValueError:
            continue
        chunk = by_idx.get(idx)
        chunk_text = (
            str((chunk or {}).get("text") or (chunk or {}).get("chunk_text") or "")
            if isinstance(chunk, dict)
            else getattr(chunk, "text", "")
        )
        claim_window = _marker_span(pair["claim"], marker) or pair["claim"]
        if not chunk_text:
            # Marker beyond the context's citation list: structurally invalid.
            res = {
                "label": "misattributed",
                "reason": f"{marker} is outside the supplied evidence range",
                "signals": {},
            }
        else:
            res = check_claim_support(claim_window, marker, chunk_text)
        results.append({
            **pair,
            "claim_window": claim_window,
            **res,
            "chunk_id": (chunk or {}).get("chunk_id") if isinstance(chunk, dict) else getattr(chunk, "chunk_id", None),
        })
        counts[res["label"]] += 1
    return {
        "claims_checked": len(results),
        "labels": dict(counts),
        "results": results,
    }


# --------------------------------------------------------------------------- #
# E2 — removal-only ablation (0 LLM calls)
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# E2 — removal-only ablation (0 LLM calls)
# --------------------------------------------------------------------------- #
def e2_answer(d2_answer: str, check: dict, max_index: int) -> str:
    """E2: the D2 answer with unsupported citation markers removed.

    Removal-only ablation (design sec 3): drop markers flagged misattributed,
    keep everything else — no LLM, no rewording.  Markers outside the evidence
    range are dropped too.  Unverifiable-by-heuristics markers are KEPT (no
    evidence they are wrong).
    """
    bad = set()
    use_labels: dict[str, set[str]] = {}
    for r in check["results"]:
        use_labels.setdefault(r["marker"], set()).add(r["label"])
    for marker, labels in use_labels.items():
        # Only remove a marker when EVERY checked use of it is misattributed;
        # mixed evidence means the marker is probably real and removal would
        # over-strip the answer (the repair call handles mixed cases).
        if labels == {"misattributed"}:
            bad.add(marker)
    text = d2_answer or ""
    for marker in sorted(bad, key=len, reverse=True):
        text = text.replace(marker, "")
    # collapse artefacts like "Section  (5)" / double spaces created by removal
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+\n", "\n", text).strip()
    # Deterministic citation repair parity with D: if all markers were removed
    # and none remain, fall back to the analysis source marks (1..max_index).
    if not _SRC_RE.search(text):
        marks = set()
        for r in check["results"]:
            if r["label"] == "supported":
                try:
                    n = int(r["marker"].strip("[]"))
                    if 1 <= n <= max_index:
                        marks.add(n)
                except ValueError:
                    pass
        if marks:
            tail = " ".join(f"[{n}]" for n in sorted(marks))
            text = f"{text}\n\nSources: {tail}"
    return text


# --------------------------------------------------------------------------- #
# E1 repair prompt + call (design sec 5) — FIXED for the whole experiment
# --------------------------------------------------------------------------- #
REPAIR_SYSTEM_PROMPT = (
    "You are a legal citation verifier. You will receive a question, numbered "
    "legal evidence sources, and a structured legal analysis in which some "
    "citations were flagged as misattributed by a deterministic checker (the "
    "claim attributes content to a cited source that the source does not "
    "contain). Your ONLY task is to repair citations: for each flagged claim, "
    "either (a) re-bind it to the correct evidence marker from the supplied "
    "sources whose text actually supports the claim, or (b) remove the claim "
    "if no supplied source supports it, or (c) keep it unchanged if, after "
    "reading the full cited source, the claim IS supported (the checker can "
    "only nominate; you adjudicate). You MUST NOT introduce new legal "
    "positions, provisions, definitions, conditions, exceptions or facts that "
    "are not already in the analysis; you MUST NOT change the analysis's "
    "legal conclusion unless a flagged claim was its only support, in which "
    "case regenerate only that conclusion sentence from the remaining "
    "supported claims. Output ONLY one JSON object with keys: "
    "repaired_claims (list of {claim, old_marker, new_marker, basis}); "
    "removed_claims (list of {claim, old_marker, reason}); "
    "kept_claims (list of {claim, marker, basis} for flags judged false "
    "alarms); "
    "conclusion_unchanged (boolean); final_answer (string: the full corrected "
    "answer to the question, citing sources with [n] markers consistent with "
    "the repaired analysis). Use only the provided evidence."
)


def render_repair_prompts(
    question: str,
    context: str,
    analysis_json: str,
    flagged_json: str,
) -> tuple[str, str]:
    user = (
        f"QUESTION:\n{question}\n\n"
        f"EVIDENCE SOURCES:\n{context}\n\n"
        f"STRUCTURED ANALYSIS:\n{analysis_json}\n\n"
        f"FLAGGED CITATIONS (deterministic checker output; repair each):\n{flagged_json}"
    )
    return REPAIR_SYSTEM_PROMPT, user


def validate_repair_payload(obj: Any, question: str) -> tuple[bool, str]:
    """Validate the repair call's JSON payload (constrained-repair contract)."""
    if not isinstance(obj, dict):
        return False, "not a JSON object"
    fa = obj.get("final_answer")
    if not isinstance(fa, str) or not fa.strip():
        return False, "empty final_answer"
    for key in ("repaired_claims", "removed_claims", "conclusion_unchanged"):
        if key not in obj:
            return False, f"missing key: {key}"
    if not isinstance(obj["repaired_claims"], list) or not isinstance(obj["removed_claims"], list):
        return False, "repaired_claims/removed_claims must be lists"
    if "kept_claims" in obj and not isinstance(obj["kept_claims"], list):
        return False, "kept_claims must be a list"
    if not isinstance(obj["conclusion_unchanged"], bool):
        return False, "conclusion_unchanged must be boolean"
    # Constrained-repair guard: the answer must still address the question at
    # all (cheap lexical containment of a question keyword is NOT required —
    # instead we reject only degenerate outputs).
    if len(fa.strip()) < 40:
        return False, "final_answer implausibly short"
    return True, ""


def e1_answer_from_payload(payload: dict, check: dict, max_index: int) -> str:
    """Deterministic post-processing of the repair payload's final answer.

    The repair prompt constrains the model, but enforcement is deterministic:
    any [n] marker outside 1..max_index in the repaired answer is dropped, and
    if the answer carries no markers at all we re-attach the surviving
    supported ones (same convention as D's repair_citation_markers).
    """
    text = str(payload.get("final_answer") or "").strip()
    present = {int(n) for n in _SRC_RE.findall(text) if int(n) >= 1}
    out_of_range = {n for n in present if n > max_index}
    for n in sorted(out_of_range):
        text = text.replace(f"[{n}]", "")
    if not _SRC_RE.search(text):
        keep = [
            int(r["marker"].strip("[]"))
            for r in check["results"]
            if r["label"] == "supported" and r["marker"].strip("[]").isdigit()
        ]
        valid = sorted({n for n in keep if 1 <= n <= max_index})
        if valid:
            text = f"{text}\n\nSources: " + " ".join(f"[{n}]" for n in valid)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Per-question runners
# --------------------------------------------------------------------------- #
def _build_context(manifest_q: dict, qid: str, payload_index: dict):
    """Identical O3 context construction as D2/D3 (hash-verified upstream)."""
    entry = manifest_q[qid]
    cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry["question"], chunks, "general_qa")
    return chunks, built, len(built.citations or [])


def run_e_one(
    qid: str,
    manifest_q: dict,
    payload_index: dict,
    client: Any,
    calls_path: Path | None,
    lock: threading.Lock,
    d2_rec: dict,
) -> dict:
    """One question through E2 (deterministic, 0 calls) + E1 (1 repair call)."""
    chunks, built, max_idx = _build_context(manifest_q, qid, payload_index)
    analysis = d2_rec.get("analysis")
    d2_answer = str(d2_rec.get("answer") or "")

    # --- deterministic check (shared by E1 and E2) ---
    citations = [
        {
            "index": cit["index"],
            "chunk_id": cit["chunk_id"],
            "text": (next((ch.text for ch in chunks if ch.chunk_id == cit["chunk_id"]), "")),
        }
        for cit in (built.citations or [])
    ]
    check = deterministic_citation_check(analysis, citations)

    e2_rec: dict[str, Any] = {
        "question_id": qid,
        "condition": "E2",
        "claims_checked": check["claims_checked"],
        "labels": check["labels"],
        "answer": e2_answer(d2_answer, check, max_idx),
        # E2 executes no generation: its end-to-end latency IS D2's.
        "latency_ms": int(d2_rec.get("latency_ms", 0) or 0),
    }

    e1_rec: dict[str, Any] = {
        "question_id": qid,
        "condition": "E1",
        "claims_checked": check["claims_checked"],
        "labels": dict(check["labels"]),
        # Baseline latency (D2 analysis replay); repair latency is added on top
        # when a repair call is made.
        "latency_ms": int(d2_rec.get("latency_ms", 0) or 0),
    }

    flagged = [r for r in check["results"] if r["label"] == "misattributed"]
    e1_rec["flagged_count"] = len(flagged)
    if analysis is None:
        e1_rec["error"] = "no D2 structured analysis available for this qid"
        return {"e1": e1_rec, "e2": e2_rec, "check": check}

    if not flagged:
        # Nothing to repair: the D2 answer IS the verified answer (0 calls).
        e1_rec["repair_skipped"] = "no misattributed citations flagged"
        e1_rec["answer"] = d2_answer
        e1_rec["repair_call_made"] = False
        return {"e1": e1_rec, "e2": e2_rec, "check": check}

    # --- E1 repair call (budgeted) ---
    analysis_json = json.dumps(analysis, ensure_ascii=False)
    text_by_marker = {f"[{c['index']}]": str(c.get("text") or "") for c in citations}
    flagged_json = json.dumps(
        [
            {
                "claim": f["claim"],
                "marker": f["marker"],
                "checker_reason": f["reason"],
                "cited_chunk_text_excerpt": text_by_marker.get(f["marker"], "")[:600],
            }
            for f in flagged
        ],
        ensure_ascii=False,
    )
    sys_p, user_p = render_repair_prompts(manifest_q[qid]["question"], built.context, analysis_json, flagged_json)

    resp = None
    payload = None
    ok = False
    error = ""
    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        resp = client.call(sys_p, user_p, temperature=LLM_TEMPERATURE, max_tokens=REPAIR_MAX_TOKENS)
        usage = getattr(resp, "usage", None) or {}
        payload_i = extract_json_object(resp.text or "") if not resp.error else None
        ok_i = False
        if payload_i is not None:
            ok_i, why_i = validate_repair_payload(payload_i, manifest_q[qid]["question"])
            if ok_i:
                payload, ok = payload_i, True
                error = ""
            else:
                error = why_i
        else:
            error = resp.error or "unparseable repair JSON"
        # Every generation attempt is logged (design sec 6: exact accounting);
        # only successful ones count toward the cap (enforced client-side).
        _log_call(
            calls_path,
            lock,
            qid=qid,
            condition="E1",
            stage="repair",
            revision_count=attempt - 1,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int(getattr(resp, "latency", 0.0) * 1000),
            success=bool(ok_i and not resp.error),
        )
        if error:
            e1_rec[f"attempt_{attempt}_error"] = error
            e1_rec[f"attempt_{attempt}_output_chars"] = len(resp.text or "")
        if ok:
            break

    if not ok:
        e1_rec["error"] = f"E1 repair failed after {MAX_REPAIR_ATTEMPTS} attempts: {error}"
        e1_rec["repair_call_made"] = True
        e1_rec["latency_ms"] = int(getattr(resp, "latency", 0.0) * 1000) if resp is not None else 0
        return {"e1": e1_rec, "e2": e2_rec, "check": check}

    e1_rec["answer"] = e1_answer_from_payload(payload, check, max_idx)
    e1_rec["repaired_claims"] = payload.get("repaired_claims") or []
    e1_rec["removed_claims"] = payload.get("removed_claims") or []
    e1_rec["kept_claims"] = payload.get("kept_claims") or []
    e1_rec["conclusion_unchanged"] = bool(payload.get("conclusion_unchanged"))
    e1_rec["repair_call_made"] = True
    e1_rec["latency_ms"] = int(d2_rec.get("latency_ms", 0) or 0) + int(getattr(resp, "latency", 0.0) * 1000)
    e1_rec["output_tokens"] = int((getattr(resp, "usage", None) or {}).get("completion_tokens", 0))
    return {"e1": e1_rec, "e2": e2_rec, "check": check}


# --------------------------------------------------------------------------- #
# Phase RUN — resumable, budget-gated (150-call cap shared by nothing else)
# --------------------------------------------------------------------------- #
def _done_set_e(ckpt_path: Path, resume: bool) -> set[str]:
    done: set[str] = set()
    if resume and ckpt_path.exists():
        for r in _load_ckpt(ckpt_path):
            if r.get("error"):
                continue
            if r.get("question_id"):
                done.add(r["question_id"])
    return done


def phase_run_e(stub: bool, resume: bool, limit: int | None, concurrency: int = 1, only: str | None = None) -> int:
    e1_ckpt = E_CKPT_E1_STUB if stub else E_CKPT_E1
    e2_ckpt = E_CKPT_E2_STUB if stub else E_CKPT_E2
    verif_path = E_VERIFICATION_STUB if stub else E_VERIFICATION
    calls_path = E_CALLS_STUB_JSONL if stub else E_CALLS_JSONL
    manifest_q = load_c_questions()
    all_qids = [q for q in sorted(manifest_q) if not manifest_q[q].get("unresolved")]

    # D2 checkpoint (reused verbatim — no reasoning calls in E).
    d2_ckpt_stub = D_D2_CKPT_STUB if stub else D_D2_CKPT
    d2_map = {}
    for r in _load_ckpt(d2_ckpt_stub):
        if not r.get("error") and r.get("answer") and r.get("question_id"):
            d2_map[r["question_id"]] = r
    missing = [q for q in all_qids if q not in d2_map]

    done = _done_set_e(e1_ckpt, resume)
    tasks = [q for q in all_qids if q in d2_map and q not in done]
    if only:
        wanted = {q.strip().upper() for q in only.split(",") if q.strip()}
        unknown = sorted(wanted - set(tasks))
        if unknown:
            print(f"WARNING: --only qids not runnable (no D2 analysis or already done): {unknown}", flush=True)
        tasks = [q for q in tasks if q in wanted]
    if limit:
        tasks = tasks[:limit]

    if not stub and missing:
        print(
            f"WARNING: {len(missing)} qids have no successful D2 analysis and will be skipped: {missing[:10]}{'...' if len(missing) > 10 else ''}",
            flush=True,
        )

    # Budget: E1 needs one call ONLY for questions with flagged citations.
    # The plan is computed from the deterministic check (free), so the cap is
    # enforced BEFORE any generation (design sec 6 STOP rule).
    payload_index = load_payload_index()
    planned_calls = 0
    plans: dict[str, dict] = {}
    for qid in all_qids:
        if qid not in d2_map:
            continue
        chunks, built, max_idx = _build_context(manifest_q, qid, payload_index)
        citations = [
            {
                "index": cit["index"],
                "chunk_id": cit["chunk_id"],
                "text": (next((ch.text for ch in chunks if ch.chunk_id == cit["chunk_id"]), "")),
            }
            for cit in (built.citations or [])
        ]
        check = deterministic_citation_check(d2_map[qid].get("analysis"), citations)
        plans[qid] = check
        if any(r["label"] == "misattributed" for r in check["results"]):
            planned_calls += 1

    already_done_calls = 0
    if resume:
        # Attempts (not successes) seed the client's attempt counter so the
        # cap accounting matches D's semantics (every generation counts).
        already_done_calls = len(_load_ckpt(calls_path))
    else:
        e1_ckpt.write_text("", encoding="utf-8")
        e2_ckpt.write_text("", encoding="utf-8")
        verif_path.write_text("", encoding="utf-8")

    planned_total = already_done_calls + sum(
        1 for q in tasks if any(r["label"] == "misattributed" for r in plans[q]["results"])
    )
    if not stub and planned_total > PLANNED_CALLS_E:
        print(
            f"STOP (design sec 6): E1 would need {planned_total} repair calls but the cap is {PLANNED_CALLS_E}. "
            f"Nothing executed — expected call count reported instead of exceeding the budget.",
            flush=True,
        )
        return BUDGET_STOP_EXIT

    if stub:
        os.environ["RAG_USE_STUB_LLM"] = "true"
        client = _EStubClient()
    else:
        os.environ["RAG_USE_STUB_LLM"] = "false"
        # Reuse D's transport/budget client but with E's cap + counter.
        D_ACTIVE_CAP[0] = PLANNED_CALLS_E
        D_CALL_COUNT[0] = already_done_calls
        client = _DNoRetryClient()
        if getattr(client, "use_stub", False):
            print("FATAL: real LLM key not configured (client in STUB mode). Abort before budget spent.", flush=True)
            return 2

    lock = threading.Lock()
    print("=" * 70, flush=True)
    print(
        f"Phase RUN E {'(STUB validation — 0 real LLM calls)' if stub else f'(REAL, repair-call cap={PLANNED_CALLS_E}, concurrency={concurrency})'}",
        flush=True,
    )
    print(
        f"  questions with D2 analysis: {len(d2_map)} | to run: {len(tasks)} | repair calls planned: {planned_total}",
        flush=True,
    )
    print(
        f"  deterministic check (free): flagged questions so far = {sum(1 for c in plans.values() if any(r['label'] == 'misattributed' for r in c['results']))}",
        flush=True,
    )
    print("=" * 70, flush=True)

    t0 = time.perf_counter()
    completed = err = 0
    total = len(tasks)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {
            ex.submit(run_e_one, qid, manifest_q, payload_index, client, calls_path, lock, d2_map[qid]): qid
            for qid in tasks
        }
        for fut in as_completed(futs):
            qid = futs[fut]
            try:
                out = fut.result()
            except Exception as exc:
                out = {
                    "e1": {"question_id": qid, "condition": "E1", "error": f"task exception: {exc!r}"},
                    "e2": {"question_id": qid, "condition": "E2", "error": f"task exception: {exc!r}"},
                    "check": {},
                }
            if out["e1"].get("error"):
                err += 1
            _append_jsonl_e(e1_ckpt, out["e1"], lock)
            _append_jsonl_e(e2_ckpt, out["e2"], lock)
            _append_jsonl_e(verif_path, {"qid": qid, **out["check"]}, lock)
            completed += 1
            if completed % 10 == 0 or completed == total or completed <= 5:
                print(f"  [E {completed}/{total}] err={err} {time.perf_counter() - t0:.0f}s", flush=True)

    print(
        f"  E RUN done: {completed}/{total} qids, repair-calls={D_CALL_COUNT[0]}/{PLANNED_CALLS_E}, err={err}",
        flush=True,
    )
    return 0


def _append_jsonl_e(path: Path, rec: dict, lock: threading.Lock) -> None:
    with lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


class _EStubClient:
    """Zero-call stub for pipeline validation: echoes the D2 answer as repaired."""

    def call(self, system_prompt: str, user_prompt: str, *, temperature: float, max_tokens: int) -> GroundedLLMResponse:
        payload = {
            "repaired_claims": [],
            "removed_claims": [],
            "conclusion_unchanged": True,
            "final_answer": "Stub repair answer for pipeline validation only. [1]",
        }
        return GroundedLLMResponse(
            text=json.dumps(payload), model="stub", latency=0.01, usage={"prompt_tokens": 0, "completion_tokens": 0}
        )


# --------------------------------------------------------------------------- #
# Phase ANALYZE — same evaluator stack as D (spec: unchanged metrics)
# --------------------------------------------------------------------------- #
def phase_analyze(stub: bool) -> int:

    print("=" * 70, flush=True)
    print("Phase ANALYZE E (no LLM)", flush=True)
    print("=" * 70, flush=True)

    manifest_q = load_c_questions()
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    d2_ckpt = D_D2_CKPT_STUB if stub else D_D2_CKPT
    d2_recs = [r for r in _load_ckpt(d2_ckpt) if not r.get("error") and r.get("answer")]
    e1_recs = [r for r in _load_ckpt(E_CKPT_E1_STUB if stub else E_CKPT_E1) if not r.get("error") and r.get("answer")]
    e2_recs = [r for r in _load_ckpt(E_CKPT_E2_STUB if stub else E_CKPT_E2) if not r.get("error") and r.get("answer")]
    e1_map = {r["question_id"]: r for r in e1_recs}
    e2_map = {r["question_id"]: r for r in e2_recs}

    print(f"  D2 reused: {len(d2_recs)} | E1 ok: {len(e1_recs)} | E2 ok: {len(e2_recs)}", flush=True)

    gold_index = build_gold_index(payload_index, family_map)
    family_cache: dict[str, tuple] = {}
    per_q: dict[str, dict] = {}
    cond_metrics: dict[str, list[dict]] = {"D2": [], "E1": [], "E2": []}

    for qid in sorted(manifest_q):
        entry = manifest_q[qid]
        if entry.get("unresolved") or qid not in questions:
            continue
        q = questions[qid]
        if qid not in family_cache:
            family_cache[qid] = (set(entry["gold_chunk_ids"]), q.recall_units())
        gold_chunk_ids, gold_units = family_cache[qid]
        cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
        chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
        cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
        built = cb.build(entry["question"], chunks, "general_qa")
        context_ids = [cit["chunk_id"] for cit in (built.citations or [])]

        row: dict[str, Any] = {"question_id": qid, "question": entry["question"]}
        for cond, recs_map in (("D2", {r["question_id"]: r for r in d2_recs}), ("E1", e1_map), ("E2", e2_map)):
            rec = recs_map.get(qid)
            if rec is None:
                row[cond] = {"status": "not_run"}
                continue
            cits, grounded = _score_like_service(rec["answer"], chunks, built)
            resp = _DResp(
                rec["answer"],
                citations=cits,
                groundedness_score=grounded,
                total_latency_ms=int(rec.get("latency_ms", 0)),
            )
            m = compute_metrics(
                resp, built, q, cid_list, context_ids, gold_chunk_ids, gold_units, payload_index, family_map
            )
            m.update({
                "question_id": qid,
                "condition": cond,
                "error": None,
                "repair_call_made": rec.get("repair_call_made", False),
                "flagged_count": rec.get("flagged_count", 0),
            })
            cond_metrics[cond].append(m)
            row[cond] = {
                k: m[k]
                for k in (
                    "answer",
                    "answer_correctness",
                    "correct",
                    "citation_recall",
                    "citation_precision",
                    "groundedness",
                    "abstained",
                    "latency_ms",
                    "context_tokens",
                )
            }
            row[cond]["flagged_count"] = rec.get("flagged_count", 0)
            row[cond]["repair_call_made"] = rec.get("repair_call_made", False)
        per_q[qid] = row

    with E_PER_QUESTION.open("w", encoding="utf-8") as f:
        for qid in sorted(per_q):
            f.write(json.dumps({"qid": qid, **per_q[qid]}, ensure_ascii=False, default=str) + "\n")

    def agg(metrics: list[dict], label: str) -> dict:
        if not metrics:
            return {"label": label, "n": 0}
        lat = [m["latency_ms"] for m in metrics]
        return {
            "label": label,
            "n": len(metrics),
            "answer_correctness": round(_mean([m["answer_correctness"] for m in metrics]), 4),
            "correct_rate": round(_mean([m["correct"] for m in metrics]), 4),
            "citation_recall": round(_mean([m["citation_recall"] for m in metrics]), 4),
            "citation_precision": round(_mean([m["citation_precision"] for m in metrics]), 4),
            "groundedness": round(_mean([m["groundedness"] for m in metrics]), 4),
            "abstain_rate": round(_mean([m["abstained"] for m in metrics]), 4),
            "mean_latency_ms": round(_mean(lat), 1),
            "median_latency_ms": round(_pctl(lat, 50), 1),
        }

    aggregates = {
        "D2": agg(cond_metrics["D2"], "D2 Structured Reasoning (reused checkpoint)"),
        "E1": agg(cond_metrics["E1"], "E1 Verification + Constrained Repair"),
        "E2": agg(cond_metrics["E2"], "E2 Verification + Removal-only (0 calls)"),
    }

    # --- targeted-subgroup analysis: D2-correct, D2-incorrect, flagged set ---
    def _correct(cond: str, qid: str):
        v = per_q.get(qid, {}).get(cond, {}).get("correct")
        return v if isinstance(v, bool) else None

    d2_correct = [qid for qid in per_q if _correct("D2", qid) is True]
    d2_incorrect = [qid for qid in per_q if _correct("D2", qid) is False]
    flagged_qs = [qid for qid in per_q if (per_q[qid].get("E1", {}) or {}).get("flagged_count", 0) > 0]

    repair_regressions = [qid for qid in d2_correct if _correct("E1", qid) is False]
    repair_gains = [qid for qid in d2_incorrect if _correct("E1", qid) is True]
    removal_regressions = [qid for qid in d2_correct if _correct("E2", qid) is False]
    removal_gains = [qid for qid in d2_incorrect if _correct("E2", qid) is True]

    targeted = {"n_flagged_questions": len(flagged_qs)}
    for cond in ("E1", "E2"):
        targeted[f"{cond}_improved_on_flagged"] = sum(
            1 for qid in flagged_qs if _correct("D2", qid) is False and _correct(cond, qid) is True
        )
        targeted[f"{cond}_regressed_on_flagged"] = sum(
            1 for qid in flagged_qs if _correct("D2", qid) is True and _correct(cond, qid) is False
        )

    aggregates["repair_safety"] = {
        "d2_correct_n": len(d2_correct),
        "E1_repair_regressions": len(repair_regressions),
        "E1_repair_regressions_qids": repair_regressions,
        "E1_repair_gains_qids": repair_gains,
        "E2_removal_regressions": len(removal_regressions),
        "E2_removal_gains_qids": removal_gains,
        **targeted,
    }

    # --- fine-grained taxonomy re-cluster on E1/E2 answers (D sec-0 patterns) ---
    PATTERNS = {
        "scope_jurisdiction_misread": r"does not apply|outside|scope|jurisdiction|extraterritorial|not cover(ed)? by",
        "wrong_provision_section_cited": r"wrong section|incorrect section|wrong provision|incorrect provision|should (be|cite|refer)|correct section is|correct provision is",
        "missing_condition": r"condition.{0,20}(not|miss|overlook)|fail(ed)? to (identify|consider|address).*condition|omits",
        "missing_exception_proviso": r"exception|proviso|provided that|subject to",
        "definition_misapplied": r"defin(e|ition|ed).{0,40}(incorrect|wrong|misappl|narrow|broad)|misdefin",
        "fact_condition_mapping_error": r"fact.{0,30}not (establish|support|satisf)|mapping|satisfaction of",
        "unsupported_claim": r"not (supported|found|contained) (by|in) (the )?(evidence|context|provided)|no evidence|unsupported|not mentioned in",
        "prohibition_obligation_confusion": r"prohibit|mandatory|obligation|permission|shall not|may not",
        "hierarchy_conflict": r"conflict|hierarchy|override|prevail|later in force|amend",
        "overqualification_hedging": r"however|uncertain|cannot be conclusively|it appears|may (vary|depend)",
    }
    tax: dict[str, dict] = {}
    for cond in ("D2", "E1", "E2"):
        c = Counter()
        for qid in per_q:
            ans = per_q[qid].get(cond, {}).get("answer")
            if isinstance(ans, str):
                # structure-based counts (proxy-free, from the checkpoint data)
                for name, pat in PATTERNS.items():
                    if re.search(pat, ans.lower()):
                        c[name] += 1
        tax[cond] = dict(c)
    with E_ERROR_TAX.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "note": "answer-surface pattern counts (coarse proxy); claim-level labels in experiment_E_verification.jsonl",
                "answer_surface_patterns": tax,
            },
            f,
            indent=2,
        )

    # --- transitions D2 -> E1 / E2 ---
    trans = {"D2_to_E1": Counter(), "D2_to_E2": Counter()}
    for qid in per_q:
        for key, cond in (("D2_to_E1", "E1"), ("D2_to_E2", "E2")):
            a, b = _correct("D2", qid), _correct(cond, qid)
            if a is None or b is None:
                trans[key]["not_run"] += 1
            elif b and not a:
                trans[key]["improved"] += 1
            elif a and not b:
                trans[key]["worsened"] += 1
            else:
                trans[key]["unchanged"] += 1
    with E_TRANSITIONS.open("w", encoding="utf-8") as f:
        json.dump({k: dict(v) for k, v in trans.items()}, f, indent=2)

    results = {
        "experiment": "E — Evidence-Binding / Citation Verification",
        "design_doc": "Experiment_E_Citation_Verification_Design.md",
        "conditions": {
            "D2": "reused Experiment D checkpoint (0 new calls)",
            "E1": "deterministic check -> 1 constrained repair call -> repaired answer",
            "E2": "deterministic check -> remove misattributed markers -> D2 answer (0 calls)",
        },
        "config": {
            "model": LLM_MODEL,
            "temperature": LLM_TEMPERATURE,
            "repair_max_tokens": REPAIR_MAX_TOKENS,
            "repair_call_cap": PLANNED_CALLS_E,
            "prompts": "D2 reasoning/answer reused byte-identical; one fixed REPAIR prompt (design sec 5)",
            "metrics": "experiment_b_topk_eval.compute_metrics (unchanged)",
        },
        "aggregate": aggregates,
        "n_per_question": len(per_q),
        "stub_validation": stub,
    }
    with E_RESULTS.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    # Call accounting (design sec 6/8)
    call_recs = _load_ckpt(calls_path_e(stub))
    succ = [r for r in call_recs if r.get("success")]
    acc = {
        "experiment": "E citation verification",
        "hard_budget_total_new_generations": PLANNED_CALLS_E,
        "planned_calls": {
            "E1_repair": sum(1 for q in per_q if (per_q[q].get("E1", {}) or {}).get("flagged_count", 0) > 0),
            "total": PLANNED_CALLS_E,
        },
        "actual_generations": len(call_recs),
        "successful_calls": len(succ),
        "failed_calls": len(call_recs) - len(succ),
        "repair_calls_skipped_no_flags": sum(
            1 for r in _load_ckpt(E_CKPT_E1_STUB if stub else E_CKPT_E1) if r.get("repair_skipped")
        ),
        "reused_experiment_D_calls": {
            "D2": len(d2_recs),
            "note": "D2 checkpoint reused verbatim; 0 new reasoning calls",
        },
        "total_token_usage": {
            "input_tokens": sum(int(r.get("input_tokens", 0)) for r in call_recs),
            "output_tokens": sum(int(r.get("output_tokens", 0)) for r in call_recs),
        },
        "stub_validation": stub,
        "budget_respected": len(succ) <= PLANNED_CALLS_E,
    }
    with E_ACCOUNTING.open("w", encoding="utf-8") as f:
        json.dump(acc, f, indent=2)

    print(
        f"  per-question: {E_PER_QUESTION.name} | results: {E_RESULTS.name} | accounting: {E_ACCOUNTING.name}",
        flush=True,
    )
    print(f"  D2={aggregates['D2'].get('n')} E1={aggregates['E1'].get('n')} E2={aggregates['E2'].get('n')}", flush=True)
    return 0


def calls_path_e(stub: bool) -> Path:
    return E_CALLS_STUB_JSONL if stub else E_CALLS_JSONL


# --------------------------------------------------------------------------- #
# Phase PLOTS
# --------------------------------------------------------------------------- #
def phase_plots(stub: bool) -> int:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("plots skipped: matplotlib unavailable", flush=True)
        return 0

    res = json.load(open(E_RESULTS, encoding="utf-8"))
    agg = res["aggregate"]
    conds = [c for c in ("D2", "E1", "E2") if agg.get(c, {}).get("n")]

    # 1) correctness comparison
    fig, ax = plt.subplots(figsize=(9, 5))
    metrics = ["answer_correctness", "correct_rate", "citation_recall", "citation_precision", "groundedness"]
    width = 0.8 / max(1, len(conds))
    import numpy as np

    x = np.arange(len(metrics))
    for i, cond in enumerate(conds):
        vals = [agg[cond].get(k, 0) or 0 for k in metrics]
        ax.bar(x + i * width, vals, width, label=f"{cond} (n={agg[cond]['n']})")
    ax.set_xticks(x + width)
    ax.set_xticklabels(["soft", "binary", "citR", "citP", "grounded"])
    ax.set_ylim(0, 1)
    ax.set_title("Experiment E — correctness comparison")
    ax.legend()
    fig.tight_layout()
    fig.savefig(E_PLOT_DIR / "experiment_E_correctness_comparison.png", dpi=140)
    plt.close(fig)

    # 2) repair safety / effect
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    rs = agg.get("repair_safety", {})
    labels = ["gains", "regressions"]
    e1_vals = [len(rs.get("E1_repair_gains_qids", [])), rs.get("E1_repair_regressions", 0)]
    e2_vals = [len(rs.get("E2_removal_gains_qids", [])), rs.get("E2_removal_regressions", 0)]
    x = np.arange(2)
    axes[0].bar(x - 0.15, e1_vals, 0.3, label="E1 repair")
    axes[0].bar(x + 0.15, e2_vals, 0.3, label="E2 removal")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels)
    axes[0].set_title("Repair/removal effect on D2 answers")
    axes[0].legend()
    flagged = rs.get("n_flagged_questions", 0)
    axes[1].bar(["improved", "regressed"], [rs.get("E1_improved_on_flagged", 0), rs.get("E1_regressed_on_flagged", 0)])
    axes[1].set_title(f"E1 on {flagged} flagged questions")
    fig.tight_layout()
    fig.savefig(E_PLOT_DIR / "experiment_E_repair_effect.png", dpi=140)
    plt.close(fig)
    print(f"  plots -> {E_PLOT_DIR}", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# Phase SUMMARY
# --------------------------------------------------------------------------- #
def phase_summary(stub: bool) -> int:
    res = json.load(open(E_RESULTS, encoding="utf-8"))
    acc = json.load(open(E_ACCOUNTING, encoding="utf-8"))
    trans = json.load(open(E_TRANSITIONS, encoding="utf-8"))
    agg = res["aggregate"]
    rs = agg.get("repair_safety", {})

    lines = [
        "# Experiment E — Evidence-Binding / Citation Verification",
        "",
        f"Stub validation: **{res.get('stub_validation')}**",
        "",
        "## Headline (design sec 8)",
        "",
        "| System | n | Soft Correctness | Binary Correct | Cit R | Cit P | Groundedness | Abstain | Median latency |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cond in ("D2", "E1", "E2"):
        a = agg.get(cond, {})
        if not a.get("n"):
            continue
        lines.append(
            f"| {cond} | {a['n']} | {a['answer_correctness']} | {a['correct_rate']} | {a['citation_recall']} | "
            f"{a['citation_precision']} | {a['groundedness']} | {a['abstain_rate']} | {a.get('median_latency_ms', '-')} |"
        )
    lines += [
        "",
        "## Repair safety (design sec 8 — must be ~0)",
        f"- D2-correct answers: {rs.get('d2_correct_n')} | E1 repair regressions: **{rs.get('E1_repair_regressions', 0)}** | E1 gains: {len(rs.get('E1_repair_gains_qids', []))}",
        f"- E2 removal regressions: **{rs.get('E2_removal_regressions', 0)}** | E2 gains: {len(rs.get('E2_removal_gains_qids', []))}",
        f"- Flagged questions: {rs.get('n_flagged_questions')} | E1 improved on flagged: {rs.get('E1_improved_on_flagged', 0)} | E1 regressed on flagged: {rs.get('E1_regressed_on_flagged', 0)}",
        "",
        "## Transitions",
        f"- D2 -> E1: {trans.get('D2_to_E1')}",
        f"- D2 -> E2: {trans.get('D2_to_E2')}",
        "",
        "## Call accounting (design sec 6)",
        f"- Cap: {acc['hard_budget_total_new_generations']} | successful: {acc['successful_calls']} | failed attempts: {acc['failed_calls']} | budget respected: {acc['budget_respected']}",
        f"- Tokens: in={acc['total_token_usage']['input_tokens']} out={acc['total_token_usage']['output_tokens']}",
        f"- Repair calls skipped (no flags): {acc.get('repair_calls_skipped_no_flags')}",
        "",
        "## Decision rule (design sec 10)",
        "- E1 > D2 meaningfully + E2 < E1 -> integrate verification+repair.",
        "- E2 ~ E1 -> integrate the deterministic checker only (0 production calls).",
        "- Neither -> misattribution detectable but not repairable; next target: modality confusion via representation, not agents.",
        "",
    ]
    E_SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print(f"  summary -> {E_SUMMARY}", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Experiment E — evidence-binding / citation verification")
    p.add_argument("--phase", required=True, choices=["run", "analyze", "plots", "summary", "stub-smoke"])
    p.add_argument("--limit", type=int, default=None, help="run only the first N pending questions (smoke tests)")
    p.add_argument("--resume", action="store_true", help="resume from checkpoints (skip successful qids)")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--stub", action="store_true", help="stub-LLM pipeline validation (0 real calls)")
    p.add_argument("--only", type=str, default=None, help="comma-separated qids to run (subset smoke tests)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.phase == "run":
        return phase_run_e(
            stub=args.stub, resume=args.resume, limit=args.limit, concurrency=args.concurrency, only=args.only
        )
    if args.phase == "analyze":
        return phase_analyze(stub=args.stub)
    if args.phase == "plots":
        return phase_plots(stub=args.stub)
    if args.phase == "summary":
        return phase_summary(stub=args.stub)
    if args.phase == "stub-smoke":
        rc = phase_run_e(stub=True, resume=False, limit=args.limit)
        if rc:
            return rc
        rc = phase_analyze(stub=True)
        if rc:
            return rc
        rc = phase_plots(stub=True)
        if rc:
            return rc
        return phase_summary(stub=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
