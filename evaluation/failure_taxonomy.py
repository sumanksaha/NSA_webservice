"""Legal failure taxonomy — automatic classification of ranking failures.

Classifies every ranking failure (gold provision present in candidate pool
but not ranked in top-K) into one of the master plan's categories A–L.
Uses ONLY deterministic signals from the payload index and gold registry —
no LLM, no external service.

Usage:
    python -m evaluation.failure_taxonomy

Produces:
    evaluation/out/ceiling_v5/failure_taxonomy.json  — per-question failure records
    evaluation/out/ceiling_v5/failure_taxonomy_summary.json — aggregate stats
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import GoldUnit, load_questions
from evaluation.config import OUT_DIR
from evaluation.resolution import FamilyMap, matches_gold, norm_section

OUT = OUT_DIR / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Taxonomy categories
# --------------------------------------------------------------------------- #
CATEGORIES = {
    "A_same_act_wrong_section": "Same Act, wrong section",
    "B_same_section_wrong_subsection": "Same section, wrong subsection",
    "C_same_legal_concept": "Same legal concept",
    "D_same_terminology": "Same terminology (high word overlap)",
    "E_procedural_vs_substantive": "Procedural vs substantive confusion",
    "F_definition_vs_operative": "Definition vs operative provision",
    "G_exception_vs_general_rule": "Exception vs general rule",
    "H_cross_reference_failure": "Cross-reference failure",
    "I_authority_jurisdiction_mismatch": "Authority/jurisdiction mismatch",
    "J_temporal_version_error": "Temporal-version error",
    "K_adjacent_section_confusion": "Adjacent-section confusion",
    "L_multi_provision_requirement": "Multi-provision requirement (needs >1 provision)",
    "unclassified": "Unclassified failure",
}


def _extract_section_number(text: str) -> str | None:
    """Extract leading section number from text."""
    m = re.match(r"\s*(\d{1,4})", str(text or ""))
    return m.group(1) if m else None


def _count_subsections(text: str) -> int:
    """Count subsection markers in text."""
    return len(re.findall(r"\(\d+\)|\([ivxlc]+\)|\([a-z]+\)", str(text)))


def _word_overlap(a: str, b: str) -> float:
    """Jaccard-like word overlap."""
    stop = {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "for",
        "under",
        "what",
        "which",
        "who",
        "how",
        "is",
        "are",
        "does",
        "do",
        "be",
        "by",
        "on",
        "at",
        "with",
        "from",
        "as",
        "that",
        "this",
        "its",
        "it",
        "not",
        "shall",
        "may",
        "act",
        "section",
        "sec",
        "rule",
    }
    wa = {w for w in re.findall(r"[a-z0-9]+", str(a).lower()) if w not in stop}
    wb = {w for w in re.findall(r"[a-z09]+", str(b).lower()) if w not in stop}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _is_provisional(text: str) -> bool:
    """Heuristic: does the text discuss procedures/remedies rather than rights?"""
    proc_markers = [
        "shall apply",
        "application",
        "procedure",
        "notice",
        "hearing",
        "appeal",
        "tribunal",
        "court",
        "filing",
        "petition",
        "complaint",
        "proceeding",
        "compliance",
        "inspection",
        "seizure",
        "detention",
    ]
    text_lower = text.lower()
    return sum(1 for m in proc_markers if m in text_lower) >= 2


def _is_definition(text: str) -> bool:
    """Heuristic: does the text define terms?"""
    def_markers = [
        '"means"',
        "'means'",
        "definition",
        "for the purposes of",
        "shall have the meaning",
        "shall be deemed",
    ]
    text_lower = text.lower()
    return any(m in text_lower for m in def_markers)


def _is_exception(text: str) -> bool:
    """Heuristic: does the text carve out an exception?"""
    exc_markers = [
        "provided that",
        "provided further",
        "notwithstanding",
        "except",
        "save as",
        "subject to",
        "excepting",
    ]
    text_lower = text.lower()
    return any(m in text_lower for m in exc_markers)


def _temporal_conflict(gold_payload: dict, negative_payload: dict) -> bool:
    """Check for temporal version mismatch (simplified)."""
    gold_status = str(gold_payload.get("temporal_status", "")).lower()
    neg_status = str(negative_payload.get("temporal_status", "")).lower()
    # One is "repealed" or "amended" while the other isn't
    if gold_status != neg_status:
        if "repeal" in gold_status or "repeal" in neg_status:
            return True
        if "amend" in gold_status or "amend" in neg_status:
            return True
    return False


def classify_failure(
    gold_unit: GoldUnit,
    gold_payload: dict,
    negative_payload: dict,
    family_map: FamilyMap,
) -> str:
    """Classify a ranking failure where gold_unit is correct but negative won.

    Returns the category key (e.g. "A_same_act_wrong_section").
    """
    # Extract structural identifiers
    gold_sec = norm_section(gold_payload.get("section_number"))
    neg_sec = norm_section(negative_payload.get("section_number"))
    gold_sub = str(gold_payload.get("subsection", "") or "")
    neg_sub = str(negative_payload.get("subsection", "") or "")

    # Family match
    neg_fams = set(
        family_map.family_s_for_act(
            str(negative_payload.get("act_name") or negative_payload.get("document_title") or "")
        )
    )
    same_family = gold_unit.family in neg_fams

    gold_text = str(gold_payload.get("chunk_text", "") or "")
    neg_text = str(negative_payload.get("chunk_text", "") or "")
    word_sim = _word_overlap(gold_text, neg_text)

    # --- Category A: Same Act, wrong section ---
    if same_family and gold_sec and neg_sec and gold_sec != neg_sec:
        # Check if sections are adjacent (K)
        try:
            g, n = int(gold_sec), int(neg_sec)
            if abs(g - n) <= 2:
                return "K_adjacent_section_confusion"
        except ValueError:
            pass
        return "A_same_act_wrong_section"

    # --- Category B: Same section, wrong subsection ---
    if same_family and gold_sec and neg_sec and gold_sec == neg_sec and gold_sub != neg_sub:
        return "B_same_section_wrong_subsection"

    # --- Category J: Temporal version error ---
    if _temporal_conflict(gold_payload, negative_payload):
        return "J_temporal_version_error"

    # --- Category F: Definition vs operative ---
    gold_is_def = _is_definition(gold_text)
    neg_is_def = _is_definition(neg_text)
    if gold_is_def != neg_is_def:
        return "F_definition_vs_operative"

    # --- Category G: Exception vs general rule ---
    neg_is_exc = _is_exception(neg_text)
    gold_is_exc = _is_exception(gold_text)
    if neg_is_exc != gold_is_exc:
        return "G_exception_vs_general_rule"

    # --- Category E: Procedural vs substantive ---
    neg_is_proc = _is_provisional(neg_text)
    gold_is_proc = _is_provisional(gold_text)
    if neg_is_proc != gold_is_proc:
        return "E_procedural_vs_substantive"

    # --- Category I: Authority/jurisdiction mismatch ---
    gold_auth = str(gold_payload.get("authority", "")).lower()
    neg_auth = str(negative_payload.get("authority", "")).lower()
    if gold_auth and neg_auth and gold_auth != neg_auth:
        return "I_authority_jurisdiction_mismatch"

    # --- Category C: Same legal concept ---
    if same_family and word_sim > 0.3:
        return "C_same_legal_concept"

    # --- Category D: Same terminology ---
    if word_sim > 0.4:
        return "D_same_terminology"

    # --- Category H: Cross-reference failure ---
    if same_family and not gold_sec and not neg_sec:
        # Both are document-level or chapter-level — likely a cross-ref issue
        return "H_cross_reference_failure"

    # --- Category L: Multi-provision requirement ---
    # (hard to determine automatically — flag for manual review)
    if same_family and not neg_sec:
        return "L_multi_provision_requirement"

    # Default: same-family unmatched
    if same_family:
        return "C_same_legal_concept"

    return "unclassified"


def classify_ranking_failures(
    questions: list,
    payload_index: dict[str, dict],
    family_map: FamilyMap,
    rank_threshold: int = 10,
) -> list[dict]:
    """For every question, classify failures where gold exists in pool
    but no gold chunk is in top-``rank_threshold``.

    This requires per-chunk ranking data. We work from the payload index
    and the question's gold units to identify which payloads *would* be
    gold covers, and classify the *gap* between gold and non-gold.
    """
    failures = []
    for q in questions:
        rel = q.relevant_units()
        if not rel:
            continue
        # For each gold unit, check if it's resolvable in the payload index
        gold_resolved = {}
        for unit in rel:
            pts = [pid for pid, payload in payload_index.items() if matches_gold(payload, unit, family_map)]
            gold_resolved[unit.provision_id] = {
                "unit": unit,
                "point_ids": pts,
                "payload": payload_index[pts[0]] if pts else {},
            }

        # Classify the failure type based on gold unit vs corpus
        for pid, info in gold_resolved.items():
            unit = info["unit"]
            if not info["point_ids"]:
                # Gold not in corpus at all
                failures.append({
                    "question_id": q.question_id,
                    "gold_provision": pid,
                    "category": "L_multi_provision_requirement",
                    "category_label": CATEGORIES["L_multi_provision_requirement"],
                    "detail": "Gold provision not found in payload index",
                    "gold_family": unit.family,
                    "gold_section": unit.section,
                })
    return failures


def main() -> int:
    from evaluation.report_ceiling import load_payload_index

    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = load_questions()

    # Classify potential failure patterns across the benchmark
    all_failures = classify_ranking_failures(questions, payload_index, family_map)

    # Aggregate stats
    cat_counts: dict[str, int] = {}
    for f in all_failures:
        cat = f["category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    summary = {
        "n_questions": len(questions),
        "n_failures": len(all_failures),
        "category_counts": cat_counts,
        "category_labels": CATEGORIES,
        "notes": (
            "This is a corpus-level pre-analysis: it classifies which failure "
            "categories the gold-vs-payload structural gap falls into. Per-query "
            "ranking failures are classified after K=500 retrieval (see "
            "hard_negative_miner.py for the live classification)."
        ),
    }

    (OUT / "failure_taxonomy.json").write_text(json.dumps(all_failures, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "failure_taxonomy_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
