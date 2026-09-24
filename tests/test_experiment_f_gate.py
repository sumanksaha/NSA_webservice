"""Offline tests for Experiment F Layer 1 (abstention calibration).

Covers the deterministic pieces only — gate logic, evaluator-v2 scoring shim,
hallucination guard, citation hygiene. No network, no LLM calls.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.experiment_b_topk_eval import abstain_check
from evaluation.experiment_f_calibration_eval import (
    _HALLUC_AMOUNT_RE,
    RECOVERY_MAX_TOKENS,
    score_v2,
)


# --------------------------------------------------------------------------- #
# Gate: abstention detection on D2 answers
# --------------------------------------------------------------------------- #
def test_abstain_check_flags_refusal():
    # verified against the production regex (B, unchanged)
    assert abstain_check("The evidence does not provide the specific fee amount.")
    assert abstain_check("The corpus does not contain the licensing fee.")
    assert abstain_check("The regulation does not specify the validity period.")


def test_abstain_check_passes_substantive_answer():
    assert not abstain_check(
        "Under section 16(1) of the Food Safety and Standards Act, the Food Authority "
        "shall regulate and monitor the manufacture, processing, distribution, sale "
        "and import of food [2]."
    )


def test_abstain_check_empty_answer():
    assert abstain_check("")
    assert abstain_check("   ")


# --------------------------------------------------------------------------- #
# Evaluator v2 scoring shim
# --------------------------------------------------------------------------- #
OVERLAY = json.loads((ROOT / "evaluation" / "evaluator_v2_overlay.json").read_text(encoding="utf-8"))


def test_score_v2_matches_v1_when_no_overlay():
    refs = ["The penalty is imprisonment up to six months and fine up to five lakh rupees."]
    ans = "The penalty for unlicensed business is imprisonment up to six months and a fine up to five lakh rupees."
    s = score_v2(ans, refs, set())
    assert s["correct"] and s["soft"] > 0.5 and not s["abstained_v2"]


def test_score_v2_overlay_addition_can_flip_correct():
    v1_ref = "The reference states a narrow conclusion about the licence duration."
    widened = OVERLAY["widened_conclusions"]["Q138"]["add"]
    answer = "The corpus does not record the exact validity period for the green-tick licence."
    alone = score_v2(answer, [v1_ref], set())
    with_v2 = score_v2(answer, [v1_ref, widened], set())
    assert not alone["correct"]
    assert with_v2["correct"] or with_v2["soft"] > alone["soft"]


def test_score_v2_banned_markers_suppress_false_abstention():
    answer = "The Act text does not establish a numeric pH limit; standards are notified separately."
    s_plain = score_v2(answer, ["ref"], set())
    s_banned = score_v2(answer, ["ref"], {"does not establish"})
    assert s_plain["abstained_v2"] and not s_banned["abstained_v2"]


# --------------------------------------------------------------------------- #
# Anti-hallucination guard
# --------------------------------------------------------------------------- #
def test_halluc_amount_regex_flags_unbacked_amount():
    m = _HALLUC_AMOUNT_RE.findall("The fine is \u20b95,00,000 for this offence.")
    assert m  # detected an amount claim that the caller must verify in evidence


def test_halluc_guard_passes_when_amount_in_evidence():
    evidence = "the fine which may extend to five lakh rupees".lower()
    claims = _HALLUC_AMOUNT_RE.findall("The penalty is fine which may extend to five lakh rupees.")
    assert all(c.lower() in evidence for c in claims)


# --------------------------------------------------------------------------- #
# Constants / contract sanity
# --------------------------------------------------------------------------- #
def test_recovery_token_cap_is_8192():
    assert RECOVERY_MAX_TOKENS == 8192


def test_gate_lists_are_disjoint():
    g = json.loads((ROOT / "evaluation/out/ceiling_v5/experiment_F_gate_lists.json").read_text(encoding="utf-8"))
    f1, f2 = set(g["f1_abstention_gate"]), set(g["f2_provision_check"])
    assert not (f1 & f2)


def test_insufficient_evidence_questions_absent_from_f1_gate():
    sys.path.insert(0, str(ROOT))
    with __import__("contextlib").redirect_stdout(__import__("io").StringIO()):
        from evaluation.benchmark import load_questions

    qs = {q.raw["question_id"]: q for q in load_questions()}
    ie = {q for q, qq in qs.items() if qq.insufficient_evidence}
    g = json.loads((ROOT / "evaluation/out/ceiling_v5/experiment_F_gate_lists.json").read_text(encoding="utf-8"))
    assert not (ie & set(g["f1_abstention_gate"]))
