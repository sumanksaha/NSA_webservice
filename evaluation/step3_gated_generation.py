"""Step 3 — Budgeted gated generation (plan sec 5.2 Step 3 + sec 5.3).

Runs the two generative interventions over the Step-1 assigned residual qids,
always behind the registered gates. ``reference_narrow`` (35) is NEVER queued
(the only budget rule it has: a generation call spent chasing it rejects the
run). Scorer is frozen: ``token_overlap`` + evaluator_v2 overlay, imported
unchanged; ``rescore_evaluator_v2``-equivalent scoring runs inline (0 LLM).

Branches
--------
``evidence_missing`` (41 qids)
  Precondition (zero calls, checked before any spend):
    - target sections = gold-unit sections ∪ sections named in question/reference;
    - every gold-unit section must be present in the *index* -> else
      ``rejected/section_not_in_index_stop_do_not_loop_retrieval`` (gate reject_if);
    - rebuild the payload: target chunks (index-resident, absent from the frozen
      O3 context) prepended to the frozen O3 ids -> "text actually added";
    - if payload would not change -> ``not_run/no_new_text_added`` (budget rule:
      spend only on ids whose text was actually added);
    - build the context (same ``_COracleContextBuilder(2000, MAX_CTX_CHARS)`` D2
      used) and require every target section to survive into the built context
      -> else ``rejected/gold_span_still_absent_from_payload`` (zero calls).
  Spend: ONE answer call on the new payload (evidence-only prompt, verbatim
  ``cited_span`` required) -> frozen score -> registered gate
  (``evaluate_evidence_missing``) -> Step-2 safety net.

``model_wrong`` (48 qids)
  Precondition (zero calls): cut contrastive options from the frozen O3 context
  (verbatim sentences carrying section id + competent authority + operative
  language) -> ``not_run/no_contrastive_span`` if nothing cuttable.
  Spend: ONE contrastive call (pick governing option, rewrite remedy to match,
  quote chosen sentence verbatim) -> registered gate (``evaluate_model_wrong``:
  span verbatim in context / not a section-number-only swap / operative sentence
  changed / no abstain-on-absent-element) -> Step-2 safety net.

Budget (registered): cap 150 generations. Accounting is deliberately
conservative and stricter than plan sec 5.2's "cap successful generations at
150": EVERY response received from the transport counts against the cap —
parseable or not — so total real calls can never exceed 150; transport failures
stay OUTSIDE the cap; <=3 attempts per qid; ledger is persisted after every
mutation. The report publishes this accounting so the budget claim matches the
plan wording rather than silently redefining "successful".

Statuses (per qid, exhaustive; report publishes per-label counts BEFORE any
aggregate soft-score claim, plan sec 5.2 Step 3):
  recovered  gate kept AND binary 0->1
  rejected   registered-gate reject or Step-2 safety reject (D2 stands);
             includes zero-call gate rejects (section_not_in_index, gold span absent)
  unchanged  kept but binary did not flip (answer changed, score flat)
  not_run    no candidate produced: budget_exhausted / transport_failure /
             parse_failure / no_new_text_added / no_contrastive_span

Reference-anchored fill (human-in-the-loop; plan sec 5.2 "add the missing
instrument text")
  For EM ids whose gold-unit sections are ALREADY in the frozen O3 payload the
  mechanical target derivation finds nothing to add (the human label says the
  *reference-supporting* text — usually a different section — is absent).
  For those, propose in-family chunks that are textually distinct from the
  frozen payload and whose reference overlap beats the best frozen chunk by
  > REF_FILL_MARGIN. Each candidate then runs the multi-stage reference-quality
  and legal-aware gate (``step3_fill_quality``) producing REJECT|REVIEW|PASS
  plus machine-readable reason codes; the proposals NEVER spend a call on their
  own: they are written to step0-style worksheet artifacts
  (step3_em_fill_review.{json,md}, full diagnostics in
  step3_em_gate_report.{json,md} via --gate-report) and a qid becomes eligible
  only after explicit approval (--approve-fills / --decisions-from-csv ->
  step3_em_fill_approved.json). A gate REJECT can never be approved (the CLI
  refuses); PASS/REVIEW still require explicit human approval. Unapproved
  proposals report as not_run/fill_review_pending; gate-REJECT as
  fill_rejected_by_human or fill_rejected_by_gate; zero calls in all cases.

Modes
-----
  --preflight   offline eligibility plan (0 calls) -> step3_run_plan.{json,md}
  --review-fills   write EM reference-fill worksheet (0 calls) ->
                step3_em_fill_review.{json,md}
  --approve-fills QIDS / --approve-fills-all
                approve proposed fills -> step3_em_fill_approved.json
  --run         execute the queued branches, checkpointed/resumable
  --stub        full pipeline with deterministic canned JSON (0 calls),
                writes *_stub artifacts
  --report      rebuild step3_report.{json,md} from the checkpoint
  --require-registered   exit 2 unless Step 0/1/2 registrations validate

Outputs (evaluation/out/ceiling_v5/):
  step3_run_plan.{json,md}        step3_calls.jsonl
  step3_em_fill_review.{json,md}  step3_em_fill_approved.json
  step3_candidates.jsonl          step3_budget_ledger.json
  step3_report.{json,md}          (+ *_stub variants for --stub)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import warnings
from collections import Counter
from dataclasses import dataclass, field
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

from evaluation.step0_label_residual import (
    load_c_manifest,
    load_payload_index,
    load_questions,
)
from evaluation.abstention_rule import abstain_credit
from evaluation.experiment_b_topk_eval import (
    abstain_check,
    to_retrieved_chunk,
    token_overlap,
)
from evaluation.experiment_c_oracle_eval import (
    MAX_CTX_CHARS,
    _COracleContextBuilder,
)
from evaluation.experiment_d_reasoning_eval import extract_json_object
from evaluation.resolution import FamilyMap, payload_to_keys
from evaluation.step1_preregister_gates import (
    BUDGET_CAP,
    evaluate_candidate,
    quote_in_evidence,
)
from evaluation.step2_safety_properties import (
    SAFETY_JSON,
    conclusion_retained,
    validate_payload as validate_safety_payload,
)
from evaluation.step3_fill_quality import (
    PASS as GATE_PASS,
    REJECT as GATE_REJECT,
    gate_fill_candidate,
    gate_fill_proposal,
)

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"
PLAN_REF = "Experiment_D_E_F_Comprehensive_Adversarial_Evaluation_and_Improvements.md §5.2/§5.3"
SCORER = (
    "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay"
)

RUN_PLAN_JSON = OUT / "step3_run_plan.json"
RUN_PLAN_MD = OUT / "step3_run_plan.md"
FILL_REVIEW_JSON = OUT / "step3_em_fill_review.json"
FILL_REVIEW_MD = OUT / "step3_em_fill_review.md"
FILL_APPROVAL_JSON = OUT / "step3_em_fill_approved.json"
GATE_REPORT_JSON = OUT / "step3_em_gate_report.json"
GATE_REPORT_MD = OUT / "step3_em_gate_report.md"
CALLS_JSONL = OUT / "step3_calls.jsonl"
CANDIDATES_JSONL = OUT / "step3_candidates.jsonl"
LEDGER_JSON = OUT / "step3_budget_ledger.json"
REPORT_JSON = OUT / "step3_report.json"
REPORT_MD = OUT / "step3_report.md"

OVERLAY_PATH = ROOT / "evaluation" / "evaluator_v2_overlay.json"
D2_PER_QUESTION = OUT / "experiment_D_per_question.jsonl"
PREREG_JSON = OUT / "step1_preregistered_gates.json"

MAX_ATTEMPTS = 3  # per qid (F convention); transport failures stay outside cap
MAX_TOKENS = 8192  # E-lesson: 4096 truncated repair JSONs
LLM_TEMPERATURE = 0.1  # frozen eval temperature
OPTIONS_MAX = 6
SENT_MIN_CHARS = 40
SENT_MAX_CHARS = 600
INSTRUMENT_ADD_CAP = 60  # whole-instrument fill: bounded addition (never flood)
# Reference-anchored fill (requires human approval — see module docstring)
REF_FILL_MARGIN = 0.05  # candidate must beat best frozen ref-overlap by this
REF_FILL_MIN_OVERLAP = 0.25  # and clear this absolute floor
REF_FILL_K = 8  # chunks added per approved qid (bounded)
RN_LABEL = "reference_narrow"
STATUS_ORDER = ("recovered", "rejected", "unchanged", "not_run")
RN_LARGE_SHARE = 0.25  # sec 5.3 rule 3: reference_narrow "large share of sample"

OPERATIVE_RE = re.compile(
    r"\b(shall|must|may not|must not|no person|every person|prohibited|prohibition|"
    r"liable|penalty|required to|entitled|empowered|duty|authority may|is required)\b",
    re.IGNORECASE,
)
_SECTION_REF_RE = re.compile(r"\b(?:sections?|sec\.?|s\.)\s*\d+[A-Za-z]?(?:\(\w+\))?", re.IGNORECASE)
_SECTION_MENTION_RE = re.compile(r"\b(?:sections?|sec\.?|s)\.?\s*(\d+[A-Za-z]?)", re.IGNORECASE)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# --------------------------------------------------------------------------- #
# Prompts (fixed text for this run; the SCORER and GATES are the frozen parts)
# --------------------------------------------------------------------------- #

_EM_SYSTEM = (
    "You are a careful Indian legal assistant working ONLY from supplied "
    "evidence. A previous analysis of this question missed relevant statutory "
    "text; the evidence below has been updated to include it. Answer the "
    "QUESTION from the evidence. Output ONLY one JSON object with keys: "
    '"answer" (string, complete answer citing sources with [n] markers); '
    '"cited_span" (string: copy VERBATIM, character-for-character, one sentence '
    "from the evidence sources that states the operative rule your answer relies "
    'on — it must appear exactly in the evidence); "operative_rule" (string); '
    '"authority" (string); "remedy" (string); "missing_element_if_any" (string '
    "or null: set to a precise statutory element ONLY if genuinely absent from "
    "the evidence, and then make answer a short abstention naming it). "
    "Never invent provisions, numbers, or rules that are not in the evidence."
)

_MW_SYSTEM = (
    "You are a careful Indian legal assistant working ONLY from supplied "
    "evidence. A previous answer to the question applied the wrong operative "
    "rule. Below are candidate operative sentences cut VERBATIM from the "
    "evidence, each with an option id, its section id, and its competent "
    "authority. Choose the ONE option whose sentence GOVERNS the question, then "
    "rewrite your answer so its operative rule, authority, and remedy match that "
    "chosen sentence. Your answer MUST quote the chosen option's sentence "
    "verbatim. Output ONLY one JSON object with keys: "
    '"chosen_option_id" (integer); "answer" (string, complete rewritten answer '
    'citing sources with [n] markers); "cited_span" (string: copy the chosen '
    'option\'s sentence VERBATIM); "operative_rule" (string); "authority" '
    '(string); "remedy" (string); "missing_element_if_any" (string or null). '
    "If no statutory element needed to answer is genuinely absent, set "
    "missing_element_if_any to null. Never invent provisions or citations."
)

_EM_USER = (
    "QUESTION:\n{question}\n\n"
    "EVIDENCE SOURCES (use ONLY these; [n] = source n):\n{context}\n\n"
    "Answer the QUESTION from the evidence. Output ONLY the required JSON object."
)

_MW_USER = (
    "QUESTION:\n{question}\n\n"
    "EVIDENCE SOURCES (use ONLY these; [n] = source n):\n{context}\n\n"
    "PREVIOUS ANSWER (a reviewer judged it to apply the wrong rule):\n{d2}\n\n"
    "CANDIDATE OPERATIVE SENTENCES (options cut verbatim from the evidence):\n"
    "{options}\n\n"
    "Pick the option that GOVERNS the question and rewrite your answer to match "
    "it, quoting the chosen sentence verbatim. Output ONLY the required JSON "
    "object."
)


# --------------------------------------------------------------------------- #
# Frozen scoring (identical semantics to experiment_f score_v2 / rescore v2)
# --------------------------------------------------------------------------- #

def load_overlay() -> tuple[dict, set[str]]:
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    banned = set((overlay.get("banned_abstain_markers") or {}).get("markers") or [])
    return dict(overlay.get("widened_conclusions") or {}), banned


def score_frozen(
    answer: str,
    refs: list[str],
    banned: set[str],
    insufficient_evidence: bool = False,
) -> dict[str, Any]:
    """Max token-overlap over references; abstain with overlay-banned markers removed.

    ``insufficient_evidence`` adds the evaluator-v2 abstention credit
    (``abstention_rule.abstain_credit``) so before/after scoring uses the same
    rule the published v2 metric uses. No queued qid is currently IE (verified
    by preflight), so today this changes nothing — it exists so the two sides
    can never silently diverge if the queue changes.
    """
    toks = [token_overlap(answer or "", r) for r in refs if r]
    best = max((t["correctness"] for t in toks), default=0.0)
    low = (answer or "").lower()
    for m in banned:
        low = low.replace(m, " ")
    return {
        "soft": round(best, 4),
        "correct": bool(best > 0.5)
        or abstain_credit(answer or "", banned, insufficient_evidence),
        "abstained": bool(abstain_check(low)),
    }


def refs_for(qid: str, questions: dict, widened: dict) -> list[str]:
    q = questions.get(qid)
    refs = [str(getattr(q, "acceptable_conclusion", "") or "")]
    w = widened.get(qid)
    if isinstance(w, dict) and w.get("add"):
        refs.append(str(w["add"]))
    elif isinstance(w, str) and w:
        refs.append(w)
    return refs


def load_d2_answers() -> dict[str, str]:
    out: dict[str, str] = {}
    if not D2_PER_QUESTION.exists():
        return out
    with D2_PER_QUESTION.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ans = ((r.get("D2") or {}).get("answer")) or None
            if ans and r.get("qid"):
                out[r["qid"]] = ans
    return out


# --------------------------------------------------------------------------- #
# Dependencies (injectable for tests; load_deps() wires the real artifacts)
# --------------------------------------------------------------------------- #

@dataclass
class Deps:
    questions: dict
    payload: dict
    manifest: dict  # manifest["questions"][qid]["conditions"]["O3_full_support"]...
    d2_answers: dict[str, str]
    widened: dict
    banned: set[str]
    fam_map: Any
    fam_sec_index: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    fam_chunks: dict[str, set[str]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def o3_ids(self, qid: str) -> list[str]:
        entry = (self.manifest.get(qid) or {})
        o3 = (entry.get("conditions") or {}).get("O3_full_support") or {}
        return list(o3.get("context_chunk_ids") or [])


def build_family_indexes(payload: dict, fam_map: Any) -> tuple[dict, dict]:
    fam_sec: dict[str, dict[str, set[str]]] = {}
    fam_chunks: dict[str, set[str]] = {}
    for cid, pl in payload.items():
        keys = payload_to_keys(pl, fam_map)
        for fam, sec in keys:
            fam_chunks.setdefault(fam, set()).add(cid)
            if sec is not None:
                fam_sec.setdefault(fam, {}).setdefault(str(sec), set()).add(cid)
    return fam_sec, fam_chunks


def load_deps(*, with_labels: bool = True) -> Deps:
    questions = load_questions()
    payload = load_payload_index()
    manifest = load_c_manifest() or {}
    fam_map = FamilyMap()
    fam_sec, fam_chunks = build_family_indexes(payload, fam_map)
    widened, banned = load_overlay()
    labels: dict[str, str] = {}
    if with_labels:
        labels_path = OUT / "step0_residual_labels.json"
        if labels_path.exists():
            labels = json.loads(labels_path.read_text(encoding="utf-8")).get("labels") or {}
    return Deps(
        questions=questions,
        payload=payload,
        manifest=manifest.get("questions") or manifest,
        d2_answers=load_d2_answers(),
        widened=widened,
        banned=banned,
        fam_map=fam_map,
        fam_sec_index=fam_sec,
        fam_chunks=fam_chunks,
        labels=labels,
    )


# --------------------------------------------------------------------------- #
# Target derivation (evidence_missing)
# --------------------------------------------------------------------------- #

def _units_of(q: Any) -> list[Any]:
    try:
        return list(q.recall_units())
    except Exception:
        return []


def section_mentions(text: str) -> set[str]:
    return {m for m in _SECTION_MENTION_RE.findall(str(text or "")) if m}


def derive_targets(q: Any) -> dict[str, Any]:
    """Target sections for one question: gold units (authoritative) + question/reference mentions."""
    units = _units_of(q)
    families = sorted({str(u.family) for u in units})
    unit_pairs = {
        (str(u.family), str(u.section))
        for u in units
        if getattr(u, "section", None) is not None
    }
    instrument_families = sorted({str(u.family) for u in units if getattr(u, "section", None) is None})
    mention_secs = section_mentions(getattr(q, "question", "")) | section_mentions(
        getattr(q, "acceptable_conclusion", "")
    )
    return {
        "families": families,
        "unit_pairs": unit_pairs,  # (family, section) — required strictly
        "instrument_families": instrument_families,  # family-level units
        "mention_secs": mention_secs,  # attributed to any family, best effort
    }


def _ordered_target_chunks(
    pairs: set[tuple[str, str]], deps: Deps
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for fam, sec in sorted(pairs):
        for cid in sorted(deps.fam_sec_index.get(fam, {}).get(sec, ())):
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out


def load_fill_decisions() -> dict[str, str]:
    """qid -> 'approved' | 'rejected' from the human fill-decision artifact."""
    if not FILL_APPROVAL_JSON.exists():
        return {}
    try:
        data = json.loads(FILL_APPROVAL_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {str(q): "approved" for q in (data.get("approved_qids") or [])}
    for q in data.get("rejected_qids") or []:
        out[str(q)] = "rejected"
    return out


def load_fill_approval() -> set[str]:
    """Qids whose reference-anchored fill proposal the human approved."""
    return {q for q, d in load_fill_decisions().items() if d == "approved"}


def find_ref_fill(qid: str, deps: Deps) -> dict[str, Any]:
    """Reference-anchored fill proposal for one EM qid (0 calls, never spends).

    The human label says the operative text backing the *reference* is absent
    from the frozen O3 payload, while the gold-unit sections are present (so the
    mechanical target derivation found nothing to add). Search the qid's own
    families for chunks that are (a) not in the frozen payload, (b) not a textual
    duplicate of a frozen chunk, (c) clear REF_FILL_MIN_OVERLAP against the
    (possibly widened) reference, and (d) beat the best frozen chunk's reference
    overlap by > REF_FILL_MARGIN. Top REF_FILL_K are proposed.

    Each candidate then runs the multi-stage reference-quality and legal-aware
    gate (``step3_fill_quality``): metadata/OCR/heading/fragment filtering,
    legal-identifier consistency, stopword-free semantic relevance, and the
    retained margin — producing per-candidate REJECT/REVIEW/PASS plus machine
    reason codes and a proposal-level summary under ``gate``. A proposal whose
    gate is REJECT is never eligible (approval is refused); PASS/REVIEW still
    require explicit human approval. The raw margin alone never decides.
    """
    out: dict[str, Any] = {
        "qid": qid,
        "label": "evidence_missing",
        "margin": REF_FILL_MARGIN,
        "min_overlap": REF_FILL_MIN_OVERLAP,
        "candidates": [],
    }
    q = deps.questions.get(qid)
    if q is None:
        return out
    t = derive_targets(q)
    frozen = set(deps.o3_ids(qid))
    ref = " ".join(refs_for(qid, deps.questions, deps.widened))
    if not ref.strip() or not frozen:
        return out
    frozen_texts = {
        _norm_ws(str((deps.payload.get(c) or {}).get("chunk_text") or "")) for c in frozen
    }
    best_frozen = 0.0
    for c in frozen:
        txt = str((deps.payload.get(c) or {}).get("chunk_text") or "")
        best_frozen = max(best_frozen, token_overlap(ref, txt)["correctness"])
    out["best_frozen_overlap"] = round(best_frozen, 4)
    threshold = max(REF_FILL_MIN_OVERLAP, best_frozen + REF_FILL_MARGIN)

    pool: set[str] = set()
    for fam in t["families"]:
        pool |= deps.fam_chunks.get(fam, set())
    pool -= frozen
    scored: list[tuple[float, str, str]] = []
    for cid in sorted(pool):
        pl = deps.payload.get(cid) or {}
        txt = str(pl.get("chunk_text") or pl.get("text") or "")
        nt = _norm_ws(txt)
        if not nt or nt in frozen_texts:
            continue
        ov = token_overlap(ref, txt)["correctness"]
        if ov >= threshold:
            scored.append((ov, cid, txt))
    scored.sort(key=lambda x: (-x[0], x[1]))
    question = str(getattr(q, "question", "") or "")
    seen: set[str] = set()
    gates: list[dict[str, Any]] = []
    for ov, cid, txt in scored:
        nt = _norm_ws(txt)
        if nt in seen:
            continue  # near-duplicate boilerplate: propose the best copy only
        seen.add(nt)
        pl = deps.payload.get(cid) or {}
        # multi-stage gate: quality/metadata/OCR + legal identifiers +
        # semantic relevance + the retained margin (step3_fill_quality)
        g = gate_fill_candidate(
            txt,
            question=question,
            frozen_reference=ref,
            raw_overlap=ov,
            best_frozen_overlap=best_frozen,
            fam_map=deps.fam_map,
            payload=pl,
            question_families=t["families"],
            payload_families=[fam for fam, _ in payload_to_keys(pl, deps.fam_map)],
        )
        gates.append(g)
        out["candidates"].append(
            {
                "chunk_id": cid,
                "section": str(pl.get("section_number") or pl.get("clause_number") or ""),
                "overlap": round(ov, 4),
                "snippet": re.sub(r"\s+", " ", txt).strip()[:240],
                "gate": g,
            }
        )
        if len(out["candidates"]) >= REF_FILL_K:
            break
    out["gate"] = gate_fill_proposal(gates)
    return out


def em_precondition(qid: str, deps: Deps) -> dict[str, Any]:
    """Zero-call eligibility for one evidence_missing qid.

    Returns a record with ``verdict`` in
    {eligible, rejected, not_run} plus the reason and, when eligible, the
    rebuilt payload id list and context pieces.
    """
    q = deps.questions.get(qid)
    if q is None:
        return {"verdict": "not_run", "reason": "question_missing"}
    t = derive_targets(q)
    frozen = deps.o3_ids(qid)
    frozen_set = set(frozen)

    # 1. Index presence: every gold-unit section must be in the index.
    missing_unit = [
        (f, s)
        for (f, s) in sorted(t["unit_pairs"])
        if not deps.fam_sec_index.get(f, {}).get(s)
    ]
    # Instrument-level units: family must exist in the index at all.
    missing_instr = [
        f for f in t["instrument_families"] if not deps.fam_chunks.get(f)
    ]
    # Question/reference mentions: best-effort attribution to a qid family.
    mention_hits: dict[str, set[str]] = {}
    for sec in sorted(t["mention_secs"]):
        have = {f for f in t["families"] if deps.fam_sec_index.get(f, {}).get(sec)}
        if have:
            mention_hits[sec] = have
    families_present = bool(t["families"]) and any(
        deps.fam_chunks.get(f) for f in t["families"]
    )
    section_in_index = families_present and not missing_unit and not missing_instr

    rec: dict[str, Any] = {
        "qid": qid,
        "label": "evidence_missing",
        "targets": {
            "families": t["families"],
            "unit_pairs": sorted("/".join(p) for p in t["unit_pairs"]),
            "mention_secs": sorted(t["mention_secs"]),
            "mention_hits": {s: sorted(v) for s, v in mention_hits.items()},
        },
        "section_in_index": section_in_index,
        "missing_unit_pairs": [f"{f}/{s}" for f, s in missing_unit],
        "missing_instrument_families": missing_instr,
        "n_frozen_o3": len(frozen),
    }

    if not section_in_index:
        rec.update(
            verdict="rejected",
            reason="section_not_in_index_stop_do_not_loop_retrieval",
            note="instrument/section absent from index — continue ingestion (sec 5.3 rule 1)",
        )
        return rec

    # 2. What text would actually be added to the payload?
    needed_pairs = set(t["unit_pairs"]) | {
        (f, sec)
        for sec, fams in mention_hits.items()
        for f in fams
    }
    target_ids = _ordered_target_chunks(needed_pairs, deps)
    # Instrument-level units (no section): only fill when the instrument is
    # wholly absent from the frozen payload (bounded — never flood the context).
    instrument_added_note = None
    for fam in t["instrument_families"]:
        in_o3 = deps.fam_chunks.get(fam, set()) & frozen_set
        if in_o3:
            continue  # instrument already represented in the payload
        fresh = [
            cid
            for cid in sorted(deps.fam_chunks.get(fam, ()))
            if cid not in frozen_set and cid not in target_ids
        ][:INSTRUMENT_ADD_CAP]
        target_ids.extend(fresh)
        if fresh:
            instrument_added_note = f"{fam}: +{len(fresh)} chunks (capped at {INSTRUMENT_ADD_CAP})"
    added = [c for c in target_ids if c not in frozen_set]
    if instrument_added_note:
        rec["instrument_fill"] = instrument_added_note
    rec["n_target_chunks"] = len(target_ids)
    rec["n_added_chunks"] = len(added)
    rec["added_chunk_ids_sample"] = added[:8]

    if not added:
        # Human premise check: the reference-supporting text (usually a section
        # other than the gold units) may be missing from the payload while the
        # gold sections are present. Propose it — but never spend without the
        # human's explicit approval (fill_review workflow).
        fill = find_ref_fill(qid, deps)
        if not fill["candidates"]:
            rec.update(
                verdict="not_run",
                reason="no_new_text_added",
                note="target text already in the frozen O3 payload and no distinct "
                "reference-anchoring chunk found — re-audit label",
            )
            return rec
        rec["proposed_fill"] = fill
        gate = fill.get("gate") or {}
        gate_decision = gate.get("decision")
        gate_codes = gate.get("reason_codes") or []
        rec["fill_gate"] = {
            "decision": gate_decision,
            "reason_codes": gate_codes,
            "n": gate.get("n", 0),
            "n_pass": gate.get("n_pass", 0),
            "n_review": gate.get("n_review", 0),
            "n_reject": gate.get("n_reject", 0),
        }
        decision = load_fill_decisions().get(qid)
        if decision == "rejected":
            rec.update(
                verdict="not_run",
                reason="fill_rejected_by_human",
                note="human rejected the reference-anchored fill proposal "
                "(lexical/metadata artifact) — re-audit label; zero calls",
            )
            return rec
        # Machine REJECT is absolute: never eligible, even if the decisions
        # file somehow records an approval (the CLI refuses such approvals).
        if gate_decision == GATE_REJECT:
            rec.update(
                verdict="not_run",
                reason="fill_rejected_by_gate",
                note="multi-stage quality gate REJECT "
                f"({', '.join(gate_codes[:6])}) — zero calls",
            )
            return rec
        if decision is None:
            rec.update(
                verdict="not_run",
                reason="fill_review_pending",
                note=f"{len(fill['candidates'])} reference-anchored chunk(s) proposed "
                f"(gate={gate_decision}) — approve via --review-fills / "
                "--approve-fills to make this qid eligible",
            )
            return rec
        # Approved -> only a gate PASS may generate (plan sec 11: "Generation
        # must occur only for PASS items after explicit approval"). A human
        # approval of a REVIEW/REJECT-gate proposal is recorded but never
        # generates: REVIEW means the machine could not establish relevance.
        usable = [
            c
            for c in fill["candidates"]
            if (c.get("gate") or {}).get("decision") == GATE_PASS
        ]
        if gate_decision != GATE_PASS or not usable:
            rec.update(
                verdict="not_run",
                reason="fill_gate_not_pass",
                note=f"human approval recorded, but gate={gate_decision} — "
                "only PASS proposals may generate (plan sec 11); zero calls",
            )
            return rec
        target_ids = [c["chunk_id"] for c in usable]
        added = list(target_ids)
        rec["fill_approved"] = True
        rec["fill_gate_promotion"] = "gate_pass"
        rec["n_usable_candidates"] = len(usable)
        rec["n_target_chunks"] = len(target_ids)
        rec["n_added_chunks"] = len(added)
        rec["added_chunk_ids_sample"] = added[:8]

    # 3. Rebuild payload (added text first so it survives char truncation) and
    #    build the same context D2 used; the added text must survive into it.
    new_ids = list(dict.fromkeys(added + frozen))
    chunks = [
        to_retrieved_chunk(cid, deps.payload.get(cid, {}), 1.0)
        for cid in new_ids
        if cid in deps.payload
    ]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(str(getattr(q, "question", "")), chunks, "general_qa")
    cited_ids = {c.get("chunk_id") for c in (built.citations or []) if c.get("chunk_id")}

    def _covered(pair: tuple[str, str]) -> bool:
        fam, sec = pair
        for cid in cited_ids:
            for f, s in payload_to_keys(deps.payload.get(cid, {}), deps.fam_map):
                if f == fam and str(s) == sec:
                    return True
        return False

    required = set(t["unit_pairs"]) | set(needed_pairs)
    uncovered = sorted(f"{f}/{s}" for (f, s) in required if not _covered((f, s)))
    rec["context_chunk_count"] = len(cited_ids)
    rec["uncovered_targets"] = uncovered

    if uncovered:
        rec.update(
            verdict="rejected",
            reason="gold_span_still_absent_from_payload",
            note="target text in index but did not survive into the rebuilt context",
        )
        return rec

    rec.update(
        verdict="eligible",
        reason="text_added_to_payload",
        gold_span_in_payload=True,
        new_payload_ids=new_ids,
        context=built.context,
        context_chunk_count=len(cited_ids),
    )
    return rec


# --------------------------------------------------------------------------- #
# Contrastive option cutting (model_wrong)
# --------------------------------------------------------------------------- #

def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+", str(text or ""))
    out = []
    for p in parts:
        p = p.strip()
        if SENT_MIN_CHARS <= len(p) <= SENT_MAX_CHARS:
            out.append(p)
    return out


def cut_contrastive_options(
    qid: str,
    deps: Deps,
    *,
    options_max: int = OPTIONS_MAX,
) -> list[dict[str, Any]]:
    """Options cut VERBATIM from the frozen O3 context: section id + authority + operative sentence."""
    q = deps.questions.get(qid)
    question = str(getattr(q, "question", "") or "")
    q_toks = set(re.findall(r"[a-z0-9]+", question.lower()))
    cands: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cid in deps.o3_ids(qid):
        pl = deps.payload.get(cid) or {}
        text = str(pl.get("chunk_text") or pl.get("text") or "")
        section = pl.get("section_number") or pl.get("clause_number") or ""
        authority = pl.get("authority") or pl.get("document_title") or ""
        for sent in _sentences(text):
            key = _norm_ws(sent)
            if not key or key in seen:
                continue
            seen.add(key)
            score = (2 if OPERATIVE_RE.search(sent) else 0) + token_overlap(
                question, sent
            )["correctness"]
            cands.append(
                {
                    "chunk_id": cid,
                    "section": str(section),
                    "authority": str(authority),
                    "sentence": sent,
                    "score": round(score, 4),
                }
            )
    cands.sort(key=lambda c: (-c["score"], c["chunk_id"]))
    out = cands[: max(options_max, 1)]
    for i, opt in enumerate(out, start=1):
        opt["option_id"] = i
    return out


# --------------------------------------------------------------------------- #
# Field derivation for the registered gates + Step 2 safety
# --------------------------------------------------------------------------- #

def strip_section_refs(s: str) -> str:
    return _SECTION_REF_RE.sub(" § ", str(s or ""))


def section_number_only_swap(d2: str, cand: str) -> bool:
    """True when the candidate differs from D2 only in section numbers (gate reject_if)."""
    if not cand or not d2:
        return False
    a, b = _norm_ws(strip_section_refs(d2)), _norm_ws(strip_section_refs(cand))
    if a and b and a == b:
        return True
    return token_overlap(a, b)["correctness"] >= 0.97


def derive_mw_fields(
    *,
    model: dict,
    options: list[dict],
    context: str,
    d2_answer: str,
    candidate: str,
) -> dict[str, Any]:
    """Fields for evaluate_candidate('model_wrong', ...) — pure, testable."""
    model_cited = str(model.get("cited_span") or "")
    raw_id = model.get("chosen_option_id")
    try:
        chosen_id = int(raw_id)
    except (TypeError, ValueError):
        chosen_id = -1
    chosen = next((o for o in options if o.get("option_id") == chosen_id), None)
    chosen_span = str(chosen["sentence"]) if chosen else ""

    missing_elem = str(model.get("missing_element_if_any") or "")
    element_in_ctx = bool(missing_elem) and _norm_ws(missing_elem) in _norm_ws(context)
    abstains_absent = bool(abstain_check(candidate or "")) and not element_in_ctx

    if chosen_span:
        direct = quote_in_evidence(chosen_span, candidate)
        ov_c = token_overlap(candidate or "", chosen_span)["correctness"]
        ov_d = token_overlap(d2_answer or "", chosen_span)["correctness"]
        operative_changed = direct or (ov_c >= 0.6 and ov_c > ov_d + 0.05)
    else:
        operative_changed = False

    return {
        "cited_span_in_context": quote_in_evidence(model_cited, context),
        "section_number_only_swap": section_number_only_swap(d2_answer, candidate),
        "operative_sentence_changed": bool(operative_changed),
        "abstains_on_element_absent_from_payload": bool(abstains_absent),
        # provenance for the report (not gate inputs)
        "_chosen_option_id": chosen_id,
        "_chosen_valid": chosen is not None,
        "_model_cited": model_cited,
    }


def derive_step2_fields(
    *,
    d2_answer: str,
    candidate: str,
    binary_before: bool,
    binary_after: bool,
    cited_span: str,
    context: str,
    step1_kept: bool,
) -> dict[str, Any]:
    """Step-2 safety record for one candidate.

    ``is_full_answer_replacement`` is the *unconstrained* condition of sec 5.3
    rule 4: a conclusion replacement that fails quote-in-evidence. A replacement
    anchored by a verbatim in-context quote is the registered gated rewrite
    (property 4 governs it), not an open-critic replacement.
    """
    quote_ok = bool(cited_span) and quote_in_evidence(cited_span, context)
    retained = conclusion_retained(d2_answer, candidate)
    conclusion_changed = bool(candidate) and not retained
    return {
        "already_correct": bool(binary_before),
        "candidate_correct": bool(binary_after),
        "is_rewrite": True,
        "cited_span": cited_span,
        "context": context,
        "is_open_critic": False,  # single registered gated call, never a critic
        "is_full_answer_replacement": bool(conclusion_changed and not quote_ok),
        "no_rewrite_sentence_deleted": False,
        "conclusion_fields_changed": (
            ["operative_rule", "authority", "remedy"] if conclusion_changed else []
        ),
        "new_subsection_quoted_from_evidence": quote_ok,
        "frozen_checker_accepts": bool(step1_kept),
    }


def derive_status(
    *,
    step1_keep: bool,
    step1_reason: str,
    step2_pass: bool | None,
    step2_reason: str | None,
    binary_before: bool,
    binary_after: bool,
    not_run: str | None = None,
) -> tuple[str, str]:
    if not_run:
        return "not_run", not_run
    if not step1_keep:
        return "rejected", f"gate:{step1_reason}"
    if step2_pass is False:
        return "rejected", f"safety:{step2_reason}"
    if binary_after and not binary_before:
        return "recovered", "binary_rose_0_to_1"
    if binary_after and binary_before:
        return "unchanged", "already_correct_before"
    return "unchanged", "kept_no_binary_flip"


# --------------------------------------------------------------------------- #
# Budget ledger (persisted; transport failures outside cap; RN spends zero)
# --------------------------------------------------------------------------- #

class Ledger:
    def __init__(self, path: Path, cap: int = BUDGET_CAP, *, stub: bool = False):
        self.path = path
        self.cap = cap
        self.stub = stub
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {
            "cap": cap,
            "spent_total": 0,
            "spent_by_label": {},
            "transport_failures": 0,
            "attempts": 0,
            "by_qid": {},
        }
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text(encoding="utf-8")))
                self.data["cap"] = cap  # cap is registered, never taken from disk
            except Exception:
                pass

    @property
    def spent(self) -> int:
        return int(self.data.get("spent_total", 0))

    def can_spend(self) -> bool:
        return self.spent < self.cap

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def record_attempt(self) -> None:
        with self._lock:
            self.data["attempts"] = int(self.data.get("attempts", 0)) + 1
            self._save()

    def record_transport_failure(self, qid: str) -> None:
        """Outside the cap (registered budget rule)."""
        with self._lock:
            self.data["transport_failures"] = int(self.data.get("transport_failures", 0)) + 1
            self.data.setdefault("by_qid", {}).setdefault(qid, {})
            self.data["by_qid"][qid]["transport_failures"] = (
                int(self.data["by_qid"][qid].get("transport_failures", 0)) + 1
            )
            self._save()

    def spend(self, qid: str, label: str) -> bool:
        """Count one received response against the cap. Returns False at cap."""
        with self._lock:
            if label == RN_LABEL:
                raise RuntimeError(
                    f"budget violation: generation call attempted on {RN_LABEL} qid {qid}"
                )
            if int(self.data.get("spent_total", 0)) >= self.cap:
                return False
            self.data["spent_total"] = int(self.data.get("spent_total", 0)) + 1
            self.data.setdefault("spent_by_label", {})
            self.data["spent_by_label"][label] = (
                int(self.data["spent_by_label"].get(label, 0)) + 1
            )
            self.data.setdefault("by_qid", {}).setdefault(qid, {})
            self.data["by_qid"][qid]["spent"] = int(self.data["by_qid"][qid].get("spent", 0)) + 1
            self._save()
            return True


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #

class _StubClient:
    """Deterministic canned JSON for --stub (0 real calls, full pipeline)."""

    def call(self, system_prompt: str, user_prompt: str, *, temperature: float, max_tokens: int):
        import types

        text = json.dumps(
            {
                "answer": (
                    "Based on the provided evidence, the operative provision is "
                    "reflected in this stub answer [1]."
                ),
                "chosen_option_id": 1,
                "cited_span": "",
                "operative_rule": "stub rule",
                "authority": "stub authority",
                "remedy": "stub remedy",
                "missing_element_if_any": None,
            }
        )
        return types.SimpleNamespace(
            text=text,
            error=None,
            usage={"prompt_tokens": 100, "completion_tokens": 60, "total_tokens": 160},
            latency=0.001,
        )


def make_client(*, stub: bool):
    if stub:
        return _StubClient()
    os.environ["RAG_USE_STUB_LLM"] = "false"
    from evaluation.eval_e2e_v2 import _SSLBypassLLMClient

    return _SSLBypassLLMClient()


# --------------------------------------------------------------------------- #
# Queue + registrations
# --------------------------------------------------------------------------- #

def load_queue(deps: Deps) -> dict[str, list[str]]:
    """EM + MW qids from the registered assignment. RN is never queued."""
    path = OUT / "step1_qid_assignment.json"
    if not path.exists():
        raise RuntimeError("step1_qid_assignment.json missing — run step1 --assign-qids first")
    assignment = json.loads(path.read_text(encoding="utf-8"))
    buckets = assignment.get("buckets") or {}
    em = list(buckets.get("evidence_missing", {}).get("qids") or [])
    mw = list(buckets.get("model_wrong", {}).get("qids") or [])
    rn = set(buckets.get(RN_LABEL, {}).get("qids") or [])
    overlap = (set(em) | set(mw)) & rn
    if overlap:
        raise RuntimeError(f"reference_narrow qids queued for generation: {sorted(overlap)}")
    return {"evidence_missing": sorted(em), "model_wrong": sorted(mw)}


def check_registrations() -> dict[str, Any]:
    """Step 0 complete + Step 1 prereg valid + Step 2 registered."""
    checks: dict[str, Any] = {}
    labels_path = OUT / "step0_residual_labels.json"
    labels_ok = False
    if labels_path.exists():
        labels_ok = bool(json.loads(labels_path.read_text(encoding="utf-8")).get("step0_complete"))
    checks["step0_complete"] = labels_ok

    prereg_ok = False
    if PREREG_JSON.exists():
        from evaluation.step1_preregister_gates import validate_prereg

        payload = json.loads(PREREG_JSON.read_text(encoding="utf-8"))
        prereg_ok = bool(validate_prereg(payload).get("ok")) and payload.get(
            "qid_assignment"
        ) == "assigned"
    checks["step1_registered"] = prereg_ok

    safety_ok = False
    if SAFETY_JSON.exists():
        safety_ok = bool(
            validate_safety_payload(json.loads(SAFETY_JSON.read_text(encoding="utf-8"))).get("ok")
        )
    checks["step2_registered"] = safety_ok

    checks["ok"] = labels_ok and prereg_ok and safety_ok
    return checks


# --------------------------------------------------------------------------- #
# Preflight (0 calls)
# --------------------------------------------------------------------------- #

def preflight(deps: Deps) -> dict[str, Any]:
    queue = load_queue(deps)
    records: list[dict] = []
    for qid in queue["evidence_missing"]:
        records.append(em_precondition(qid, deps))
    for qid in queue["model_wrong"]:
        opts = cut_contrastive_options(qid, deps)
        if opts:
            records.append(
                {
                    "qid": qid,
                    "label": "model_wrong",
                    "verdict": "eligible",
                    "reason": "contrastive_options_cut",
                    "n_options": len(opts),
                    "options_preview": [o["sentence"][:80] for o in opts[:3]],
                }
            )
        else:
            records.append(
                {
                    "qid": qid,
                    "label": "model_wrong",
                    "verdict": "not_run",
                    "reason": "no_contrastive_span",
                    "n_options": 0,
                }
            )

    by_label: dict[str, dict[str, int]] = {}
    for r in records:
        d = by_label.setdefault(r["label"], {})
        d[r["verdict"]] = d.get(r["verdict"], 0) + 1

    # Never persist full contexts / id lists into the plan artifact: run_one_*
    # recomputes them per qid via em_precondition().
    for r in records:
        r.pop("context", None)
        r.pop("new_payload_ids", None)

    n_eligible = sum(1 for r in records if r["verdict"] == "eligible")
    plan = {
        "experiment": "Step 3 — preflight run plan (0 calls)",
        "plan_ref": PLAN_REF,
        "generated_at": now(),
        "scorer": SCORER,
        "budget_cap": BUDGET_CAP,
        "max_attempts_per_qid": MAX_ATTEMPTS,
        "queue": {k: len(v) for k, v in queue.items()},
        "reference_narrow_queued": 0,
        "n_eligible_for_generation": n_eligible,
        "projected_spend": n_eligible,  # 1 call per eligible qid
        "within_budget": n_eligible <= BUDGET_CAP,
        "verdicts_by_label": by_label,
        "records": records,
        "registrations": check_registrations(),
    }
    return plan


def render_plan_md(plan: dict) -> str:
    lines = [
        "# Step 3 — preflight run plan (0 model calls)",
        "",
        f"**Plan:** {plan['plan_ref']}",
        f"**Generated:** {plan['generated_at']}",
        f"**Scorer:** `{plan['scorer']}`",
        f"**Budget cap (successful generations):** {plan['budget_cap']} — "
        f"projected spend **{plan['projected_spend']}** "
        f"(within budget: {plan['within_budget']})",
        f"**reference_narrow queued for generation:** {plan['reference_narrow_queued']} (must be 0)",
        "",
        "## Registrations",
        "",
    ]
    for k, v in plan["registrations"].items():
        lines.append(f"- {k}: **{v}**")
    lines += ["", "## Verdicts", "", "| Label | Verdict | N |", "|---|---|---:|"]
    for label, counts in plan["verdicts_by_label"].items():
        for verdict in sorted(counts):
            lines.append(f"| `{label}` | {verdict} | {counts[verdict]} |")
    lines += ["", "## Zero-call rejects / not-runs", ""]
    for r in plan["records"]:
        if r["verdict"] != "eligible":
            extra = ""
            if r.get("reason") == "fill_review_pending":
                n = len((r.get("proposed_fill") or {}).get("candidates") or [])
                gate = (r.get("fill_gate") or {}).get("decision")
                extra = f" — {n} chunk(s) proposed (gate={gate}), awaiting --approve-fills"
            elif r.get("reason") in ("fill_rejected_by_gate", "fill_gate_not_pass"):
                codes = (r.get("fill_gate") or {}).get("reason_codes") or []
                extra = f" — {', '.join(codes[:5])}"
            lines.append(
                f"- **{r['qid']}** (`{r['label']}`) — {r['verdict']}: {r.get('reason')}{extra}"
            )
    lines += ["", "## Eligible qids", ""]
    for r in plan["records"]:
        if r["verdict"] == "eligible":
            extra = (
                f" (+{r.get('n_added_chunks')} chunks)"
                if r["label"] == "evidence_missing"
                else f" ({r.get('n_options')} options)"
            )
            lines.append(f"- **{r['qid']}** (`{r['label']}`){extra}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def _log_call(path: Path, rec: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _valid_candidate(model: dict | None, label: str) -> bool:
    if not isinstance(model, dict):
        return False
    ans = model.get("answer")
    if not isinstance(ans, str) or not ans.strip():
        return False
    if not isinstance(model.get("cited_span", ""), str):
        return False
    if label == "model_wrong":
        return model.get("chosen_option_id") is not None
    return True


def _call_model(
    *,
    client: Any,
    system: str,
    user: str,
    qid: str,
    label: str,
    ledger: Ledger,
    calls_path: Path,
) -> tuple[dict | None, str | None]:
    """One logical generation (<=MAX_ATTEMPTS transport attempts).

    Returns (model_payload, not_run_reason). A received response spends budget
    even if unparseable; transport failures never do.
    """
    last_error = "transport_failure"
    for _ in range(MAX_ATTEMPTS):
        if not ledger.can_spend():
            return None, "budget_exhausted"
        ledger.record_attempt()
        resp = client.call(system, user, temperature=LLM_TEMPERATURE, max_tokens=MAX_TOKENS)
        if getattr(resp, "error", None):
            last_error = "transport_failure"
            ledger.record_transport_failure(qid)
            _log_call(
                calls_path,
                {
                    "qid": qid,
                    "label": label,
                    "at": now(),
                    "success": False,
                    "transport_failure": True,
                    "error": str(resp.error)[:300],
                },
            )
            continue
        if not ledger.spend(qid, label):  # received -> counts against cap
            return None, "budget_exhausted"
        usage = getattr(resp, "usage", None) or {}
        model = extract_json_object(getattr(resp, "text", "") or "")
        ok = _valid_candidate(model, label)
        _log_call(
            calls_path,
            {
                "qid": qid,
                "label": label,
                "at": now(),
                "success": ok,
                "transport_failure": False,
                "parse_ok": model is not None,
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
                "latency_ms": int(float(getattr(resp, "latency", 0.0) or 0.0) * 1000),
                "response_chars": len(getattr(resp, "text", "") or ""),
            },
        )
        if ok:
            return model, None
        last_error = "parse_failure"
    return None, last_error


def _base_record(qid: str, label: str) -> dict[str, Any]:
    return {
        "qid": qid,
        "label": label,
        "at": now(),
        "model": None,  # filled by caller
        "status": None,
        "reason": None,
        "binary_before": None,
        "binary_after": None,
        "soft_before": None,
        "soft_after": None,
        "step1": None,
        "step2": None,
        "generation_calls": 0,
    }



def run_one_em(
    qid: str,
    deps: Deps,
    client: Any,
    ledger: Ledger,
    calls_path: Path,
) -> dict[str, Any]:
    rec = _base_record(qid, "evidence_missing")
    d2 = deps.d2_answers.get(qid) or ""
    refs = refs_for(qid, deps.questions, deps.widened)
    ie = bool(getattr(deps.questions.get(qid), "insufficient_evidence", False))
    before = score_frozen(d2, refs, deps.banned, ie)
    rec["binary_before"] = before["correct"]
    rec["soft_before"] = before["soft"]

    pre = em_precondition(qid, deps)
    rec["precondition"] = {k: v for k, v in pre.items() if k not in ("context", "new_payload_ids")}
    if pre["verdict"] == "rejected":
        g = evaluate_candidate(
            "evidence_missing",
            {
                "gold_span_in_payload": False,
                "section_in_index": bool(pre.get("section_in_index")),
                "binary_before": before["correct"],
                "binary_after": False,
            },
        )
        rec.update(step1=g, status="rejected", reason=f"gate:{g['reason']}")
        return rec
    if pre["verdict"] != "eligible":
        rec.update(status="not_run", reason=pre.get("reason") or "not_run")
        return rec

    q = deps.questions[qid]
    user = _EM_USER.format(question=str(q.question), context=pre["context"])
    model, not_run = _call_model(
        client=client,
        system=_EM_SYSTEM,
        user=user,
        qid=qid,
        label="evidence_missing",
        ledger=ledger,
        calls_path=calls_path,
    )
    rec["generation_calls"] = 1 if model is not None else 0
    if model is None:
        rec.update(status="not_run", reason=not_run or "transport_failure")
        return rec

    candidate = str(model.get("answer") or "")
    after = score_frozen(candidate, refs, deps.banned, ie)
    rec.update(
        binary_after=after["correct"],
        soft_after=after["soft"],
        candidate_abstained=after["abstained"],
        cited_span=str(model.get("cited_span") or ""),
        missing_element=model.get("missing_element_if_any"),
    )

    g = evaluate_candidate(
        "evidence_missing",
        {
            "gold_span_in_payload": True,
            "section_in_index": True,
            "binary_before": before["correct"],
            "binary_after": after["correct"],
        },
    )
    rec["step1"] = g
    s2 = None
    step2_pass: bool | None = None
    if g.get("keep"):
        s2_fields = derive_step2_fields(
            d2_answer=d2,
            candidate=candidate,
            binary_before=before["correct"],
            binary_after=after["correct"],
            cited_span=str(model.get("cited_span") or ""),
            context=pre["context"],
            step1_kept=True,
        )
        from evaluation.step2_safety_properties import evaluate_safety

        s2 = evaluate_safety(s2_fields)
        s2["fields"] = s2_fields
        rec["step2"] = s2
        step2_pass = bool(s2.get("pass"))

    status, reason = derive_status(
        step1_keep=bool(g.get("keep")),
        step1_reason=str(g.get("reason")),
        step2_pass=step2_pass,
        step2_reason=(s2 or {}).get("reason"),
        binary_before=before["correct"],
        binary_after=after["correct"],
    )
    rec.update(status=status, reason=reason)
    return rec


def run_one_mw(
    qid: str,
    deps: Deps,
    client: Any,
    ledger: Ledger,
    calls_path: Path,
) -> dict[str, Any]:
    rec = _base_record(qid, "model_wrong")
    d2 = deps.d2_answers.get(qid) or ""
    refs = refs_for(qid, deps.questions, deps.widened)
    ie = bool(getattr(deps.questions.get(qid), "insufficient_evidence", False))
    before = score_frozen(d2, refs, deps.banned, ie)
    rec["binary_before"] = before["correct"]
    rec["soft_before"] = before["soft"]

    options = cut_contrastive_options(qid, deps)
    if not options:
        rec.update(status="not_run", reason="no_contrastive_span")
        return rec
    rec["n_options"] = len(options)

    q = deps.questions[qid]
    entry = deps.manifest.get(qid) or {}
    frozen_ids = deps.o3_ids(qid)
    chunks = [
        to_retrieved_chunk(cid, deps.payload.get(cid, {}), 1.0)
        for cid in frozen_ids
        if cid in deps.payload
    ]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(str(entry.get("question") or q.question), chunks, "general_qa")
    context = built.context
    rec["context_chunk_count"] = len(built.citations or [])

    options_block = "\n".join(
        f"[{o['option_id']}] section {o['section'] or '—'} — authority: "
        f"{o['authority'] or '—'}\n    {o['sentence']}"
        for o in options
    )
    user = _MW_USER.format(
        question=str(entry.get("question") or q.question),
        context=context,
        d2=d2,
        options=options_block,
    )
    model, not_run = _call_model(
        client=client,
        system=_MW_SYSTEM,
        user=user,
        qid=qid,
        label="model_wrong",
        ledger=ledger,
        calls_path=calls_path,
    )
    rec["generation_calls"] = 1 if model is not None else 0
    if model is None:
        rec.update(status="not_run", reason=not_run or "transport_failure")
        return rec

    candidate = str(model.get("answer") or "")
    after = score_frozen(candidate, refs, deps.banned, ie)
    rec.update(
        binary_after=after["correct"],
        soft_after=after["soft"],
        candidate_abstained=after["abstained"],
        missing_element=model.get("missing_element_if_any"),
    )

    fields = derive_mw_fields(
        model=model,
        options=options,
        context=context,
        d2_answer=d2,
        candidate=candidate,
    )
    gate_fields = {k: v for k, v in fields.items() if not k.startswith("_")}
    rec["mw_derivation"] = {k: v for k, v in fields.items() if k.startswith("_")}
    g = evaluate_candidate("model_wrong", gate_fields)
    rec["step1"] = g
    rec["step1_fields"] = gate_fields

    s2 = None
    step2_pass: bool | None = None
    if g.get("keep"):
        s2_fields = derive_step2_fields(
            d2_answer=d2,
            candidate=candidate,
            binary_before=before["correct"],
            binary_after=after["correct"],
            cited_span=str(model.get("cited_span") or ""),
            context=context,
            step1_kept=True,
        )
        from evaluation.step2_safety_properties import evaluate_safety

        s2 = evaluate_safety(s2_fields)
        s2["fields"] = s2_fields
        rec["step2"] = s2
        step2_pass = bool(s2.get("pass"))

    status, reason = derive_status(
        step1_keep=bool(g.get("keep")),
        step1_reason=str(g.get("reason")),
        step2_pass=step2_pass,
        step2_reason=(s2 or {}).get("reason"),
        binary_before=before["correct"],
        binary_after=after["correct"],
    )
    rec.update(status=status, reason=reason)
    return rec


# --------------------------------------------------------------------------- #
# Execute (checkpointed, resumable)
# --------------------------------------------------------------------------- #

def load_checkpoint(path: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("qid") and r.get("status"):
                done[r["qid"]] = r  # last record wins
    return done


def execute(
    *,
    deps: Deps,
    client: Any,
    ledger: Ledger,
    plan: dict,
    out_jsonl: Path,
    calls_path: Path,
    limit: int | None = None,
    qids: list[str] | None = None,
    fresh: bool = False,
    model_name: str = "poolside/laguna-s-2.1:free",
) -> dict[str, Any]:
    if fresh and out_jsonl.exists():
        out_jsonl.unlink()
    raw_done = load_checkpoint(out_jsonl)
    # Placeholder records folded for un-approved fill proposals are NOT done:
    # once the human approves, the same qid must become eligible on resume.
    # fill_gate_not_pass / fill_rejected_by_* ARE terminal (approval or gate
    # verdict recorded) and count as done.
    done = {
        k: v
        for k, v in raw_done.items()
        if v.get("reason") != "fill_review_pending"
    }
    eligible = [r for r in plan["records"] if r["verdict"] == "eligible"]
    if qids:
        want = set(qids)
        eligible = [r for r in eligible if r["qid"] in want]
    pending = [r for r in eligible if r["qid"] not in done]
    if limit is not None:
        pending = pending[:limit]

    processed: list[dict] = []
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("a", encoding="utf-8") as fh:
        for r in pending:
            qid, label = r["qid"], r["label"]
            if label == "evidence_missing":
                rec = run_one_em(qid, deps, client, ledger, calls_path)
            else:
                rec = run_one_mw(qid, deps, client, ledger, calls_path)
            rec["model"] = model_name
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            processed.append(rec)
            print(
                f"  {qid} [{label}] -> {rec['status']}: {rec['reason']} "
                f"(spent {ledger.spent}/{ledger.cap})",
                flush=True,
            )

    # Fold zero-call preflight rejects/not-runs into the checkpoint so the
    # report covers ALL queued qids (report tabulates from here).
    with out_jsonl.open("a", encoding="utf-8") as fh:
        for r in plan["records"]:
            if r["qid"] in raw_done or r["verdict"] == "eligible":
                continue
            if r["qid"] in {p["qid"] for p in processed}:
                continue
            rec = _base_record(r["qid"], r["label"])
            rec["model"] = None
            rec["preflight"] = {k: v for k, v in r.items() if k != "context"}
            if r["verdict"] == "rejected":
                rec.update(status="rejected", reason=f"gate:{r.get('reason')}")
            else:
                rec.update(status="not_run", reason=r.get("reason") or "not_run")
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            processed.append(rec)

    return {
        "n_pending": len(pending),
        "n_processed": len(processed),
        "spent": ledger.spent,
        "cap": ledger.cap,
        "transport_failures": ledger.data.get("transport_failures", 0),
        "records": processed,
    }


# --------------------------------------------------------------------------- #
# Report (per-label counts FIRST, then aggregates — plan sec 5.2 Step 3)
# --------------------------------------------------------------------------- #

def build_report(checkpoint: dict[str, dict], deps: Deps) -> dict[str, Any]:
    labels = deps.labels or {}
    rn_qids = set()
    assign_path = OUT / "step1_qid_assignment.json"
    if assign_path.exists():
        rn_qids = set(
            (json.loads(assign_path.read_text(encoding="utf-8")).get("buckets") or {})
            .get(RN_LABEL, {})
            .get("qids")
            or []
        )

    counts: dict[str, dict[str, int]] = {}
    reasons: dict[str, Counter] = {}
    safety_fail: Counter = Counter()
    gate_fail: Counter = Counter()
    soft_by_label: dict[str, dict[str, list[float]]] = {}
    unconstrained_kept = 0
    regressed = 0

    for qid, rec in checkpoint.items():
        label = rec.get("label") or labels.get(qid) or "unknown"
        status = rec.get("status") or "not_run"
        counts.setdefault(label, {s: 0 for s in STATUS_ORDER})
        counts[label][status] = counts[label].get(status, 0) + 1
        reasons.setdefault(label, Counter())[str(rec.get("reason"))] += 1
        if rec.get("step1") and not rec["step1"].get("keep"):
            gate_fail[str(rec["step1"].get("reason"))] += 1
        s2 = rec.get("step2") or {}
        if s2 and not s2.get("pass"):
            safety_fail[str(s2.get("failed_property") or s2.get("reason"))] += 1
        if s2.get("pass") and (s2.get("fields") or {}).get("is_full_answer_replacement"):
            unconstrained_kept += 1
        if rec.get("binary_before") and rec.get("binary_after") is False:
            regressed += 1
        if rec.get("soft_before") is not None:
            d = soft_by_label.setdefault(label, {"before": [], "after": []})
            d["before"].append(float(rec["soft_before"]))
            if rec.get("soft_after") is not None:
                d["after"].append(float(rec["soft_after"]))

    # counts are finalized here — only AFTER this point may soft scores appear.
    soft_section: dict[str, dict[str, Any]] = {}
    for label, d in soft_by_label.items():
        b, a = d["before"], d["after"]
        soft_section[label] = {
            "n": len(b),
            "soft_before_mean": round(sum(b) / len(b), 4) if b else 0.0,
            "soft_after_mean": round(sum(a) / len(a), 4) if a else 0.0,
        }

    def _bands(vals: list[float]) -> dict[str, int]:
        """Near-threshold mass: binary claims on answers just under 0.5 are fragile."""
        edges = ((0.45, 0.50), (0.40, 0.45), (0.35, 0.40))
        out = {f"[{x:.2f},{y:.2f})": 0 for x, y in edges}
        out["<0.35"] = 0
        for v in vals:
            for (x, y), k in zip(edges, out, strict=False):
                if x <= v < y:
                    out[k] += 1
                    break
            else:
                if v < 0.35:
                    out["<0.35"] += 1
        return out

    threshold_bands = {
        "note": "soft-score bands; counts near 0.50 sit within one phrasing change "
        "of a binary flip — read binary deltas next to these",
        "before": _bands([v for d in soft_by_label.values() for v in d["before"]]),
        "after": _bands([v for d in soft_by_label.values() for v in d["after"]]),
    }

    n_rn = len(rn_qids)
    n_tabulated = sum(sum(c.values()) for c in counts.values())
    # Registered sample + queue: assignment buckets are loaded once here.
    queued_path = OUT / "step1_qid_assignment.json"
    buckets: dict = {}
    if queued_path.exists():
        buckets = json.loads(queued_path.read_text(encoding="utf-8")).get("buckets") or {}

    # Share is measured against the REGISTERED residual sample (step0 labels,
    # 124 qids; assignment buckets as fixture fallback) — never against
    # whatever has been tabulated so far, otherwise a 1-record run reports
    # reference_narrow as ~97% of "the sample" and rule 3 fires before the run
    # has barely started (review 2026-09-26).
    n_registered = len(labels) if labels else 0
    if not n_registered:
        # fixture fallback: registered sample = everything tabulated OR queued
        n_registered = len(
            set(checkpoint)
            | set(buckets.get("evidence_missing", {}).get("qids") or [])
            | set(buckets.get("model_wrong", {}).get("qids") or [])
            | set(buckets.get(RN_LABEL, {}).get("qids") or [])
        )
    n_total = n_registered or (n_tabulated + n_rn)
    rn_share = (n_rn / n_total) if n_total else 0.0

    # Completeness: every registered non-RN qid — from the step0 labels AND the
    # step1 queue — must be tabulated before §5.3 recovery rules may fire.
    # RN qids are never run, so they are excluded.
    queue_non_rn = set(buckets.get("evidence_missing", {}).get("qids") or []) | set(
        buckets.get("model_wrong", {}).get("qids") or []
    )
    labels_non_rn = {q for q, lab in labels.items() if lab != RN_LABEL}
    registered_non_rn = queue_non_rn | labels_non_rn
    missing_queued = sorted(registered_non_rn - set(checkpoint))
    run_complete = not missing_queued

    em = counts.get("evidence_missing", {})
    mw = counts.get("model_wrong", {})
    em_rec, mw_rec = em.get("recovered", 0), mw.get("recovered", 0)

    def _rule(rule_id: str, text: str, condition: bool) -> dict[str, Any]:
        """§5.3 recovery rules only fire on a COMPLETE tabulation.

        r4 (discard on unconstrained replacement) is an observed defect, not a
        recovery conclusion, so it is evaluated ungated by the caller.
        """
        if not run_complete:
            return {
                "id": rule_id,
                "text": text,
                "fires": False,
                "blocked_by_incomplete_run": True,
            }
        return {
            "id": rule_id,
            "text": text,
            "fires": bool(condition),
            "blocked_by_incomplete_run": False,
        }

    decision_rules = [
        _rule(
            "5.3-r1",
            "If evidence_missing recovers and model_wrong does not, the bottleneck "
            "is corpus coverage. Stop generation work and continue instrument ingestion.",
            em_rec > 0 and mw_rec == 0,
        ),
        _rule(
            "5.3-r2",
            "If model_wrong recovers under the quote-and-reject gate and already-correct "
            "answers do not regress, integrate that single contrastive call behind the gate.",
            mw_rec > 0 and regressed == 0,
        ),
        _rule(
            "5.3-r3",
            "If both stay at zero binary flips while reference_narrow is a large share "
            "of the sample, the ceiling is the scorer. Fix references before any further prompt.",
            em_rec == 0 and mw_rec == 0 and rn_share >= RN_LARGE_SHARE,
        ),
        {
            "id": "5.3-r4",
            "text": (
                "If a candidate is produced by unconstrained full-answer replacement, "
                "discard the run. That condition is already measured."
            ),
            "fires": unconstrained_kept > 0,
            "blocked_by_incomplete_run": False,
        },
    ]

    report = {
        "experiment": "Step 3 — gated generation report",
        "plan_ref": PLAN_REF,
        "generated_at": now(),
        "scorer": SCORER,
        # 1. per-label counts — published before any aggregate soft-score claim
        "counts": {label: counts[label] for label in sorted(counts)},
        "n_queued_residual": n_tabulated,
        "run_complete": run_complete,
        "missing_queued_qids": missing_queued[:40],
        "n_missing_queued": len(missing_queued),
        "registered_residual_sample": n_registered or None,
        "reasons": {label: dict(c.most_common()) for label, c in sorted(reasons.items())},
        "gate_rejections": dict(gate_fail.most_common()),
        "safety_rejections": dict(safety_fail.most_common()),
        "reference_narrow": {
            "n": n_rn,
            "generation_calls_spent": 0 if not rn_qids & set(checkpoint) else -1,
            "share_of_residual_sample": round(rn_share, 4),
            "note": "never queued; dual-score reported by step0_dual_score_report (sec 5.2 Step 1)",
        },
        "safety_invariants": {
            "already_correct_regressed": regressed,
            "unconstrained_full_answer_replacements_kept": unconstrained_kept,
        },
        "budget_accounting": {
            "cap": BUDGET_CAP,
            "unit": "received responses (parseable or not) — a stricter read of "
            "plan sec 5.2 'cap successful generations at 150'",
            "transport_failures_counted": False,
        },
        # 2. decision rules (sec 5.3) — evaluated on the counts above
        "decision_rules": decision_rules,
        # 3. aggregate soft scores — only after counts are published
        "soft_scores": soft_section,
        "threshold_bands": threshold_bands,
        "n_records": len(checkpoint),
    }
    # hard invariants
    if rn_qids & set(checkpoint):
        report["reference_narrow"]["generation_calls_spent"] = -1
        report["budget_violation"] = "reference_narrow qid present in checkpoint"
    return report


def render_report_md(report: dict) -> str:
    lines = [
        "# Step 3 — gated generation report",
        "",
        f"**Plan:** {report['plan_ref']}",
        f"**Generated:** {report['generated_at']}",
        f"**Scorer:** `{report['scorer']}`",
        "",
        "## Per-label counts (published before any aggregate soft-score claim)",
        "",
        "| Label | recovered | rejected | unchanged | not_run | total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, c in report["counts"].items():
        tot = sum(c.values())
        lines.append(
            f"| `{label}` | {c.get('recovered', 0)} | {c.get('rejected', 0)} | "
            f"{c.get('unchanged', 0)} | {c.get('not_run', 0)} | {tot} |"
        )
    rn = report["reference_narrow"]
    lines.append(
        f"| `{RN_LABEL}` | — | — | {rn['n']} | — | {rn['n']} |"
    )
    completeness = (
        "**complete** — all queued qids tabulated"
        if report["run_complete"]
        else f"**incomplete** — {report['n_missing_queued']} queued qid(s) not yet "
        "tabulated (§5.3 recovery rules withheld)"
    )
    lines += [
        "",
        f"- run tabulation: {completeness}",
        f"- reference_narrow generation calls spent: **{rn['generation_calls_spent']}** "
        f"(registered budget: 0; share of residual sample: {rn['share_of_residual_sample']:.1%})",
        "",
        "### Rejection reasons by label",
        "",
    ]
    for label, rs in report["reasons"].items():
        lines.append(f"- `{label}`: " + ", ".join(f"{k} ×{v}" for k, v in rs.items()))
    lines += ["", "### Registered-gate rejection reasons", ""]
    for k, v in report["gate_rejections"].items():
        lines.append(f"- {k} ×{v}")
    lines += ["", "### Step-2 safety rejections", ""]
    if report["safety_rejections"]:
        for k, v in report["safety_rejections"].items():
            lines.append(f"- {k} ×{v}")
    else:
        lines.append("- none")
    inv = report["safety_invariants"]
    lines += [
        "",
        "### Safety invariants",
        "",
        f"- already-correct answers regressed: **{inv['already_correct_regressed']}** (must be 0)",
        f"- unconstrained full-answer replacements kept: "
        f"**{inv['unconstrained_full_answer_replacements_kept']}** (must be 0; else discard run)",
        "",
        "## Decision rules (§5.3)",
        "",
    ]
    for r in report["decision_rules"]:
        if r.get("blocked_by_incomplete_run"):
            flag = "withheld — run incomplete"
        else:
            flag = "FIRES" if r["fires"] else "does not fire"
        lines.append(f"- **{r['id']}** [{flag}]: {r['text']}")
    lines += ["", "## Aggregate soft scores (after per-label counts)", "", "| Label | n | soft before | soft after |", "|---|---:|---:|---:|"]
    for label, s in report["soft_scores"].items():
        lines.append(
            f"| `{label}` | {s['n']} | {s['soft_before_mean']} | {s['soft_after_mean']} |"
        )
    lines += [
        "",
        f"Records tabulated: {report['n_records']} "
        f"(queued residual: {report['n_queued_residual']})",
        "",
        "### Near-threshold soft-score bands",
        "",
        "Binary deltas are fragile where soft sits just under 0.50:",
        "",
        "| band | before | after |",
        "|---|---:|---:|",
    ]
    tb = report["threshold_bands"]
    for band in ("[0.45,0.50)", "[0.40,0.45)", "[0.35,0.40)", "<0.35"):
        lines.append(f"| {band} | {tb['before'].get(band, 0)} | {tb['after'].get(band, 0)} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _suffix(stub: bool) -> str:
    return "_stub" if stub else ""


def render_fill_review_md(payload: dict) -> str:
    lines = [
        "# Step 3 — EM reference-anchored fill review (0 calls)",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Plan:** {payload['plan_ref']}",
        "",
        "Approve per qid (or all) with:",
        "",
        "```",
        "SKIP_SCHEMA_CHECK=1 .venv/Scripts/python.exe evaluation/step3_gated_generation.py \\",
        "  --approve-fills Q052,Q055  (or --approve-fills-all)",
        "```",
        "",
        f"Proposals: {payload['n_proposals']} qid(s). Margin rule: candidate reference "
        f"overlap must beat the best frozen chunk by > {payload['margin']} "
        f"(floor {payload['min_overlap']}); chunks are in-family, not in the frozen "
        "payload, not textual duplicates of it. **No generation call is spent until approved.**",
        "",
        f"Decisions so far — approved: {payload['approved_qids'] or '[]'} | "
        f"rejected: {payload['rejected_qids'] or '[]'}",
        "",
    ]
    for p in payload["proposals"]:
        g = p.get("gate") or {}
        lines += [
            f"## {p['qid']}",
            "",
            f"- best frozen overlap: {p.get('best_frozen_overlap', 'n/a')} "
            f"→ proposed threshold: {p.get('best_frozen_overlap', 0) or 0} + {payload['margin']}",
            f"- decision: **{p.get('decision', 'pending')}** | "
            f"gate: **{g.get('decision', 'n/a')}** "
            f"(p/r/j={g.get('n_pass', 0)}/{g.get('n_review', 0)}/{g.get('n_reject', 0)})",
            f"- gate codes: `{', '.join(g.get('reason_codes') or []) or 'none'}`",
            "",
            "| # | section | overlap | gate | score | sem | codes | snippet |",
            "|---:|---|---:|---|---:|---:|---|---|",
        ]
        for i, c in enumerate(p["candidates"], 1):
            snip = c["snippet"].replace("|", "\\|")
            cg = c.get("gate") or {}
            codes = ", ".join((cg.get("reason_codes") or [])[:3])
            lines.append(
                f"| {i} | {c['section'] or '—'} | {c['overlap']} | "
                f"{cg.get('decision', 'n/a')} | {cg.get('reference_quality_score', 'n/a')} | "
                f"{cg.get('semantic_anchor', 'n/a')} | {codes} | {snip}… |"
            )
        lines.append("")
    return "\n".join(lines)


def build_fill_review(deps: Deps) -> dict[str, Any]:
    """Proposals for EM qids that have nothing mechanical to add (0 calls)."""
    decisions = load_fill_decisions()
    queue = load_queue(deps)
    proposals: list[dict] = []
    for qid in queue["evidence_missing"]:
        pre = em_precondition(qid, deps)
        fill = pre.get("proposed_fill")
        if not fill:
            continue
        proposals.append(
            {
                **fill,
                "decision": decisions.get(qid, "pending"),
                "approved": decisions.get(qid) == "approved",
                "gate": pre.get("fill_gate") or fill.get("gate") or {},
                "current_reason": pre.get("reason"),
            }
        )
    return {
        "experiment": "Step 3 — EM reference-anchored fill review (0 calls)",
        "plan_ref": PLAN_REF,
        "generated_at": now(),
        "margin": REF_FILL_MARGIN,
        "min_overlap": REF_FILL_MIN_OVERLAP,
        "k_per_qid": REF_FILL_K,
        "approved_qids": sorted(q for q, d in decisions.items() if d == "approved"),
        "rejected_qids": sorted(q for q, d in decisions.items() if d == "rejected"),
        "n_proposals": len(proposals),
        "gate_summary": {
            d: sum(1 for p in proposals if (p.get("gate") or {}).get("decision") == d)
            for d in ("PASS", "REVIEW", "REJECT")
        },
        "proposals": proposals,
    }


def build_gate_report(review: dict[str, Any]) -> dict[str, Any]:
    """Per-candidate multi-stage gate diagnostics (0 calls, read-only)."""
    per_candidate: list[dict[str, Any]] = []
    gate_summary = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
    qid_summary = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
    code_counts: Counter = Counter()
    for p in review["proposals"]:
        g = p.get("gate") or {}
        if g.get("decision") in qid_summary:
            qid_summary[g["decision"]] += 1
        for code in g.get("reason_codes") or []:
            code_counts[code] += 1
        for i, c in enumerate(p.get("candidates") or []):
            cg = c.get("gate") or {}
            dec = cg.get("decision", "REVIEW")
            if dec in gate_summary:
                gate_summary[dec] += 1
            for code in cg.get("reason_codes") or []:
                code_counts[code] += 1
            per_candidate.append(
                {
                    "qid": p["qid"],
                    "candidate_index": i,
                    "chunk_id": c.get("chunk_id"),
                    "section": c.get("section"),
                    "decision": dec,
                    "reason_codes": cg.get("reason_codes") or [],
                    "reference_quality_score": cg.get("reference_quality_score"),
                    "semantic_anchor": cg.get("semantic_anchor"),
                    "raw_overlap": cg.get("raw_overlap"),
                    "best_frozen_overlap": cg.get("best_frozen_overlap"),
                    "overlap_inflation": cg.get("overlap_inflation"),
                    "margin_ok": cg.get("margin_ok"),
                    "identifier_match": cg.get("identifier_match"),
                    "instrument_conflict": cg.get("identifier_conflict"),
                    "section_mismatch": cg.get("section_mismatch"),
                    "question_act": cg.get("question_act"),
                    "candidate_act": cg.get("candidate_act"),
                    "question_sections": cg.get("question_sections"),
                    "candidate_sections": cg.get("candidate_sections"),
                    "snippet": c.get("snippet"),
                }
            )
    return {
        "experiment": "Step 3 — EM fill quality gate report (0 calls)",
        "plan_ref": PLAN_REF,
        "generated_at": now(),
        "scorer": SCORER,
        "gate_stages": [
            "raw overlap", "reference quality", "metadata/OCR/heading/fragment",
            "legal identifier extraction", "legal identifier consistency",
            "semantic relevance (stopword-free)", "frozen-vs-candidate margin",
            "decision REJECT|REVIEW|PASS", "generation only after approval",
        ],
        "thresholds": {
            "margin": REF_FILL_MARGIN,
            "min_overlap": REF_FILL_MIN_OVERLAP,
        },
        "n_proposals": review["n_proposals"],
        "n_candidates": len(per_candidate),
        "qid_gate_summary": qid_summary,
        "gate_summary": gate_summary,
        "reason_code_counts": dict(code_counts.most_common()),
        "approved_qids": review["approved_qids"],
        "rejected_qids": review["rejected_qids"],
        "candidates": per_candidate,
        "note": (
            "Deterministic multi-stage gate; REJECT proposals can never be "
            "approved, REVIEW/PASS still require explicit human approval. "
            "No generation call is spent by this report."
        ),
    }


def render_gate_report_md(report: dict[str, Any]) -> str:
    gs = report["gate_summary"]
    qs = report["qid_gate_summary"]
    lines = [
        "# Step 3 — EM fill quality gate report",
        "",
        f"**Generated:** {report['generated_at']} | **Candidates:** "
        f"{report['n_candidates']} across {report['n_proposals']} proposals",
        "",
        "## Pipeline",
        "",
    ]
    lines += [f"{i}. {s}" for i, s in enumerate(report["gate_stages"], 1)]
    lines += [
        "",
        "## Decisions",
        "",
        "| Level | PASS | REVIEW | REJECT |",
        "|---|---:|---:|---:|",
        f"| proposal (qid) | {qs['PASS']} | {qs['REVIEW']} | {qs['REJECT']} |",
        f"| candidate | {gs['PASS']} | {gs['REVIEW']} | {gs['REJECT']} |",
        "",
        "## Reason-code frequencies",
        "",
        "| Code | Count |",
        "|---|---:|",
    ]
    for code, n in report["reason_code_counts"].items():
        lines.append(f"| `{code}` | {n} |")
    lines += [
        "",
        "## Candidates",
        "",
        "| qid | # | section | decision | quality | sem | raw | infl | margin | codes |",
        "|---|---:|---|---|---:|---:|---:|---:|---|---|",
    ]
    for c in report["candidates"]:
        codes = ", ".join(c["reason_codes"][:4])
        lines.append(
            f"| {c['qid']} | {c['candidate_index']} | {c['section'] or '—'} | "
            f"**{c['decision']}** | {c['reference_quality_score']} | "
            f"{c['semantic_anchor']} | {c['raw_overlap']} | {c['overlap_inflation']} | "
            f"{'ok' if c['margin_ok'] else 'fail'} | {codes} |"
        )
    lines += [
        "",
        f"Approved qids: {report['approved_qids'] or '[]'} | "
        f"human-rejected: {report['rejected_qids'] or '[]'}",
        "",
        report["note"],
        "",
    ]
    return "\n".join(lines)


def write_approval(
    approved_qids: set[str],
    source: str,
    *,
    rejected_qids: set[str] | None = None,
    findings: dict[str, str] | None = None,
    imported_from: str | None = None,
) -> Path:
    prev: dict = {}
    if FILL_APPROVAL_JSON.exists():
        try:
            prev = json.loads(FILL_APPROVAL_JSON.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    approved = set(prev.get("approved_qids") or [])
    rejected = set(prev.get("rejected_qids") or [])
    new_approved = set(approved_qids)
    new_rejected = set(rejected_qids or [])
    # latest decision wins: re-approving a rejected qid (or vice versa) is allowed
    approved = (approved - new_rejected) | new_approved
    rejected = (rejected - new_approved) | new_rejected
    merged_findings = dict(prev.get("findings") or {})
    merged_findings.update(findings or {})
    payload = {
        "experiment": "Step 3 — EM fill decision (human-in-the-loop)",
        "plan_ref": PLAN_REF,
        "decided_at": now(),
        "source": source,
        **({"imported_from": imported_from} if imported_from else {}),
        "approved_qids": sorted(approved),
        "rejected_qids": sorted(rejected),
        "findings": merged_findings,
        "note": (
            "Approving a qid permits ONE answer call each on a payload rebuilt with "
            "the proposed reference-anchored chunks prepended (plan sec 5.2, 'add the "
            "missing instrument text'). Rejecting keeps it not_run/fill_rejected_by_human "
            "(zero calls). The scorer and gates are frozen."
        ),
    }
    FILL_APPROVAL_JSON.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return FILL_APPROVAL_JSON


def load_fill_decisions_csv(path) -> tuple[set[str], set[str], dict[str, str], int]:
    """Parse a human fill-review CSV: columns qid + status (+ optional finding).

    Status vocabulary (case-insensitive substring match): reject* -> rejected;
    approve* -> approved; anything else (e.g. 'Manual verification') stays
    UNDECIDED and is not written — the qid keeps reporting fill_review_pending.

    Returns (approved, rejected, findings, n_data_rows) — the row count so the
    caller can report undecided rows from the CSV's own size rather than from
    ``findings`` (which only counts rows that carried a finding cell).
    """
    import csv as _csv

    approved: set[str] = set()
    rejected: set[str] = set()
    findings: dict[str, str] = {}
    n_rows = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in _csv.reader(f) if r and any(c.strip() for c in r)]
    if not rows:
        return approved, rejected, findings, n_rows
    header = [c.strip().lower() for c in rows[0]]

    def _col(*names: str) -> int | None:
        for n in names:
            if n in header:
                return header.index(n)
        return None

    qid_i = _col("qid", "question_id", "question id")
    status_i = _col("final status", "status", "verdict", "decision", "review")
    finding_i = _col(
        "validation / truthfulness finding", "finding", "notes", "note", "reason"
    )
    if qid_i is None or status_i is None:
        raise ValueError(f"fill-decisions CSV must have qid + status columns (got: {rows[0]})")
    for row in rows[1:]:
        def _get(i: int | None) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        qid = _get(qid_i)
        if not qid:
            continue
        n_rows += 1
        status = _get(status_i).lower()
        finding = _get(finding_i)
        if finding:
            findings[qid] = finding
        if "reject" in status:
            rejected.add(qid)
        elif "approv" in status:
            approved.add(qid)
        # else: undecided (e.g. 'Manual verification') -> stays pending
    approved -= rejected
    return approved, rejected, findings, n_rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 3 — budgeted gated generation (plan sec 5.2 Step 3)")
    ap.add_argument("--preflight", action="store_true", help="write run plan (0 calls)")
    ap.add_argument("--review-fills", action="store_true",
                    help="write EM reference-fill worksheet (0 calls) -> step3_em_fill_review.{json,md}")
    ap.add_argument("--gate-report", action="store_true",
                    help="multi-stage quality-gate diagnostics for every fill candidate "
                         "(0 calls) -> step3_em_gate_report.{json,md}")
    ap.add_argument("--approve-fills", default=None, metavar="QIDS",
                    help="comma-separated qids approved for reference-anchored fill")
    ap.add_argument("--approve-fills-all", action="store_true",
                    help="approve every currently proposed fill")
    ap.add_argument("--reject-fills", default=None, metavar="QIDS",
                    help="comma-separated qids whose fill proposal is rejected (zero calls)")
    ap.add_argument("--decisions-from-csv", default=None, metavar="CSV",
                    help="import fill decisions from a review CSV (qid + status columns); "
                         "reject*/approve* recorded, other statuses stay pending")
    ap.add_argument("--run", action="store_true", help="execute eligible qids (checkpointed)")
    ap.add_argument("--stub", action="store_true", help="0-call pipeline validation with canned JSON")
    ap.add_argument("--report", action="store_true", help="rebuild report from checkpoint")
    ap.add_argument("--require-registered", action="store_true",
                    help="exit 2 unless Step 0/1/2 registrations validate")
    ap.add_argument("--allow-unregistered", action="store_true",
                    help="skip the registration gate (development only)")
    ap.add_argument("--limit", type=int, default=None, help="process at most N pending qids")
    ap.add_argument("--qid", action="append", default=None, help="restrict to specific qid(s)")
    ap.add_argument("--fresh", action="store_true", help="discard existing checkpoint (regenerable artifact)")
    args = ap.parse_args(argv)

    if not (args.preflight or args.run or args.report or args.review_fills or args.approve_fills
            or args.approve_fills_all or args.reject_fills or args.decisions_from_csv
            or args.gate_report):
        ap.print_help()
        return 1

    registrations = check_registrations()
    if args.require_registered and not registrations["ok"]:
        missing = [k for k, v in registrations.items() if k != "ok" and not v]
        print(f"NOT REGISTERED: {missing}", file=sys.stderr)
        return 2
    if args.run and not (args.allow_unregistered or registrations["ok"]):
        missing = [k for k, v in registrations.items() if k != "ok" and not v]
        print(
            f"REFUSING RUN: registrations invalid {missing} "
            "(use --allow-unregistered only for development)",
            file=sys.stderr,
        )
        return 2

    sfx = _suffix(args.stub)
    deps = load_deps(with_labels=True)

    if args.review_fills or args.gate_report:
        payload = build_fill_review(deps)
        FILL_REVIEW_JSON.write_text(
            json.dumps(payload, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        FILL_REVIEW_MD.write_text(render_fill_review_md(payload), encoding="utf-8")
        print(
            f"fill review: {payload['n_proposals']} proposal(s), "
            f"{len(payload['approved_qids'])} approved, "
            f"{len(payload['rejected_qids'])} rejected, "
            f"{payload['n_proposals'] - len(payload['approved_qids']) - len(payload['rejected_qids'])} pending"
        )
        print(f"  wrote {FILL_REVIEW_JSON.name}, {FILL_REVIEW_MD.name}")
        if args.gate_report:
            greport = build_gate_report(payload)
            GATE_REPORT_JSON.write_text(
                json.dumps(greport, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
            )
            GATE_REPORT_MD.write_text(render_gate_report_md(greport), encoding="utf-8")
            gs = greport["gate_summary"]
            print(
                f"gate: PASS={gs['PASS']} REVIEW={gs['REVIEW']} REJECT={gs['REJECT']} "
                f"(candidate-level: {greport['n_candidates']} total)"
            )
            print(f"  wrote {GATE_REPORT_JSON.name}, {GATE_REPORT_MD.name}")
        return 0

    if args.decisions_from_csv:
        csv_path = Path(args.decisions_from_csv)
        if not csv_path.exists():
            print(f"REFUSING: {csv_path} not found", file=sys.stderr)
            return 2
        try:
            wanted_ok, wanted_no, findings, n_csv_rows = load_fill_decisions_csv(csv_path)
        except ValueError as e:
            print(f"REFUSING: {e}", file=sys.stderr)
            return 2
        review = build_fill_review(deps)
        proposed = {p["qid"]: p for p in review["proposals"]}
        unknown = (wanted_ok | wanted_no) - set(proposed)
        if unknown:
            print(
                f"REFUSING: no fill proposal for {sorted(unknown)} "
                "(run --review-fills to see proposals)",
                file=sys.stderr,
            )
            return 2
        gate_rejected = {
            q
            for q in wanted_ok
            if (proposed[q].get("gate") or {}).get("decision") == GATE_REJECT
        }
        if gate_rejected:
            print(
                f"REFUSING: quality gate REJECT for {sorted(gate_rejected)} "
                "(approvals of gate-REJECT proposals are never accepted)",
                file=sys.stderr,
            )
            wanted_ok -= gate_rejected
            if not wanted_ok and not wanted_no:
                return 2
        path = write_approval(
            wanted_ok,
            source="--decisions-from-csv",
            rejected_qids=wanted_no,
            findings=findings,
            imported_from=str(csv_path),
        )
        print(
            f"imported: approved={sorted(wanted_ok) or '[]'} "
            f"rejected={len(wanted_no)} "
            f"gate_REJECT_refused={sorted(gate_rejected) or 'none'} "
            f"undecided_in_csv={n_csv_rows - len(wanted_ok) - len(wanted_no) - len(gate_rejected)}"
        )
        print(f"  wrote {path.name}")
        return 0

    if args.reject_fills:
        wanted_no = {q.strip() for q in str(args.reject_fills).split(",") if q.strip()}
        review = build_fill_review(deps)
        proposed = {p["qid"] for p in review["proposals"]}
        unknown = wanted_no - proposed
        if unknown:
            print(f"REFUSING: no fill proposal for {sorted(unknown)}", file=sys.stderr)
            return 2
        path = write_approval(set(), source="--reject-fills", rejected_qids=wanted_no)
        print(f"rejected: {sorted(wanted_no)} (zero calls; reports fill_rejected_by_human)")
        print(f"  wrote {path.name}")
        return 0

    if args.approve_fills or args.approve_fills_all:
        review = build_fill_review(deps)
        proposed = {p["qid"]: p for p in review["proposals"]}
        already = set(review["approved_qids"])
        skipped_human: set[str] = set()
        if args.approve_fills_all:
            # Bulk approval = fill the UNDECIDED queue only. It must never
            # flip an explicit human rejection (that requires a deliberate
            # per-qid --approve-fills QID, and is still gate-checked).
            wanted = {
                q
                for q, p in proposed.items()
                if p.get("decision", "pending") == "pending"
            }
            skipped_human = set(proposed) - wanted
            skipped_human = {
                q for q in skipped_human if proposed[q].get("decision") == "rejected"
            }
        else:
            wanted = {q.strip() for q in str(args.approve_fills).split(",") if q.strip()}
            unknown = wanted - set(proposed)
            if unknown:
                print(
                    f"REFUSING: no fill proposal for {sorted(unknown)} "
                    "(run --review-fills to see proposals)",
                    file=sys.stderr,
                )
                return 2
        # Machine REJECT is absolute: approval of a gate-REJECTED proposal is
        # refused (plan sec 5.2; reason codes reported for audit).
        gate_rejected = {
            q
            for q in wanted
            if (proposed[q].get("gate") or {}).get("decision") == GATE_REJECT
        }
        if gate_rejected:
            for q in sorted(gate_rejected):
                codes = (proposed[q].get("gate") or {}).get("reason_codes") or []
                print(f"  {q}: {', '.join(codes[:6])}", file=sys.stderr)
            print(
                f"REFUSING: quality gate REJECT for {sorted(gate_rejected)} — "
                "these proposals cannot be approved (see --gate-report)",
                file=sys.stderr,
            )
            wanted -= gate_rejected
            if not wanted:
                return 2
        merged = already | wanted
        path = write_approval(merged, source="--approve-fills-all" if args.approve_fills_all else "--approve-fills")
        print(
            f"approved: +{sorted(wanted - already) or '[]'} "
            f"(total {len(merged)}/{len(proposed)}; "
            f"gate-REJECT refused: {sorted(gate_rejected) or 'none'}; "
            f"human rejections preserved: {sorted(skipped_human) or 'none'})"
        )
        print(f"  wrote {path.name}")
        return 0

    if args.preflight:
        plan = preflight(deps)
        RUN_PLAN_JSON.write_text(json.dumps(plan, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        RUN_PLAN_MD.write_text(render_plan_md(plan), encoding="utf-8")
        print(
            f"preflight: eligible={plan['n_eligible_for_generation']} "
            f"projected_spend={plan['projected_spend']}/{plan['budget_cap']} "
            f"within_budget={plan['within_budget']} rn_queued={plan['reference_narrow_queued']}"
        )
        print(f"  wrote {RUN_PLAN_JSON.name}, {RUN_PLAN_MD.name}")
        return 0 if plan["within_budget"] else 1

    if args.run:
        # Plans are cheap (0 calls): refresh so new fill approvals take effect
        # instead of executing a stale plan written before --approve-fills.
        plan = preflight(deps)
        RUN_PLAN_JSON.write_text(
            json.dumps(plan, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        RUN_PLAN_MD.write_text(render_plan_md(plan), encoding="utf-8")
        print(
            f"plan: eligible={plan['n_eligible_for_generation']} "
            f"fill_review_pending={sum(1 for r in plan['records'] if r.get('reason')=='fill_review_pending')} "
            f"within_budget={plan['within_budget']}",
            flush=True,
        )
        if not plan["within_budget"]:
            print("REFUSING RUN: projected spend exceeds registered cap", file=sys.stderr)
            return 1
        ledger = Ledger(LEDGER_JSON.with_name(LEDGER_JSON.stem + sfx + LEDGER_JSON.suffix), stub=args.stub)
        client = make_client(stub=args.stub)
        out_jsonl = CANDIDATES_JSONL.with_name(CANDIDATES_JSONL.stem + sfx + CANDIDATES_JSONL.suffix)
        calls_path = CALLS_JSONL.with_name(CALLS_JSONL.stem + sfx + CALLS_JSONL.suffix)
        print(
            f"run: stub={args.stub} cap={ledger.cap} spent={ledger.spent} "
            f"pending eligible={sum(1 for r in plan['records'] if r['verdict']=='eligible')}",
            flush=True,
        )
        result = execute(
            deps=deps,
            client=client,
            ledger=ledger,
            plan=plan,
            out_jsonl=out_jsonl,
            calls_path=calls_path,
            limit=args.limit,
            qids=args.qid,
            fresh=args.fresh,
            model_name="stub-canned-json" if args.stub else "poolside/laguna-s-2.1:free",
        )
        print(
            f"done: processed={result['n_processed']} spent={result['spent']}/{result['cap']} "
            f"transport_failures={result['transport_failures']}"
        )
        # auto-report after a run
        checkpoint = load_checkpoint(out_jsonl)
        report = build_report(checkpoint, deps)
        rj = REPORT_JSON.with_name(REPORT_JSON.stem + sfx + REPORT_JSON.suffix)
        rm = REPORT_MD.with_name(REPORT_MD.stem + sfx + REPORT_MD.suffix)
        rj.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        rm.write_text(render_report_md(report), encoding="utf-8")
        print(f"  wrote {rj.name}, {rm.name}")
        return 0

    if args.report:
        out_jsonl = CANDIDATES_JSONL.with_name(CANDIDATES_JSONL.stem + sfx + CANDIDATES_JSONL.suffix)
        checkpoint = load_checkpoint(out_jsonl)
        if not checkpoint:
            print(f"no checkpoint at {out_jsonl.name}", file=sys.stderr)
            return 2
        report = build_report(checkpoint, deps)
        rj = REPORT_JSON.with_name(REPORT_JSON.stem + sfx + REPORT_JSON.suffix)
        rm = REPORT_MD.with_name(REPORT_MD.stem + sfx + REPORT_MD.suffix)
        rj.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        rm.write_text(render_report_md(report), encoding="utf-8")
        print(f"counts: {json.dumps(report['counts'])}")
        print(f"  wrote {rj.name}, {rm.name}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
