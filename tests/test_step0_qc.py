"""Offline tests for Step 0 label QC + evidence_missing triage (no network, no LLM)."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.step0_qc import (
    EM_CLASSES,
    RN_RETURNED_PROBES,
    TEMPLATE_NOTES,
    classify_em,
    render_qc_md,
    render_reaudit_md,
    render_triage_md,
    run_em_triage,
    run_qc,
    run_reaudit,
    unit_states,
)

RESIDUAL = ["Q001", "Q002", "Q003", "Q004"]


def _unit(in_corpus=True, body_in_corpus=True, body_in_o3=True):
    return {
        "in_corpus": in_corpus,
        "body_in_corpus": body_in_corpus,
        "body_in_o3": body_in_o3,
        "missing_from_o3": not body_in_o3,
    }


def _find(qc, code):
    return [f for f in qc["findings"] if f["code"] == code]


def test_em_class_priority_order():
    units = {
        "water_act:s25": _unit(in_corpus=False),
        "epa:s7": _unit(body_in_corpus=False),
        "kmc:s3": _unit(body_in_o3=False),
        "fssai:s16": _unit(),
    }
    assert unit_states(units) == {
        "water_act:s25": "index_gap",
        "epa:s7": "ingest_priority",
        "kmc:s3": "payload_gap",
        "fssai:s16": "evidence_present",
    }
    # worst unit state decides the qid class
    assert classify_em(units) == "index_gap"
    del units["water_act:s25"]
    assert classify_em(units) == "ingest_priority"
    del units["epa:s7"]
    assert classify_em(units) == "payload_gap"
    del units["kmc:s3"]
    assert classify_em(units) == "evidence_present"
    assert classify_em({}) is None


def test_em_class_order_constant():
    assert EM_CLASSES == (
        "index_gap",
        "ingest_priority",
        "payload_gap",
        "evidence_present",
    )


def test_contradiction_human_correct_vs_verdict():
    labels = {
        "Q001": "model_wrong",
        "Q002": "reference_narrow",
        "Q003": "evidence_missing",
        "Q004": "model_wrong",
    }
    records = {
        "Q001": {"human_correct": "true", "notes": "misapplies s.22", "verdict": "model_wrong"},
        "Q002": {"human_correct": "false", "notes": "narrow ref", "verdict": "reference_narrow"},
        "Q003": {"human_correct": "true", "notes": "water act absent", "verdict": "evidence_missing"},
        "Q004": {"human_correct": "false", "notes": "wrong authority", "verdict": "model_wrong"},
    }
    qc = run_qc(labels, RESIDUAL, records, {}, {})
    con = _find(qc, "contradiction_human_correct_vs_verdict")
    assert [f["severity"] for f in con] == ["error"]
    assert con[0]["qids"] == ["Q001", "Q002"]
    # non-contradicting records raise no contradiction finding
    assert "Q003" not in con[0]["qids"] and "Q004" not in con[0]["qids"]
    assert "Q001" in qc["reaudit_qids"] and "Q002" in qc["reaudit_qids"]


def test_template_notes_and_unset_human_correct():
    labels = {"Q001": "model_wrong", "Q002": "model_wrong", "Q003": "model_wrong", "Q004": "model_wrong"}
    records = {
        "Q001": {"human_correct": "true", "notes": TEMPLATE_NOTES["model_wrong"]},
        "Q002": {"human_correct": "", "notes": ""},
        "Q003": {"human_correct": "maybe", "notes": "custom rationale"},
        "Q004": {"human_correct": "false", "notes": "custom rationale"},
    }
    qc = run_qc(labels, RESIDUAL, records, {}, {})
    assert _find(qc, "notes_are_template")[0]["qids"] == ["Q001", "Q002"]
    assert _find(qc, "human_correct_unset")[0]["qids"] == ["Q002"]
    assert _find(qc, "human_correct_unparseable")[0]["qids"] == ["Q003"]
    assert not _find(qc, "notes_are_template")[0]["qids"].count("Q004")
    # Q004 is consistent: model_wrong + human_correct=false
    assert "Q004" not in qc["reaudit_qids"]


def test_model_action_mismatch():
    labels = {"Q001": "model_wrong", "Q002": "model_wrong", "Q003": "model_wrong", "Q004": "model_wrong"}
    records = {
        "Q001": {"human_correct": "false", "model_action": "fix_reference"},
        "Q002": {"human_correct": "false", "model_action": "contrastive_repair"},
    }
    qc = run_qc(labels, RESIDUAL, records, {}, {})
    assert _find(qc, "model_action_mismatch")[0]["qids"] == ["Q001"]


def test_preanno_disagreement_codes():
    labels = {
        "Q001": "model_wrong",  # machine proposed evidence_missing
        "Q002": "evidence_missing",  # machine did not propose it
        "Q003": "reference_narrow",
        "Q004": "model_wrong",
    }
    preanno = {
        "Q001": {"suggested_label": "evidence_missing", "evidence_missing_candidate": True},
        "Q002": {"suggested_label": None, "evidence_missing_candidate": False},
        "Q003": {},
        "Q004": {},
    }
    qc = run_qc(labels, RESIDUAL, {}, preanno, {})
    assert _find(qc, "preanno_overridden")[0]["qids"] == ["Q001"]
    assert _find(qc, "em_not_preannotated")[0]["qids"] == ["Q002"]


def test_evidence_support_flags_both_directions():
    labels = {
        "Q001": "evidence_missing",  # all bodies present -> questionable label
        "Q002": "model_wrong",  # unit absent from payload -> evidence gap
        "Q003": "evidence_missing",  # genuine payload gap -> no flag
        "Q004": "reference_narrow",
    }
    preanno = {
        "Q001": {"primary_units": {"fssai:s16": _unit()}},
        "Q002": {"primary_units": {"kmc:s3": _unit(body_in_o3=False)}},
        "Q003": {"primary_units": {"water_act:s25": _unit(body_in_corpus=False)}},
        "Q004": {"primary_units": {}},
    }
    qc = run_qc(labels, RESIDUAL, {}, preanno, {})
    assert _find(qc, "em_but_evidence_present")[0]["qids"] == ["Q001"]
    assert _find(qc, "non_em_but_evidence_absent")[0]["qids"] == ["Q002"]
    assert "Q003" not in qc["reaudit_qids"]


def test_abstention_and_threshold_signals_are_info():
    labels = {
        "Q001": "evidence_missing",
        "Q002": "model_wrong",
        "Q003": "reference_narrow",
        "Q004": "evidence_missing",
    }
    v2_rows = {
        "Q001": {"D2": {"v2": {"soft": 0.48, "correct": False, "abstained": False}}},
        "Q002": {"D2": {"v2": {"soft": 0.2, "correct": False}}, "insufficient_evidence": True},
        "Q003": {"D2": {"v2": {"soft": 0.47, "correct": False, "abstained": True}}},
        "Q004": {"D2": {"v2": {"soft": 0.3, "correct": False, "abstained": True}}},
    }
    qc = run_qc(labels, RESIDUAL, {}, {}, v2_rows)
    assert _find(qc, "em_without_abstention_signal")[0]["qids"] == ["Q001"]
    assert _find(qc, "ie_flag_outside_em")[0]["qids"] == ["Q002"]
    near = _find(qc, "near_threshold")[0]
    assert near["severity"] == "info"
    assert near["qids"] == ["Q001", "Q003"]
    # info findings never send a qid to re-audit
    assert "Q003" not in qc["reaudit_qids"]


def test_missing_and_invalid_labels_are_errors():
    labels = {"Q001": "model_wrong", "Q002": "not_a_label"}
    qc = run_qc(labels, RESIDUAL, {}, {}, {})
    assert _find(qc, "label_missing")[0]["severity"] == "error"
    assert _find(qc, "label_missing")[0]["n"] == 2
    assert _find(qc, "label_invalid")[0]["qids"] == ["Q002"]
    assert qc["n_findings_by_severity"]["error"] == 2


def test_run_em_triage_bucket_split():
    labels = {
        "Q001": "evidence_missing",
        "Q002": "evidence_missing",
        "Q003": "evidence_missing",
        "Q004": "evidence_missing",
        "Q005": "model_wrong",
    }
    preanno = {
        "Q001": {"primary_units": {"water_act:s25": _unit(in_corpus=False)}},
        "Q002": {"primary_units": {"wbmo:s5": _unit(body_in_corpus=False)}},
        "Q003": {"primary_units": {"kmc:s3": _unit(body_in_o3=False)}},
        "Q004": {"primary_units": {"fssai:s16": _unit()}},
        "Q005": {"primary_units": {"epa:s7": _unit(body_in_o3=False)}},
    }
    tri = run_em_triage(labels, preanno)
    assert tri["n_evidence_missing"] == 4
    assert tri["class_counts"] == {
        "index_gap": 1,
        "ingest_priority": 1,
        "payload_gap": 1,
        "evidence_present": 1,
    }
    assert tri["classes"]["index_gap"]["qids"] == ["Q001"]
    assert tri["classes"]["ingest_priority"]["qids"] == ["Q002"]
    assert tri["classes"]["payload_gap"]["qids"] == ["Q003"]
    assert tri["classes"]["evidence_present"]["qids"] == ["Q004"]
    assert tri["n_actionable_for_ingestion"] == 2
    assert tri["n_re_retrievable"] == 1
    assert tri["n_label_questionable"] == 1
    # ingestion priority table counts unit states per instrument
    assert tri["ingestion_priority_by_instrument"]["water_act"] == {"index_gap": 1}
    assert tri["ingestion_priority_by_instrument"]["wbmo"] == {"ingest_priority": 1}
    # evidence gaps outside the EM bucket are surfaced (quote-gate risk)
    assert tri["evidence_gaps_under_other_labels"] == [
        {"qid": "Q005", "label": "model_wrong", "absent_units": ["epa:s7"], "states": {"epa:s7": "payload_gap"}}
    ]


# --------------------------------------------------------------------------- #
# Re-audit rule engine
# --------------------------------------------------------------------------- #

class _Q:
    def __init__(self, ref):
        self.acceptable_conclusion = ref


def _reaudit_inputs(
    payload_text, *, label, units, ref, answer, human_correct="true", qid="Q001"
):
    """Minimal question/manifest/payload/preanno/record set for one packet."""
    questions = {qid: _Q(ref)}
    manifest = {
        "questions": {
            qid: {"conditions": {"O3_full_support": {"context_chunk_ids": ["c1"]}}}
        }
    }
    payload_index = {"c1": {"chunk_text": payload_text}}
    preanno = {qid: {"primary_units": units}}
    records = {qid: {"human_correct": human_correct}}
    v2_rows = {qid: {"D2": {"answer": answer}}}
    return questions, manifest, payload_index, preanno, records, v2_rows


def _run_one(
    payload_text, *, label, units, ref, answer, human_correct="true", qid="Q001"
):
    inputs = _reaudit_inputs(
        payload_text,
        label=label,
        units=units,
        ref=ref,
        answer=answer,
        human_correct=human_correct,
        qid=qid,
    )
    questions, manifest, payload_index, preanno, records, v2_rows = inputs
    return run_reaudit(
        {qid: label},
        [qid],
        questions=questions,
        manifest=manifest,
        payload_index=payload_index,
        records=records,
        preanno=preanno,
        v2_rows=v2_rows,
    )


def test_reaudit_r1_model_wrong_with_human_correct_moves_to_reference_narrow():
    payload = "standards of quality safety and other parameters are specified by regulations " * 4
    ra = _run_one(
        payload,
        label="model_wrong",
        units={"fssai:s22": _unit()},
        ref="Standards of quality safety and other parameters are specified by the FSSAI by regulations",
        answer="standards of quality safety and other parameters are specified by regulations " * 3,
    )
    assert ra["changes"] == {"Q001": {"from": "model_wrong", "to": "reference_narrow"}}
    assert ra["examined"][0]["rule"] == "R1_mw_human_correct_answer_grounded"
    assert ra["counts_after"] == {"reference_narrow": 1}


def test_reaudit_r1_does_not_fire_without_human_correct():
    payload = "standards of quality safety and other parameters are specified by regulations " * 4
    ra = _run_one(
        payload,
        label="model_wrong",
        units={"fssai:s22": _unit()},
        ref="Standards of quality safety and other parameters are specified by the FSSAI by regulations",
        answer="standards of quality safety and other parameters are specified by regulations " * 3,
        human_correct="false",
    )
    # out of scope: no contradiction, so not examined at all
    assert ra["changes"] == {}
    assert ra["n_examined"] == 0


def test_reaudit_r2_em_with_reference_in_payload_moves_to_reference_narrow():
    payload = "the fair rent shall be determined by adding to the rent as on twenty years " * 5
    ra = _run_one(
        payload,
        label="evidence_missing",
        units={"wbpt:s16": _unit()},
        ref="the fair rent shall be determined by adding to the rent as on twenty years or more",
        answer="fair rent determined by adding to the rent as on twenty years",
    )
    assert ra["changes"] == {"Q001": {"from": "evidence_missing", "to": "reference_narrow"}}
    assert ra["examined"][0]["rule"] == "R2_em_reference_present_in_payload"


def test_reaudit_r2_does_not_fire_when_a_unit_shows_a_gap():
    payload = "the fair rent shall be determined by adding to the rent as on twenty years " * 5
    ra = _run_one(
        payload,
        label="evidence_missing",
        units={"wbpt:s16": _unit(body_in_o3=False)},
        ref="the fair rent shall be determined by adding to the rent as on twenty years or more",
        answer="fair rent determined by adding to the rent as on twenty years",
    )
    # a qid with a real unit gap is not flagged, so it is never examined
    assert ra["changes"] == {}
    assert ra["n_examined"] == 0


def test_reaudit_upholds_em_when_reference_content_is_absent():
    payload = "unrelated statutory text that shares nothing with the reference conclusion"
    ra = _run_one(
        payload,
        label="evidence_missing",
        units={"epa:s6": _unit()},
        ref="the central government may direct the closure of any industry in an environmental emergency",
        answer="closure may be directed in an environmental emergency",
    )
    # in scope (all primary bodies present) but ref_cov < 0.5 -> label stands
    assert ra["changes"] == {}
    assert ra["examined"][0]["rule"] == "upheld_em_reference_not_in_payload"


def test_reaudit_r3_rn_without_operative_probe_moves_to_evidence_missing():
    # payload carries unrelated text; none of Q049's registered probes appear
    payload = "rules to regulate environmental pollution (1) the central government may make rules"
    ra = _run_one(
        payload,
        qid="Q049",
        label="reference_narrow",
        units={"epa:s6": _unit(body_in_o3=False)},
        ref="the central government may direct the closure of any industry in an environmental emergency",
        answer="the central government may direct closure in an environmental emergency",
    )
    assert "Q049" in RN_RETURNED_PROBES
    assert ra["changes"] == {"Q049": {"from": "reference_narrow", "to": "evidence_missing"}}
    assert ra["examined"][0]["rule"] == "R3_rn_operative_text_absent_from_payload"


def test_reaudit_upholds_returned_rn_when_probe_hits():
    payload = "the corporation may remove any nuisance including noxious trades under section 517"
    ra = _run_one(
        payload,
        qid="Q070",
        label="reference_narrow",
        units={"kmc:s517": _unit()},
        ref="the corporation may remove nuisances that endanger health of adjoining premises",
        answer="the corporation may remove nuisances endangering health of adjoining premises",
    )
    assert ra["changes"] == {}
    assert ra["examined"][0]["rule"] == "upheld_rn_operative_text_present"
    assert "payload-wide" in ra["examined"][0]["rationale"]


def test_reaudit_examines_only_flagged_packets():
    payload = "unrelated statutory text with no reference content whatsoever"
    ra = _run_one(
        payload,
        label="evidence_missing",
        units={"epa:s6": _unit(body_in_o3=False)},
        ref="something entirely absent from this payload about closure of industries",
        answer="answer text",
    )
    # scope = contradictions + evidence_present EM + returned RN; a payload-gap EM
    # with no contradiction is neither flagged nor examined
    assert ra["n_examined"] == 0
    assert ra["changes"] == {}


def test_render_reaudit_md_smoke():
    payload = "standards of quality safety and other parameters are specified by regulations " * 4
    ra = _run_one(
        payload,
        label="model_wrong",
        units={"fssai:s22": _unit()},
        ref="Standards of quality safety and other parameters are specified by the FSSAI by regulations",
        answer="standards of quality safety and other parameters are specified by regulations " * 3,
    )
    md = render_reaudit_md(ra)
    assert "# Step 0 — label re-audit" in md
    assert "R1_mw_human_correct_answer_grounded" in md


def test_renderers_smoke():
    labels = {"Q001": "evidence_missing", "Q002": "model_wrong", "Q003": "model_wrong", "Q004": "model_wrong"}
    records = {"Q001": {"human_correct": "true", "notes": TEMPLATE_NOTES["evidence_missing"]}}
    preanno = {"Q001": {"primary_units": {"fssai:s16": _unit()}}}
    qc = run_qc(labels, RESIDUAL, records, preanno, {})
    md = render_qc_md(qc)
    assert "# Step 0 — Label QC Report" in md
    assert "em_but_evidence_present" in md

    tri = run_em_triage(labels, preanno)
    md2 = render_triage_md(tri)
    assert "# Step 0 — evidence_missing triage" in md2
    assert "Ingestion priority by instrument" in md2
