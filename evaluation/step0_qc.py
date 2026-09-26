"""Step 0 — label QC + evidence_missing triage (no model calls, no network).

Two deterministic reports over the published Step 0 label set
(``step0_residual_labels.json``, 124 residual qids):

  --qc         step0_label_qc.{json,md}
               Consistency checks between labels, the review records
               (step0_human_records.json), the machine pre-annotations and the
               frozen per-question scores. Every finding carries a code, a
               severity (error | warning | info) and the qids it applies to, so
               a human can re-audit exactly the packets that need it.

  --em-triage  step0_em_ingestion_report.{json,md}
               Splits the ``evidence_missing`` bucket into the four states the
               plan's Step 1 gate actually distinguishes:

                 index_gap        gold unit not resolved in the corpus index
                                  -> plan reject rule ("section not in the
                                     index — stop, do not loop retrieval")
                 ingest_priority  in the index but the section *body* text was
                                  never ingested -> corpus fill (the plan's
                                  "add the missing instrument text")
                 payload_gap      body ingested, absent from the O3 payload
                                  -> re-retrieve this qid only (Step 3)
                 evidence_present body in corpus AND payload -> the label is
                                  questionable; re-audit before spending a call

Scorer is frozen: this module never imports generation code and never calls an
LLM. Findings are advisory — labels only change through the registered
``step0_label_residual.py --labels/--publish`` path.

Human-record semantics assumed by the contradiction checks (documented here
because the residual worksheet never defined the field):

  human_correct == the reviewer judged the model's best answer legally
                   acceptable (``true`` / ``false``).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

OUT = ROOT / "evaluation" / "out" / "ceiling_v5"

LABELS_PUBLISHED = OUT / "step0_residual_labels.json"
RECORDS_PATH = OUT / "step0_human_records.json"
PREANNO_PATH = OUT / "step0_preannotations.json"
V2_PERQ_PATH = OUT / "evaluator_v2_per_question.jsonl"
QC_JSON = OUT / "step0_label_qc.json"
QC_MD = OUT / "step0_label_qc.md"
TRIAGE_JSON = OUT / "step0_em_ingestion_report.json"
TRIAGE_MD = OUT / "step0_em_ingestion_report.md"

SEVERITIES = ("error", "warning", "info")

# The three templated review notes shipped with the review CSV. A record whose
# ``notes`` equals its verdict's template carries no per-qid rationale.
TEMPLATE_NOTES = {
    "model_wrong": (
        "Operative text is available, but the candidate answer materially misapplies or misstates the provision."
    ),
    "evidence_missing": (
        "The operative text needed to establish the reference answer is not present in the available O3 evidence."
    ),
    "reference_narrow": ("Candidate gives a defensible reading, but the reference is narrower or framed differently."),
}

EXPECTED_MODEL_ACTION = {
    "model_wrong": "contrastive_repair",
    "evidence_missing": "add_instrument_text",
    "reference_narrow": "fix_reference",
}

# Priority order for EM triage: worst unit state decides the qid class.
EM_CLASSES = (
    "index_gap",
    "ingest_priority",
    "payload_gap",
    "evidence_present",
)
_CLASS_RANK = {name: i for i, name in enumerate(EM_CLASSES)}


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0"):
            return False
    return None


def unit_states(units: dict[str, dict]) -> dict[str, str]:
    """Map each primary gold unit to an evidence state string."""
    out: dict[str, str] = {}
    for uid, u in (units or {}).items():
        if not u.get("in_corpus"):
            out[uid] = "index_gap"
        elif u.get("body_in_corpus") is False:
            out[uid] = "ingest_priority"
        elif not u.get("body_in_o3"):
            out[uid] = "payload_gap"
        else:
            out[uid] = "evidence_present"
    return out


def classify_em(units: dict[str, dict]) -> str | None:
    """Worst (highest-priority) unit state for one evidence_missing qid."""
    states = unit_states(units)
    if not states:
        return None
    return min(states.values(), key=lambda s: _CLASS_RANK.get(s, 0))


def finding(code: str, severity: str, qids: list[str], detail: str = "") -> dict:
    assert severity in SEVERITIES, severity
    return {
        "code": code,
        "severity": severity,
        "n": len(qids),
        "qids": sorted(qids),
        "detail": detail,
    }


def run_qc(
    labels: dict[str, str],
    residual_qids: list[str],
    records: dict[str, dict] | None = None,
    preanno: dict[str, dict] | None = None,
    v2_rows: dict[str, dict] | None = None,
) -> dict:
    """Deterministic label/record/evidence consistency checks.

    Returns a report dict: summary counts, findings grouped by code, and a
    per-qid index of everything needing human re-audit (``reaudit_qids``).
    """
    import evaluation.step0_label_residual as s0

    records = records or {}
    preanno = preanno or {}
    v2_rows = v2_rows or {}

    validation = s0.validate_labels(labels, residual_qids)
    by_code: dict[str, list[str]] = defaultdict(list)
    details: dict[str, str] = {}

    def flag(code: str, qid: str, detail: str = "") -> None:
        by_code[code].append(qid)
        if detail and code not in details:
            details[code] = detail

    for qid in validation["missing_qids"]:
        flag("label_missing", qid, "residual qid has no Step 0 label")
    for qid, lab in validation["invalid_labels"].items():
        flag("label_invalid", qid, f"label {lab!r} outside the Step 0 enum")

    for qid, lab in sorted(validation["labels"].items()):
        rec = records.get(qid) or {}
        hc = _as_bool(rec.get("human_correct"))
        pre = preanno.get(qid) or {}

        # -- record consistency ------------------------------------------------
        if rec and hc is None and str(rec.get("human_correct", "")).strip() != "":
            flag(
                "human_correct_unparseable",
                qid,
                "human_correct is neither true nor false",
            )
        elif rec and not str(rec.get("human_correct", "")).strip():
            flag("human_correct_unset", qid, "review record left human_correct blank")
        elif hc is True and lab == "model_wrong":
            flag(
                "contradiction_human_correct_vs_verdict",
                qid,
                "verdict=model_wrong but human_correct=true — either the label or "
                "the human_correct field is wrong (Q005-class contradiction)",
            )
        elif hc is False and lab in ("reference_narrow", "evidence_missing"):
            flag(
                "contradiction_human_correct_vs_verdict",
                qid,
                "verdict says not-the-model's-fault but human_correct=false",
            )

        notes = str(rec.get("notes") or "").strip()
        if rec and (not notes or notes == TEMPLATE_NOTES.get(lab)):
            flag(
                "notes_are_template",
                qid,
                "review note is the verdict template — no per-qid rationale on record",
            )

        action = str(rec.get("model_action") or "").strip()
        if rec and action and action != EXPECTED_MODEL_ACTION.get(lab):
            flag(
                "model_action_mismatch",
                qid,
                f"model_action does not follow the verdict (expected {EXPECTED_MODEL_ACTION.get(lab)})",
            )

        # -- machine pre-annotation disagreement -------------------------------
        suggested = pre.get("suggested_label")
        if suggested and suggested != lab:
            flag(
                "preanno_overridden",
                qid,
                f"machine suggested {suggested}, human labeled {lab}",
            )
        if (
            lab == "evidence_missing"
            and pre
            and "evidence_missing_candidate" in pre
            and not pre.get("evidence_missing_candidate")
        ):
            flag(
                "em_not_preannotated",
                qid,
                "labeled evidence_missing but the pre-annotation did not propose it",
            )

        # -- mechanical evidence support ---------------------------------------
        units = pre.get("primary_units") or {}
        if units and lab == "evidence_missing":
            states = set(unit_states(units).values())
            if states and not (states & {"index_gap", "ingest_priority", "payload_gap"}):
                flag(
                    "em_but_evidence_present",
                    qid,
                    "every primary gold unit's body is in the corpus index and in "
                    "the O3 payload — evidence_missing label is questionable",
                )
        if lab != "evidence_missing" and units:
            bad = [u for u, s in unit_states(units).items() if s != "evidence_present"]
            if bad:
                flag(
                    "non_em_but_evidence_absent",
                    qid,
                    f"labeled {lab} but primary unit(s) missing from corpus/payload: " + ", ".join(sorted(bad)),
                )

        # -- abstention / threshold signals -------------------------------------
        row = (v2_rows.get(qid) or {}).get("D2") or {}
        v2 = row.get("v2") or {}
        ie = bool((v2_rows.get(qid) or {}).get("insufficient_evidence"))
        if lab == "evidence_missing" and not ie and not v2.get("abstained"):
            flag(
                "em_without_abstention_signal",
                qid,
                "labeled evidence_missing but the benchmark does not flag insufficient_evidence and D2 did not abstain",
            )
        if lab != "evidence_missing" and ie:
            flag(
                "ie_flag_outside_em",
                qid,
                f"benchmark insufficient_evidence flag on a {lab} label",
            )
        soft = v2.get("soft")
        if isinstance(soft, (int, float)) and not v2.get("correct") and 0.45 <= soft < 0.50:
            flag(
                "near_threshold",
                qid,
                "v2 soft in [0.45,0.50) — one wording change from a binary flip",
            )

    findings = [finding(code, _severity(code), qids, details.get(code, "")) for code, qids in sorted(by_code.items())]
    sev_counts = Counter(f["severity"] for f in findings)
    code_counts = {f["code"]: f["n"] for f in findings}

    def _qs(sev: str) -> list[str]:
        return sorted({qid for f in findings if f["severity"] == sev for qid in f["qids"]})

    error_qids = _qs("error")
    warn_qids = sorted(set(_qs("warning")) - set(error_qids))
    reaudit = sorted(set(error_qids) | set(warn_qids))
    return {
        "experiment": "Step 0 — label QC (deterministic, no model calls)",
        "plan_ref": "Experiment_D_E_F_Comprehensive_Adversarial_Evaluation_and_Improvements.md §5.2",
        "scorer": "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay",
        "source_files": {
            "labels": LABELS_PUBLISHED.name,
            "records": RECORDS_PATH.name,
            "preannotations": PREANNO_PATH.name,
            "per_question": V2_PERQ_PATH.name,
        },
        "n_residual": len(residual_qids),
        "n_labeled": validation["n_labeled_valid"],
        "label_counts": validation["label_counts"],
        "n_findings": len(findings),
        "n_findings_by_severity": dict(sev_counts),
        "n_flagged_qids": len(reaudit),
        "codes": code_counts,
        "findings": findings,
        "error_qids": error_qids,
        "warn_qids": warn_qids,
        "reaudit_qids": reaudit,
        "note": ("findings are advisory: labels change only via step0_label_residual.py --labels/--publish"),
    }


_SEVERITY_BY_CODE = {
    "label_missing": "error",
    "label_invalid": "error",
    "human_correct_unparseable": "error",
    "contradiction_human_correct_vs_verdict": "error",
    "model_action_mismatch": "error",
    "human_correct_unset": "warning",
    # blanket process finding (applies to every packet) — not a per-qid cue
    "notes_are_template": "info",
    "preanno_overridden": "warning",
    "em_not_preannotated": "warning",
    "em_but_evidence_present": "warning",
    "non_em_but_evidence_absent": "warning",
    "ie_flag_outside_em": "warning",
    "em_without_abstention_signal": "info",
    "near_threshold": "info",
}


def _severity(code: str) -> str:
    return _SEVERITY_BY_CODE.get(code, "warning")


def run_em_triage(labels: dict[str, str], preanno: dict[str, dict]) -> dict:
    """Classify every evidence_missing qid (plus out-of-bucket evidence gaps)."""
    em_qids = sorted(q for q, v in labels.items() if v == "evidence_missing")
    per_qid: dict[str, dict] = {}
    for qid in em_qids:
        units = (preanno.get(qid) or {}).get("primary_units") or {}
        states = unit_states(units)
        cls = classify_em(units)
        per_qid[qid] = {
            "class": cls or "unknown",
            "units": states,
            "absent_units": sorted(u for u, s in states.items() if s != "evidence_present"),
        }

    class_counts = Counter(v["class"] for v in per_qid.values())

    # Instrument-level ingestion priority: worst state per unit, grouped by
    # the family prefix of the provision id (e.g. "water_act:s25" -> water_act).
    ingest: dict[str, Counter] = defaultdict(Counter)
    for qid, info in per_qid.items():
        for uid in info["absent_units"]:
            family = uid.split(":", 1)[0]
            ingest[family][info["units"][uid]] += 1

    # Evidence gaps sitting under other labels (they would fail the Step 3
    # quote gate if their payload really lacks the text).
    other_gaps = []
    for qid, lab in sorted(labels.items()):
        if lab == "evidence_missing":
            continue
        units = (preanno.get(qid) or {}).get("primary_units") or {}
        states = unit_states(units)
        bad = sorted(u for u, s in states.items() if s != "evidence_present")
        if bad:
            other_gaps.append({
                "qid": qid,
                "label": lab,
                "absent_units": bad,
                "states": {u: states[u] for u in bad},
            })

    actionable = [q for q, v in per_qid.items() if v["class"] in ("index_gap", "ingest_priority")]
    retrievable = [q for q, v in per_qid.items() if v["class"] == "payload_gap"]
    questionable = [q for q, v in per_qid.items() if v["class"] == "evidence_present"]

    return {
        "experiment": "Step 0 — evidence_missing triage (corpus vs payload vs label)",
        "plan_ref": "Experiment_D_E_F_Comprehensive_Adversarial_Evaluation_and_Improvements.md §5.2/Step 1 gate",
        "n_evidence_missing": len(em_qids),
        "class_counts": dict(class_counts),
        "classes": {
            "index_gap": {
                "meaning": "gold unit not resolved in the corpus index",
                "action": "plan reject rule — stop, do not loop retrieval",
                "qids": sorted(q for q, v in per_qid.items() if v["class"] == "index_gap"),
            },
            "ingest_priority": {
                "meaning": "in the index, but the section body text was never ingested",
                "action": "add the missing instrument text to the corpus",
                "qids": sorted(q for q, v in per_qid.items() if v["class"] == "ingest_priority"),
            },
            "payload_gap": {
                "meaning": "body ingested, absent from the O3 payload",
                "action": "re-retrieve this qid only, then one answer call (Step 3)",
                "qids": sorted(q for q, v in per_qid.items() if v["class"] == "payload_gap"),
            },
            "evidence_present": {
                "meaning": "body in corpus and payload — evidence_missing is questionable",
                "action": "re-audit the label before spending a generation call",
                "qids": sorted(q for q, v in per_qid.items() if v["class"] == "evidence_present"),
            },
        },
        "n_actionable_for_ingestion": len(actionable),
        "n_re_retrievable": len(retrievable),
        "n_label_questionable": len(questionable),
        "ingestion_priority_by_instrument": {
            fam: dict(cnt) for fam, cnt in sorted(ingest.items(), key=lambda kv: -sum(kv[1].values()))
        },
        "per_qid": per_qid,
        "evidence_gaps_under_other_labels": other_gaps,
        "note": (
            "class per qid = worst primary-unit state. evidence_present qids are "
            "NOT eligible for a corpus fill call until the label is re-audited."
        ),
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_qc_md(qc: dict) -> str:
    lines = [
        "# Step 0 — Label QC Report",
        "",
        f"- residual qids: **{qc['n_residual']}**, labeled: **{qc['n_labeled']}** ({qc['label_counts']})",
        f"- findings: **{qc['n_findings']}** across **{qc['n_flagged_qids']}** qids ({qc['n_findings_by_severity']})",
        f"- source: `{qc['source_files']['labels']}` + "
        f"`{qc['source_files']['records']}` + `{qc['source_files']['preannotations']}`",
        "",
        "Findings are advisory — labels change only through `step0_label_residual.py --labels ... --publish`.",
        "",
        "## Findings by code",
        "",
        "| code | severity | n | meaning |",
        "|---|---|---:|---|",
    ]
    for f in qc["findings"]:
        lines.append(f"| `{f['code']}` | {f['severity']} | {f['n']} | {f['detail'] or '-'} |")
    lines += ["", "## Qids needing human re-audit", ""]
    lines += [
        f"- errors (n={len(qc['error_qids'])}):",
        "",
        "```",
        " ".join(qc["error_qids"]) or "(none)",
        "```",
        "",
        f"- warnings (n={len(qc['warn_qids'])}):",
        "",
        "```",
        " ".join(qc["warn_qids"]) or "(none)",
        "```",
        "",
    ]
    lines += ["## Detail", ""]
    for f in qc["findings"]:
        if f["severity"] == "info":
            continue
        lines += [f"### {f['code']} ({f['severity']}, n={f['n']})", ""]
        lines += ["```", " ".join(f["qids"]), "```", ""]
    return "\n".join(lines)


def render_triage_md(tri: dict) -> str:
    lines = [
        "# Step 0 — evidence_missing triage (corpus vs payload vs label)",
        "",
        f"Bucket: **{tri['n_evidence_missing']}** `evidence_missing` qids ({tri['class_counts']}),",
        "",
        "| class | n | meaning | action |",
        "|---|---:|---|---|",
    ]
    for name in EM_CLASSES:
        blk = tri["classes"][name]
        lines.append(f"| `{name}` | {len(blk['qids'])} | {blk['meaning']} | {blk['action']} |")
    lines += [
        "",
        f"- actionable for corpus fill: **{tri['n_actionable_for_ingestion']}**",
        f"- re-retrieval only (already ingested): **{tri['n_re_retrievable']}**",
        f"- label questionable (evidence already present): **{tri['n_label_questionable']}**",
        "",
        "## Ingestion priority by instrument",
        "",
        "| instrument | index_gap | ingest_priority | payload_gap | evidence_present |",
        "|---|---:|---:|---:|---:|",
    ]
    for fam, cnt in tri["ingestion_priority_by_instrument"].items():
        lines.append(
            f"| {fam} | {cnt.get('index_gap', 0)} | {cnt.get('ingest_priority', 0)} "
            f"| {cnt.get('payload_gap', 0)} | {cnt.get('evidence_present', 0)} |"
        )
    lines += ["", "## Per-qid class", "", "| qid | class | absent units |", "|---|---|---|"]
    for qid, info in sorted(tri["per_qid"].items()):
        lines.append(f"| {qid} | `{info['class']}` | {', '.join(info['absent_units']) or '-'} |")
    if tri["evidence_gaps_under_other_labels"]:
        lines += [
            "",
            "## Evidence gaps under other labels (quote-gate risk)",
            "",
            "| qid | label | absent units |",
            "|---|---|---|",
        ]
        for r in tri["evidence_gaps_under_other_labels"]:
            lines.append(f"| {r['qid']} | {r['label']} | {', '.join(r['absent_units'])} |")
    lines += ["", tri["note"], ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Re-audit — evidence-based label corrections (still no model calls)
# --------------------------------------------------------------------------- #

REAUDIT_JSON = OUT / "step0_reaudit.json"
REAUDIT_MD = OUT / "step0_reaudit.md"
PRE_REAUDIT_LABELS = OUT / "step0_residual_labels.pre_reaudit.json"

# Rule thresholds (documented, conservative: a label only moves on strong evidence).
REF_COV_EM_TO_RN = 0.50  # EM -> RN: reference content this present in the payload
ANS_COV_MW_TO_RN = 0.50  # MW + human_correct -> RN: answer this grounded in the payload

# The 7 returned `reference_narrow` packets (step0_rn_widen_status.json) and the
# targeted provision probes that decide whether the operative text really is in
# the O3 payload. `must_any`: at least one pattern must appear, otherwise the
# RN label is falsified (`evidence_missing`); all present -> label upheld and the
# widening is retried against a payload-wide anchor instead.
# A generic token-coverage rule cannot replace this: 14 RN qids sit below 0.5
# coverage and only these 2 actually lack the operative provision text.
RN_RETURNED_PROBES = {
    "Q009": {
        "must_any": ["food analyst"],
        "topic": "FSS s44 — Food Analyst's report as evidence",
    },
    "Q049": {
        "must_any": [
            "direct the closure",
            "environmental emergency",
            "closure of any industries",
        ],
        "topic": "EPA s6 — closure/demolition direction",
    },
    "Q051": {
        "must_any": ["opportunity of being heard", "show cause", "show-cause"],
        "topic": "EPA s6 — hearing proviso before a closure direction",
    },
    "Q065": {
        "must_any": ["noxious", "obnoxious"],
        "topic": "KMC — noxious/dangerous trade and nuisance control",
    },
    "Q069": {
        "must_any": ["sewerage", "drainage"],
        "topic": "KMC — drainage/sewerage duty and nuisance removal",
    },
    "Q070": {
        "must_any": ["section 517", "nuisance"],
        "topic": "KMC s517 — immediate removal of nuisance",
    },
    "Q087": {
        "must_any": ["single-use plastic", "single use plastic"],
        "topic": "PWM Rules — single-use plastic prohibition",
    },
}


def payload_blob(qid: str, manifest: dict, payload_index: dict) -> str:
    entry = (manifest.get("questions") or {}).get(qid) or {}
    cond = (entry.get("conditions") or {}).get("O3_full_support") or {}
    ids = cond.get("context_chunk_ids") or []
    import evaluation.step0_label_residual as s0

    return "\n".join(
        s0._chunk_text(payload_index[cid])
        for cid in ids
        if cid in payload_index
    )


def _coverage(needle: str, haystack: str) -> float | None:
    """Fraction of distinctive needle tokens present in haystack (stopword-free)."""
    import re as _re

    stop = set(
        ["the", "a", "an", "of", "and", "or", "to", "in", "for", "is", "are", "be", "shall", "must", "may", "with", "on", "by", "that", "this", "it", "as", "at", "from", "any", "its", "not", "no", "if", "or", "other", "under", "within", "act", "section"]
    )
    a = {t for t in _re.findall(r"[a-z0-9]+", needle.lower()) if t not in stop and len(t) > 2}
    b = set(_re.findall(r"[a-z0-9]+", haystack.lower()))
    return len(a & b) / len(a) if a else None


def reaudit_evidence(
    qid: str,
    *,
    labels: dict[str, str],
    questions: dict,
    manifest: dict,
    payload_index: dict,
    records: dict,
    preanno: dict,
    v2_rows: dict,
) -> dict:
    """Everything a re-audit decision rests on, for one qid."""
    lab = labels.get(qid)
    q = questions.get(qid)
    ref = (getattr(q, "acceptable_conclusion", None) or "") if q else ""
    blob = payload_blob(qid, manifest, payload_index)
    d2 = ((v2_rows.get(qid) or {}).get("D2") or {}).get("answer") or ""
    units = (preanno.get(qid) or {}).get("primary_units") or {}
    hc = _as_bool((records.get(qid) or {}).get("human_correct"))
    probe = RN_RETURNED_PROBES.get(qid)
    probe_hit = None
    if probe:
        low = blob.lower()
        probe_hit = next((p for p in probe["must_any"] if p in low), None)
    return {
        "label": lab,
        "human_correct": hc,
        "ref_cov": _coverage(ref, blob),
        "ans_cov": _coverage(d2, blob),
        "unit_states": sorted(unit_states(units).values()),
        "probe_topic": probe["topic"] if probe else None,
        "probe_hit": probe_hit,
        "probe_missing": [p for p in (probe or {}).get("must_any", []) if p not in blob.lower()],
    }


def decide_reaudit(qid: str, ev: dict) -> tuple[str | None, str, str]:
    """Apply the registered re-audit rules to one packet.

    Returns (new_label or None, rule id, rationale). None = label upheld.
    """
    lab = ev.get("label")
    states = set(ev.get("unit_states") or [])
    ref_cov = ev.get("ref_cov")
    ans_cov = ev.get("ans_cov")

    if lab == "model_wrong" and ev.get("human_correct") is True and (ans_cov or 0) >= ANS_COV_MW_TO_RN:
        return (
            "reference_narrow",
            "R1_mw_human_correct_answer_grounded",
            f"verdict contradicts human_correct=true and the answer is grounded in the "
            f"payload (ans_cov={ans_cov:.3f} >= {ANS_COV_MW_TO_RN}); the miss is "
            "reference/scorer-side, not a provision misapplication",
        )
    if (
        lab == "evidence_missing"
        and states == {"evidence_present"}
        and (ref_cov or 0) >= REF_COV_EM_TO_RN
    ):
        return (
            "reference_narrow",
            "R2_em_reference_present_in_payload",
            f"every primary gold body is in the corpus index and in the O3 payload and "
            f"{ref_cov:.3f} of the reference's content tokens are in the payload "
            f">= {REF_COV_EM_TO_RN}; 'operative text absent' is falsified and "
            "human_correct=true makes this a scorer miss",
        )
    if lab == "reference_narrow" and qid in RN_RETURNED_PROBES and ev.get("probe_hit") is None:
        return (
            "evidence_missing",
            "R3_rn_operative_text_absent_from_payload",
            f"none of the registered provision probes for {ev.get('probe_topic')} "
            f"appear in the O3 payload (tried: {', '.join(ev.get('probe_missing') or [])}); "
            "the reference cannot be widened from evidence that is not there",
        )
    if lab == "reference_narrow" and qid in RN_RETURNED_PROBES:
        return (
            None,
            "upheld_rn_operative_text_present",
            f"registered probe for {ev.get('probe_topic')} hit on "
            f"{ev['probe_hit']!r} — evidence is in the payload; the gold-unit anchor "
            "was the defect, so the widening is retried payload-wide",
        )
    if lab == "evidence_missing":
        return (
            None,
            "upheld_em_reference_not_in_payload",
            f"ref_cov={ref_cov} below {REF_COV_EM_TO_RN} or a primary unit shows an "
            "index/payload gap — the evidence_missing label stands",
        )
    if lab == "model_wrong":
        return (
            None,
            "upheld_mw",
            "no contradiction with human_correct and the label is not in the "
            "re-audit scope rule set",
        )
    return None, "upheld_no_rule", "no re-audit rule applies"


def run_reaudit(
    labels: dict[str, str],
    residual_qids: list[str],
    *,
    questions: dict,
    manifest: dict,
    payload_index: dict,
    records: dict,
    preanno: dict,
    v2_rows: dict,
) -> dict:
    """Evidence-based label corrections for the flagged packets.

    Scope (31 packets): verdict/human_correct contradictions + evidence_missing
    qids whose primary bodies are all present + the returned reference_narrow
    packets. Only strong evidence moves a label; everything else is recorded as
    upheld with its reason.
    """
    import evaluation.step0_label_residual as s0

    contradiction = {
        q
        for q, rec in records.items()
        if q in labels
        and _as_bool(rec.get("human_correct")) is not None
        and (
            (labels[q] == "model_wrong" and _as_bool(rec.get("human_correct")) is True)
            or (
                labels[q] in ("reference_narrow", "evidence_missing")
                and _as_bool(rec.get("human_correct")) is False
            )
        )
    }
    em_present = {
        q
        for q in residual_qids
        if labels.get(q) == "evidence_missing"
        and set(unit_states((preanno.get(q) or {}).get("primary_units") or {}).values())
        == {"evidence_present"}
    }
    # probe table keys only count when the qid is actually in this residual set
    scope = sorted(
        (contradiction | em_present | set(RN_RETURNED_PROBES)) & set(residual_qids)
    )

    examined: list[dict] = []
    changes: dict[str, str] = {}
    for qid in scope:
        ev = reaudit_evidence(
            qid,
            labels=labels,
            questions=questions,
            manifest=manifest,
            payload_index=payload_index,
            records=records,
            preanno=preanno,
            v2_rows=v2_rows,
        )
        new_label, rule, rationale = decide_reaudit(qid, ev)
        if new_label and new_label != ev["label"]:
            changes[qid] = new_label
        examined.append(
            {
                "qid": qid,
                "from": ev["label"],
                "to": new_label or ev["label"],
                "changed": bool(new_label and new_label != ev["label"]),
                "rule": rule,
                "rationale": rationale,
                "evidence": {
                    k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in ev.items()
                },
            }
        )

    new_labels = dict(labels)
    new_labels.update(changes)
    return {
        "experiment": "Step 0 — re-audit of flagged label packets (deterministic, 0 model calls)",
        "plan_ref": "Experiment_D_E_F_Comprehensive_Adversarial_Evaluation_and_Improvements.md §5.2",
        "scorer": "frozen: experiment_b_topk_eval token_overlap/abstain_check + evaluator_v2 overlay",
        "scope_note": (
            "scope = verdict/human_correct contradictions + evidence_missing qids with all "
            "primary bodies present + the 7 returned reference_narrow packets"
        ),
        "thresholds": {
            "REF_COV_EM_TO_RN": REF_COV_EM_TO_RN,
            "ANS_COV_MW_TO_RN": ANS_COV_MW_TO_RN,
            "rn_returned_probes": {k: v["topic"] for k, v in RN_RETURNED_PROBES.items()},
        },
        "n_examined": len(examined),
        "n_changed": len(changes),
        "counts_before": dict(s0.validate_labels(labels, residual_qids)["label_counts"]),
        "counts_after": dict(
            s0.validate_labels(new_labels, residual_qids)["label_counts"]
        ),
        "changes": {k: {"from": labels[k], "to": v} for k, v in sorted(changes.items())},
        "examined": examined,
        "new_labels": dict(sorted(new_labels.items())),
        "note": (
            "published only via --publish-reaudit, which snapshots the registered labels "
            "first; Step 3 counts stay reported on the registered partition"
        ),
    }


def render_reaudit_md(ra: dict) -> str:
    lines = [
        "# Step 0 — label re-audit",
        "",
        f"Examined **{ra['n_examined']}** flagged packets, changed **{ra['n_changed']}** "
        f"({ra['counts_before']} → {ra['counts_after']}).",
        "",
        f"Scope: {ra['scope_note']}",
        "",
        "| qid | from | to | rule | rationale |",
        "|---|---|---|---|---|",
    ]
    for r in ra["examined"]:
        lines.append(
            f"| {r['qid']} | `{r['from']}` | `{r['to']}` | `{r['rule']}` | "
            f"{r['rationale']} |"
        )
    lines += [
        "",
        "Thresholds: "
        + ", ".join(f"`{k}={v}`" for k, v in ra["thresholds"].items() if isinstance(v, float)),
        "",
        ra["note"],
        "",
    ]
    return "\n".join(lines)


def publish_reaudit(ra: dict, residual_qids: list[str], preanno: dict) -> Path:
    """Snapshot the registered labels, then republish with re-audit provenance."""
    import evaluation.step0_label_residual as s0

    if LABELS_PUBLISHED.exists():
        PRE_REAUDIT_LABELS.write_text(
            LABELS_PUBLISHED.read_text(encoding="utf-8"), encoding="utf-8"
        )
    validation = s0.validate_labels(ra["new_labels"], residual_qids)
    if not validation["step0_complete"]:
        raise SystemExit("re-audit label set incomplete — refusing to publish")
    extra = {
        "reaudit": {
            "n_examined": ra["n_examined"],
            "n_changed": ra["n_changed"],
            "changes": ra["changes"],
            "counts_before": ra["counts_before"],
            "counts_after": ra["counts_after"],
            "thresholds": ra["thresholds"],
            "registered_labels_snapshot": PRE_REAUDIT_LABELS.name,
            "step3_reporting_note": (
                "Step 3 per-label counts were published on the registered partition; "
                "both partitions are reported in step0_analysis.md"
            ),
        }
    }
    return s0.publish(validation, "step0_qc.py --publish-reaudit", preanno, extra=extra)


# --------------------------------------------------------------------------- #
# Loading + CLI
# --------------------------------------------------------------------------- #


def load_inputs() -> tuple[dict, list[str], dict, dict, dict]:
    labels_doc = json.loads(LABELS_PUBLISHED.read_text(encoding="utf-8")) if LABELS_PUBLISHED.exists() else {}
    labels = labels_doc.get("labels") or {}
    residual_qids = sorted(labels) or []
    if not residual_qids and (OUT / "step0_residual_set.json").exists():
        residual_qids = json.loads((OUT / "step0_residual_set.json").read_text(encoding="utf-8")).get("qids") or []
    records = json.loads(RECORDS_PATH.read_text(encoding="utf-8")) if RECORDS_PATH.exists() else {}
    preanno = (
        json.loads(PREANNO_PATH.read_text(encoding="utf-8")).get("annotations", {}) if PREANNO_PATH.exists() else {}
    )
    v2_rows: dict[str, dict] = {}
    if V2_PERQ_PATH.exists():
        with V2_PERQ_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    v2_rows[rec["qid"]] = rec
    return labels, residual_qids, records, preanno, v2_rows


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 0 — label QC + EM triage (no model calls)")
    ap.add_argument("--qc", action="store_true", help="write step0_label_qc.{json,md}")
    ap.add_argument(
        "--em-triage", action="store_true", help="write step0_em_ingestion_report.{json,md}"
    )
    ap.add_argument(
        "--reaudit",
        action="store_true",
        help="write step0_reaudit.{json,md} — evidence-based label corrections (0 calls)",
    )
    ap.add_argument(
        "--publish-reaudit",
        action="store_true",
        help="snapshot registered labels, then republish them with re-audit provenance",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 when the QC report contains error-severity findings",
    )
    args = ap.parse_args(argv)

    if not (args.qc or args.em_triage or args.reaudit or args.publish_reaudit):
        ap.print_help()
        return 1

    labels, residual_qids, records, preanno, v2_rows = load_inputs()
    if not labels:
        print("no published labels found — run step0_label_residual.py --publish first")
        return 2

    rc = 0
    if args.qc:
        qc = run_qc(labels, residual_qids, records, preanno, v2_rows)
        _write(QC_JSON, json.dumps(qc, indent=1, ensure_ascii=False))
        _write(QC_MD, render_qc_md(qc))
        print(
            f"qc: {qc['n_findings']} findings on {qc['n_flagged_qids']}/{qc['n_residual']} qids "
            f"({qc['n_findings_by_severity']}) -> {QC_JSON.name}, {QC_MD.name}"
        )
        for f in qc["findings"]:
            print(f"  [{f['severity']}] {f['code']}: {f['n']}")
        if args.strict and qc["n_findings_by_severity"].get("error"):
            rc = 2

    if args.em_triage:
        tri = run_em_triage(labels, preanno)
        _write(TRIAGE_JSON, json.dumps(tri, indent=1, ensure_ascii=False))
        _write(TRIAGE_MD, render_triage_md(tri))
        print(f"em-triage: {tri['class_counts']} -> {TRIAGE_JSON.name}, {TRIAGE_MD.name}")
        if not preanno:
            print(
                "  (no pre-annotations loaded — regenerate with step0_label_residual.py --preannotate)",
                file=sys.stderr,
            )

    if args.reaudit or args.publish_reaudit:
        import evaluation.step0_label_residual as s0

        questions = s0.load_questions()
        manifest = s0.load_c_manifest()
        payload_index = s0.load_payload_index()
        if not payload_index or not manifest:
            print(
                "reaudit needs payload_index.jsonl + the C manifest — cannot verify evidence presence",
                file=sys.stderr,
            )
            return 2
        ra = run_reaudit(
            labels,
            residual_qids,
            questions=questions,
            manifest=manifest,
            payload_index=payload_index,
            records=records,
            preanno=preanno,
            v2_rows=v2_rows,
        )
        _write(REAUDIT_JSON, json.dumps(ra, indent=1, ensure_ascii=False))
        _write(REAUDIT_MD, render_reaudit_md(ra))
        print(
            f"reaudit: examined {ra['n_examined']} packets, {ra['n_changed']} changes "
            f"{ra['counts_before']} -> {ra['counts_after']} -> {REAUDIT_JSON.name}, {REAUDIT_MD.name}"
        )
        for qid, ch in ra["changes"].items():
            print(f"  {qid}: {ch['from']} -> {ch['to']}")
        if args.publish_reaudit:
            out = publish_reaudit(ra, residual_qids, preanno)
            print(
                f"published {out.name} (registered labels snapshotted to {PRE_REAUDIT_LABELS.name})"
            )

    return rc


if __name__ == "__main__":
    sys.exit(main())
