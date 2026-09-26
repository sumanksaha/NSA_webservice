"""Offline tests for the Step 0 human review walker (no stdin, no network)."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

import evaluation.step0_human_review as hr
import evaluation.step0_label_residual as s0


RESIDUAL = ["Q001", "Q002", "Q003", "Q004"]


# --------------------------------------------------------------------------- #
# parse_verdict
# --------------------------------------------------------------------------- #


def test_parse_verdict_keys():
    assert hr.parse_verdict("r") == "reference_narrow"
    assert hr.parse_verdict("e") == "evidence_missing"
    assert hr.parse_verdict("m") == "model_wrong"
    assert hr.parse_verdict(" E ") == "evidence_missing"


def test_parse_verdict_full_words_and_invalid():
    for word in s0.STEP0_ENUM:
        assert hr.parse_verdict(word) == word
    assert hr.parse_verdict("x") is None
    assert hr.parse_verdict("") is None
    assert hr.parse_verdict("genuinely_wrong") is None


# --------------------------------------------------------------------------- #
# truncate_text
# --------------------------------------------------------------------------- #


def test_truncate_text_noop_and_tail():
    text = "x" * 50
    assert hr.truncate_text(text, None) == text
    assert hr.truncate_text(text, 50) == text
    cut = hr.truncate_text("abcdef", 4)
    assert cut.startswith("abcd")
    assert "[+2 chars]" in cut


# --------------------------------------------------------------------------- #
# candidates_first_order
# --------------------------------------------------------------------------- #


def test_candidates_first_order():
    preanno = {
        "Q003": {"evidence_missing_candidate": True},
        "Q001": {"evidence_missing_candidate": True},
        "Q002": {"evidence_missing_candidate": False},
        "Q004": {},
    }
    got = hr.candidates_first_order(RESIDUAL, preanno)
    assert got == ["Q001", "Q003", "Q002", "Q004"]


# --------------------------------------------------------------------------- #
# emit_labels / load_state / save_state round-trip
# --------------------------------------------------------------------------- #


def _state():
    return {
        "order": ["Q001", "Q002"],
        "verdicts": {
            "Q001": {"verdict": "model_wrong", "notes": "n", "ts": "t"},
            "Q002": {"verdict": "", "notes": "", "ts": "t"},
        },
    }


def test_emit_labels_skips_unlabeled():
    assert hr.emit_labels(_state()) == {"Q001": "model_wrong"}


def test_state_round_trip(tmp_path):
    progress = tmp_path / "progress.json"
    labels = tmp_path / "labels.json"
    state = _state()
    hr.save_state(state, progress, labels)
    on_disk = json.loads(progress.read_text(encoding="utf-8"))
    assert on_disk["order"] == ["Q001", "Q002"]
    assert json.loads(labels.read_text(encoding="utf-8")) == {"Q001": "model_wrong"}

    resumed = hr.load_state(RESIDUAL, progress)
    # order reconciled: previous order first, then any new residual qids
    assert resumed["order"][:2] == ["Q001", "Q002"]
    assert resumed["verdicts"]["Q001"]["verdict"] == "model_wrong"


def test_load_state_drops_stale_qids(tmp_path):
    progress = tmp_path / "progress.json"
    progress.write_text(
        json.dumps({"order": ["Q001", "Q999"], "verdicts": {"Q999": {"verdict": "model_wrong"}}}),
        encoding="utf-8",
    )
    state = hr.load_state(["Q001", "Q002"], progress)
    assert state["order"] == ["Q001", "Q002"]
    assert "Q999" not in state["verdicts"]


def test_load_state_corrupt_file(tmp_path):
    progress = tmp_path / "progress.json"
    progress.write_text("{not json", encoding="utf-8")
    state = hr.load_state(RESIDUAL, progress)
    assert state["order"] == RESIDUAL
    assert state["verdicts"] == {}


# --------------------------------------------------------------------------- #
# format_packet / stats_line
# --------------------------------------------------------------------------- #


def _packet():
    return {
        "qid": "Q001",
        "difficulty": "EASY",
        "question_type": "Direct provision",
        "question": "What does s.16 require?",
        "reference": "It requires X.",
        "v1_reference": None,
        "signals": ["f1_justified_abstention"],
        "suggestion": "evidence_missing",
        "answers": {"C-O3": "a", "D2": "b", "D3": "c", "E1": "d"},
        "v2row": {
            "D2": {
                "status": "ok",
                "v2": {"correct": False, "soft": 0.43},
                "v1": {"correct": False, "answer_correctness": 0.43},
            },
        },
    }


def test_format_packet_contains_sections():
    text = hr.format_packet(_packet(), short=False, note=None)
    assert "[Q001] EASY" in text
    assert "REFERENCE (v2 widened):" in text
    assert "machine suggestion: evidence_missing" in text
    assert "[B. D2] (v2: incorrect, soft 0.430" in text
    # conditions with no v2 row render as not run
    assert "[A. C-O3] (not run)" in text


def test_format_packet_note_and_trim():
    p = _packet()
    p["answers"]["D2"] = "y" * 900
    text = hr.format_packet(p, short=True, note="rule 63 absent")
    assert "[+200 chars]" in text
    assert text.endswith("note: rule 63 absent")


def test_stats_line_counts():
    state = {
        "order": ["Q001", "Q002", "Q003"],
        "verdicts": {
            "Q001": {"verdict": "model_wrong"},
            "Q002": {"verdict": "model_wrong"},
        },
    }
    line = hr.stats_line(state)
    assert "done 2/3" in line and "remaining 1" in line and "model_wrong" in line


# --------------------------------------------------------------------------- #
# load_csv_records
# --------------------------------------------------------------------------- #


def test_load_csv_records_full_schema(tmp_path):
    path = tmp_path / "review.csv"
    path.write_text(
        "packet_no,question_id,human_correct,verdict,model_action,category,notes\n"
        "1,Q001,true,model_wrong,contrastive_repair,provision_application_error,\"misapplied s.22\"\n"
        "2,Q002,false,evidence_missing,add_instrument_text,corpus_gap,rule 63 absent\n"
        "3,Q003,true,reference_narrow,fix_reference,reference_scope_mismatch,\"narrow ref\"\n",
        encoding="utf-8",
    )
    records, skipped = hr.load_csv_records(path, RESIDUAL)
    assert skipped == []
    assert records["Q001"] == {
        "verdict": "model_wrong",
        "notes": "misapplied s.22",
        "human_correct": "true",
        "model_action": "contrastive_repair",
        "category": "provision_application_error",
    }
    assert records["Q002"]["verdict"] == "evidence_missing"
    assert records["Q003"]["model_action"] == "fix_reference"


def test_load_csv_records_skips_invalid_and_non_residual(tmp_path):
    path = tmp_path / "review.csv"
    path.write_text(
        "packet_no,question_id,human_correct,verdict,model_action,category,notes\n"
        "1,Q001,true,model_wrong,contrastive_repair,,ok row\n"
        "2,Q002,true,genuinely_wrong,,bad verdict\n"
        "3,Q999,true,model_wrong,contrastive_repair,,non-residual qid\n"
        "4,,true,model_wrong,contrastive_repair,,empty qid\n",
        encoding="utf-8",
    )
    records, skipped = hr.load_csv_records(path, RESIDUAL)
    assert set(records) == {"Q001"}
    assert len(skipped) == 3
    assert any("invalid verdict" in s for s in skipped)
    assert any("not in residual set" in s for s in skipped)


def test_load_csv_records_bom_and_case(tmp_path):
    path = tmp_path / "review.csv"
    # utf-8-sig text mode prepends exactly one BOM, like a real Excel export
    path.write_text("question_id,verdict\nQ001,MODEL_WRONG\n", encoding="utf-8-sig")
    records, skipped = hr.load_csv_records(path, ["Q001"])
    assert skipped == []
    assert records["Q001"]["verdict"] == "model_wrong"


def test_load_csv_records_missing_optional_columns(tmp_path):
    path = tmp_path / "review.csv"
    path.write_text("question_id,verdict\nQ001,evidence_missing\n", encoding="utf-8")
    records, skipped = hr.load_csv_records(path, ["Q001"])
    assert records["Q001"] == {
        "verdict": "evidence_missing",
        "notes": "",
        "human_correct": "",
        "model_action": "",
        "category": "",
    }
