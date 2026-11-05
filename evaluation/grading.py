"""Deterministic answer grader (protocol §13 — answer-level metrics).

Scores a generated answer 2/1/0 per ``evaluation_rubric_v1.0.md`` §3 using a
transparent keyword + citation heuristic.  This is an *automatic* grader —
not legal-expert judgment — and the report labels it as such:

    A. legal_correctness  — token overlap with gold conclusion + concepts
    B. provision_correct  — cited chunks cover the gold provision
    C. citation_correct   — every [n] resolves to a chunk that covers gold
    D. completeness       — all primary+acceptable provisions cited
    E. temporal_correct   — repealed markers absent (no gold label; heuristic)
    F. jurisdiction       — jurisdiction tokens consistent with gold
    G. hallucination      — unsupported numeric/legal claims not in evidence
    H. abstention         — correct refusal on insufficient-evidence questions

``critical_error`` (rubric §4) forces the score to 0.
"""

from __future__ import annotations

import re
from typing import Any

from evaluation.benchmark import BenchmarkQuestion
from evaluation.resolution import FamilyMap, norm_act_name

_CITATION_RE = re.compile(r"\[(\d+)\]")
_AMOUNT_RE = re.compile(
    r"(?:rs\.?\s*|inr\s*|\u20b9|rupees?)\s*[\d,]+(?:\s*(?:lakh|crore))?|[\d,]{2,}\s*(?:lakh|crore)\s*rupees?",
    re.IGNORECASE,
)
_IMPRISONMENT_RE = re.compile(
    r"imprisonment\s*for\s*a\s*term|imprisonment|fine\s*(?:not\s*less|which\s*may\s*extend)", re.IGNORECASE,
)
#: Section numbers cited in prose ("Section 31", "S. 31", "section 31A").
_SECTION_RE = re.compile(r"(?:section|sec\.?|s\.?)\s*\d+[A-Za-z]?", re.IGNORECASE)
#: Act / regulation names ("the Act", "FSS Act", "the Rules").
_ACT_NAME_RE = re.compile(
    r"(?:the\s+)?(?:[A-Z][A-Za-z]{1,30}\s+)?(?:Act|Rules?|Regulation|Ordinance|Amendment)\b",
    re.IGNORECASE,
)
#: High-signal legal terms that must be backed by evidence when asserted
#: as a positive claim (not merely mentioned in a negation).
_LEGAL_TERMS_RE = re.compile(
    r"\b(?:proviso|suspension|forfeiture|seizure|cancellation|licen[cs]e|"
    r"permit|registration|appeal|revision|review|commutation|"
    r"exemption|exclusion|proviso|saving)\b",
    re.IGNORECASE,
)
_ABSTAIN_PHRASES = (
    "not establish",
    "not establish",
    "cannot be determined",
    "cannot be reliably",
    "insufficient evidence",
    "insufficient",
    "no evidence",
    "does not specify",
    "does not establish",
    "is not recorded",
    "not recorded in the corpus",
    "cannot be answered",
    "not available in the corpus",
    "does not provide",
    "not stated",
    "no specific",
    "not mentioned",
    "cannot reliably read",
    "the corpus does not",
    "no provision in the corpus",
    "no information",
)
_REFUSE_PHRASES = (
    "not establish",
    "cannot be determined",
    "insufficient",
    "does not establish",
    "not recorded",
    "no evidence",
    "cannot be reliably",
)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def _overlap_ratio(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not tb:
        return 0.0
    return len(ta & tb) / len(tb)


def grade_answer(
    question: BenchmarkQuestion,
    answer: str,
    cited_chunk_ids: list[str],
    evidence_chunk_ids: list[str],
    evidence_texts: dict[str, str],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> dict[str, Any]:
    """Grade one answer.  ``cited_chunk_ids`` = ids behind the [n] markers."""
    answer = answer or ""
    a_low = answer.lower()
    units = question.relevant_units()
    all_units = question.recall_units()

    # --- citation extraction from [n] markers ---
    cited_ids = list(dict.fromkeys(cited_chunk_ids))
    markers = [int(m) for m in _CITATION_RE.findall(answer)]

    # --- B/C: provision + citation correctness ---
    gold_covered_by_cites = set()
    for cid in cited_ids:
        payload = payload_index.get(str(cid))
        if payload is None:
            continue
        for unit in all_units:
            from evaluation.resolution import matches_gold

            if matches_gold(payload, unit, family_map, text_fallback=True):
                gold_covered_by_cites.add(unit.provision_id)
    provision_correct = any(u.provision_id in gold_covered_by_cites for u in units)
    citation_correct = provision_correct and not markers  # no markers -> can't verify
    if markers:
        citation_correct = provision_correct

    # --- A: legal correctness (conclusion + concepts overlap) ---
    concl_ratio = _overlap_ratio(answer, question.acceptable_conclusion)
    concepts = " ".join(question.gold_concepts)
    concepts_ratio = _overlap_ratio(answer, concepts)
    legal_correct = concl_ratio >= 0.35 or concepts_ratio >= 0.45

    # --- D: completeness (all relevant cited) ---
    completeness = all(u.provision_id in gold_covered_by_cites for u in units) and bool(units)

    # --- E: temporal heuristic ---
    temporal_correct = None
    if "Temporal" in question.question_types:
        temporal_correct = "repealed" not in a_low or "repealed law" not in a_low

    # --- F: jurisdiction consistency ---
    gold_jur = norm_act_name(question.jurisdiction)
    jur_tokens = [t for t in gold_jur.split() if len(t) >= 4]
    jurisdiction_correct = bool(jur_tokens) and any(t in a_low for t in jur_tokens)

    # --- G: hallucination proxy (numeric/legal claims not in evidence) ---
    evidence_blob = " ".join(evidence_texts.values()).lower()
    hallucinated = False
    hallucination_reasons: list[str] = []
    for claim in _AMOUNT_RE.findall(answer):
        if claim.lower() not in evidence_blob:
            hallucinated = True
            hallucination_reasons.append(f"amount:{claim!r}")
            break
    if not hallucinated and _IMPRISONMENT_RE.search(answer):
        # imprisonment claims must be backed by evidence text mentioning them
        if "imprisonment" not in evidence_blob and "fine" not in evidence_blob:
            hallucinated = True
            hallucination_reasons.append("imprisonment_unbacked")

    # G+: section-number hallucination — a cited section must appear in evidence
    if not hallucinated:
        for match in _SECTION_RE.finditer(answer):
            section_text = match.group(0).strip().lower()
            # Only flag if the section number is asserted as a positive claim
            # (not inside a negation or "not section X" context)
            if section_text not in evidence_blob:
                hallucinated = True
                hallucination_reasons.append(f"section:{section_text!r}")
                break

    # G+: act-name hallucination — named Acts/Rules not in evidence
    if not hallucinated:
        for match in _ACT_NAME_RE.finditer(answer):
            act_text = match.group(0).strip().lower()
            if act_text not in evidence_blob and act_text not in ("the act", "the rules", "the regulation"):
                # Skip generic references; only flag specific named instruments
                if re.search(r"[A-Z][a-z]+", match.group(0)):
                    hallucinated = True
                    hallucination_reasons.append(f"act_name:{act_text!r}")
                    break

    # G+: unbacked high-signal legal terms — positive assertions of legal
    # concepts (proviso, forfeiture, cancellation, etc.) not supported by evidence
    if not hallucinated:
        for match in _LEGAL_TERMS_RE.finditer(answer):
            term = match.group(0).lower()
            if term not in evidence_blob:
                hallucinated = True
                hallucination_reasons.append(f"legal_term:{term!r}")
                break

    # --- H: abstention ---
    abstention_correct = None
    if question.insufficient_evidence:
        abstention_correct = any(p in a_low for p in _REFUSE_PHRASES)
    else:
        abstention_correct = True  # not required

    # --- critical errors (rubric §4) ---
    critical_error = False
    wrong_family_marker = False
    if not provision_correct and units:
        critical_error = True  # wrong provision
    # wrong-law detection: answer names an act family outside the gold set
    gold_families = {u.family for u in units}
    for fam in family_map.families:
        if fam in gold_families:
            continue
        act_names = family_map.family_to_acts.get(fam, [])
        for act in act_names:
            if act and norm_act_name(act) and norm_act_name(act) in norm_act_name(answer):
                wrong_family_marker = True
                break
        if wrong_family_marker:
            break
    if wrong_family_marker and units:
        critical_error = True
    if hallucinated:
        critical_error = True

    # --- composite score 2/1/0 ---
    if critical_error:
        score = 0
    elif legal_correct and provision_correct and citation_correct and not hallucinated:
        score = 2 if completeness else 1
    elif legal_correct or provision_correct:
        score = 1
    else:
        score = 0

    return {
        "score": score,
        "legal_correct": legal_correct,
        "provision_correct": provision_correct,
        "citation_correct": citation_correct,
        "completeness": completeness,
        "temporal_correct": temporal_correct,
        "jurisdiction_correct": jurisdiction_correct,
        "hallucination_detected": hallucinated,
        "hallucination_reasons": hallucination_reasons,
        "abstention_correct": abstention_correct,
        "critical_error": critical_error,
        "wrong_family_marker": wrong_family_marker,
        "conclusion_overlap": round(concl_ratio, 3),
        "concepts_overlap": round(concepts_ratio, 3),
        "n_citation_markers": len(markers),
        "n_valid_citations": len(cited_ids),
        "gold_covered_by_citations": sorted(gold_covered_by_cites),
    }
