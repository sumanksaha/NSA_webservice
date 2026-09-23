"""Offline helper tests for Experiment D (no LLM calls, no network).

Importing ``evaluation.experiment_d_reasoning_eval`` pulls in the Experiment
B/C modules, which import matplotlib at module level.  When matplotlib is not
installed in the test environment we inject a minimal stub so the offline
helpers remain testable (the plots phase is never executed in tests).
"""

from __future__ import annotations

import json
import sys
import types

import pytest


def _ensure_matplotlib_stub() -> None:
    try:
        import matplotlib  # noqa: F401
    except ModuleNotFoundError:
        mpl = types.ModuleType("matplotlib")
        mpl.use = lambda *a, **k: None
        pyplot = types.ModuleType("matplotlib.pyplot")

        class _FakeFig:
            def suptitle(self, *a, **k): ...
            def tight_layout(self, *a, **k): ...
            def savefig(self, *a, **k): ...

        def _fake_subplots(*a, **k):
            ax = types.SimpleNamespace(
                bar=lambda *a, **k: None,
                barh=lambda *a, **k: None,
                set_title=lambda *a, **k: None,
                set_xlabel=lambda *a, **k: None,
                set_xticks=lambda *a, **k: None,
                set_xticklabels=lambda *a, **k: None,
                set_yticks=lambda *a, **k: None,
                set_yticklabels=lambda *a, **k: None,
                set_ylim=lambda *a, **k: None,
                text=lambda *a, **k: None,
                scatter=lambda *a, **k: None,
                tick_params=lambda *a, **k: None,
            )
            return (_FakeFig(), [ax] if a and isinstance(a[0], int) and a[0] == 1 else ax)

        pyplot.subplots = _fake_subplots
        pyplot.close = lambda *a, **k: None
        mpl.pyplot = pyplot
        sys.modules["matplotlib"] = mpl
        sys.modules["matplotlib.pyplot"] = pyplot


_ensure_matplotlib_stub()


@pytest.fixture(scope="module")
def d():
    _ensure_matplotlib_stub()
    from evaluation import experiment_d_reasoning_eval as mod

    return mod


# --------------------------------------------------------------------------- #
# extract_json_object
# --------------------------------------------------------------------------- #
def test_extract_json_plain(d):
    obj = d.extract_json_object('{"a": 1, "b": "x"}')
    assert obj == {"a": 1, "b": "x"}


def test_extract_json_with_prose(d):
    obj = d.extract_json_object('Here is the analysis:\n{"a": 1}\nHope that helps!')
    assert obj == {"a": 1}


def test_extract_json_fenced(d):
    text = '```json\n{"a": {"b": [1, 2]}, "c": "}" }\n```'
    assert d.extract_json_object(text) == {"a": {"b": [1, 2]}, "c": "}"}


def test_extract_json_braces_inside_strings(d):
    text = '{"rule": "use the { brace carefully", "n": 2}'
    assert d.extract_json_object(text) == {"rule": "use the { brace carefully", "n": 2}


def test_extract_json_trailing_commas_repaired(d):
    assert d.extract_json_object('{"a": [1, 2,], "b": "x",}') == {"a": [1, 2], "b": "x"}


def test_extract_json_invalid_returns_none(d):
    assert d.extract_json_object("no json here at all") is None
    assert d.extract_json_object("") is None
    assert d.extract_json_object("{broken") is None


# --------------------------------------------------------------------------- #
# Validators + normalizer
# --------------------------------------------------------------------------- #
def _valid_analysis() -> dict:
    return {
        "issue": "Does the requirement apply?",
        "governing_provisions": [{"provision_id": "fssai:s31", "description": "Licensing", "reason": "governs [1]"}],
        "definitions": [{"term": "food business", "definition": "...", "source": "[2]"}],
        "legal_rules": [{"rule": "No person shall operate without a licence", "source": "[1]"}],
        "conditions": [
            {
                "condition": "operating a food business",
                "source": "[1]",
                "status": "satisfied",
                "supporting_fact": "vendor operates [3]",
            }
        ],
        "exceptions_and_provisos": [{"exception": "petty retailer exemption", "source": "[4]", "applicable": False}],
        "facts": [{"fact": "vendor sells food", "source": "question"}],
        "fact_condition_mapping": [
            {"condition": "operating", "fact": "vendor sells food", "determination": "satisfied", "reason": "stated"}
        ],
        "cross_references": [],
        "conflicts_or_hierarchy": [],
        "legal_conclusion": "The licence requirement applies [1].",
        "supporting_evidence": [{"claim": "requirement applies", "source": "[1]"}],
        "uncertainties": [],
    }


def test_validate_reasoning_ok_and_failures(d):
    ok, why = d.validate_reasoning(_valid_analysis())
    assert ok, why
    bad = _valid_analysis()
    bad.pop("legal_conclusion")
    ok2, why2 = d.validate_reasoning(bad)
    assert not ok2
    assert "legal_conclusion" in why2
    ok3, _ = d.validate_reasoning("not a dict")
    assert not ok3


def test_validate_reasoning_payload(d):
    payload = {"structured_analysis": _valid_analysis(), "final_answer": "The requirement applies [1]."}
    ok, why = d.validate_reasoning_payload(payload)
    assert ok, why
    ok2, why2 = d.validate_reasoning_payload({"final_answer": "x"})
    assert not ok2 and "structured_analysis" in why2
    payload["final_answer"] = ""
    ok3, why3 = d.validate_reasoning_payload(payload)
    assert not ok3 and "final_answer" in why3


def test_validate_audit(d):
    ok, _ = d.validate_audit({"status": "PASS"})
    assert ok
    ok2, _ = d.validate_audit({"status": "fail", "defects": []})
    assert ok2
    ok3, why3 = d.validate_audit({"defects": []})
    assert not ok3 and "status" in why3


def test_normalize_audit_defaults_and_defects(d):
    raw = {
        "status": "FAIL",
        "defects": [
            {"type": "missed_exception", "description": "ignored proviso", "severity": "CRITICAL", "evidence": ["[4]"]},
            {"type": "made_up_type", "description": "other", "severity": "huge"},
            "junk",
        ],
        "missing_elements": "proviso analysis",
        "corrected_conclusion": "The exemption applies.",
    }
    n = d.normalize_audit(raw)
    assert n["status"] == "FAIL"
    assert n["defects"][0]["type"] == "other"  # not in the taxonomy -> other
    assert n["defects"][0]["severity"] == "critical"  # lower-cased
    assert n["defects"][1]["type"] == "other"
    assert n["defects"][1]["severity"] == "minor"  # default
    assert n["missing_elements"] == ["proviso analysis"]
    assert n["corrected_conclusion"] == "The exemption applies."


# --------------------------------------------------------------------------- #
# Source-marker collection + citation-marker repair
# --------------------------------------------------------------------------- #
def test_analysis_source_marks(d):
    marks = d.analysis_source_marks(_valid_analysis())
    assert marks == {1, 2, 3, 4}


def test_audit_source_marks(d):
    audit = {
        "status": "FAIL",
        "defects": [{"type": "other", "description": "d", "severity": "major", "evidence": ["[7]", "[2]"]}],
        "required_corrections": ["Address [3]"],
        "citation_corrections": ["Replace [9] with [5]"],
        "corrected_conclusion": "See [5].",
        "missing_elements": [],
    }
    assert d.audit_source_marks(audit) == {7, 2, 3, 9, 5}


def test_repair_citation_markers_appends_when_missing(d):
    analysis = _valid_analysis()
    out = d.repair_citation_markers("The licence requirement applies.", analysis, None, max_index=6)
    assert "[1]" in out and "[2]" in out and "[4]" in out
    assert out.startswith("The licence requirement applies.")


def test_repair_citation_markers_noop_when_present(d):
    analysis = _valid_analysis()
    original = "The licence requirement applies [1]."
    out = d.repair_citation_markers(original, analysis, None, max_index=6)
    assert out == original


def test_repair_citation_markers_clamps_to_max_index(d):
    analysis = {"legal_conclusion": "x [99]", "supporting_evidence": [{"claim": "c", "source": "[2]"}]}
    out = d.repair_citation_markers("x", analysis, None, max_index=6)
    assert "[2]" in out and "[99]" not in out


# --------------------------------------------------------------------------- #
# Transition counting + structured-field identification
# --------------------------------------------------------------------------- #
def test_transition_counts(d):
    pairs = [("q1", False, True), ("q2", True, True), ("q3", True, False)]
    c = d.transition_counts(pairs)
    assert c["improved"] == 1 and c["worsened"] == 1 and c["unchanged"] == 1


def test_identify_structured_fields(d):
    a = _valid_analysis()
    a["exceptions_and_provisos"][0]["applicable"] = True
    a["fact_condition_mapping"].append({"condition": "c2", "fact": "f2", "determination": "unknown", "reason": "r"})
    f = d.identify_structured_fields(a)
    assert f["n_governing_provisions"] == 1
    assert f["n_definitions"] == 1
    assert f["n_conditions"] == 1
    assert f["n_exceptions"] == 1 and f["n_exceptions_applicable"] == 1
    assert f["n_fact_condition_mappings"] == 2
    assert f["n_unknown_determinations"] == 1
    assert f["n_uncertainties"] == 0


# --------------------------------------------------------------------------- #
# D3 final-answer builder (PASS + FAIL paths)
# --------------------------------------------------------------------------- #
def test_build_final_answer_pass_path(d):
    analysis = _valid_analysis()
    audit = d.normalize_audit({"status": "PASS"})
    out = d.build_final_answer(analysis, audit, max_index=6)
    assert "The licence requirement applies" in out
    assert "Corrected conclusion" not in out


def test_build_final_answer_fail_path_applies_corrections(d):
    analysis = _valid_analysis()
    audit = d.normalize_audit({
        "status": "FAIL",
        "required_corrections": ["Consider the petty-retailer exemption"],
        "corrected_conclusion": "The exemption applies; no licence is required.",
    })
    out = d.build_final_answer(analysis, audit, max_index=6)
    # FAIL + corrected_conclusion REPLACES the original (spec sec 7):
    # the answer must not retain the flagged-wrong original conclusion.
    assert out.startswith("The exemption applies; no licence is required.")
    assert "Corrected conclusion (auditor)" not in out
    assert analysis["legal_conclusion"] not in out


def test_build_final_answer_fail_without_correction_keeps_notes(d):
    analysis = _valid_analysis()
    audit = d.normalize_audit({
        "status": "FAIL",
        "required_corrections": ["Consider the petty-retailer exemption"],
        "corrected_conclusion": "",
    })
    out = d.build_final_answer(analysis, audit, max_index=6)
    # No corrected conclusion available: original conclusion + correction notes.
    assert analysis["legal_conclusion"] in out
    assert "Note: Consider the petty-retailer exemption" in out


def test_build_final_answer_includes_uncertainties(d):
    analysis = _valid_analysis()
    analysis["uncertainties"] = ["Evidence does not state the vendor's turnover"]
    audit = d.normalize_audit({"status": "PASS"})
    out = d.build_final_answer(analysis, audit, max_index=6)
    assert "Caveats:" in out and "turnover" in out


# --------------------------------------------------------------------------- #
# Prompt renderers + leakage guard (spec sec 12)
# --------------------------------------------------------------------------- #
def test_rendered_prompts_never_contain_acceptable_conclusion(d):
    """The conclusion text itself must never be in any prompt template output.

    (In the real pipeline the question comes from the benchmark and the
    acceptable_conclusion is only ever used by the offline evaluators; here we
    verify the renderers inject nothing beyond question + context/analysis.)
    """
    conclusion = "CLASSIFIED-GOLD-CONCLUSION-xyzzy"
    ctx = "Legal evidence [1]."
    # The conclusion leaks ONLY if a renderer adds it by itself (it is absent
    # from the question on purpose in this test).
    prompts = [
        d.render_reasoning_prompts("Q?", ctx)[1],
        d.render_answer_prompts("Q?", "{analysis}")[1],
        d.render_audit_prompts("Q?", ctx, "{analysis}")[1],
        d.render_revision_prompts("Q?", "{analysis}", "{audit}")[1],
    ]
    assert all(conclusion.lower() not in p.lower() for p in prompts)
    # The question IS legitimately part of the prompts (benchmark input).
    assert "Q?" in d.render_reasoning_prompts("Q?", ctx)[1]


def test_answer_prompt_does_not_include_context(d):
    """The final-answer generator must see the analysis only (spec sec 4)."""
    sys_p, user_p = d.render_answer_prompts("Q?", '{"legal_conclusion": "x"}')
    assert "Legal evidence sources:" not in user_p
    assert "structured legal analysis" in user_p.lower()


# --------------------------------------------------------------------------- #
# Budget guard (spec sec 8)
# --------------------------------------------------------------------------- #
def test_budget_stop_reports_expected_count(d, capsys):
    d._CALL_COUNT[0] = 0
    d._ACTIVE_CAP[0] = d.PLANNED_CALLS
    rc = d._budget_stop(42)
    out = capsys.readouterr().out
    assert rc == d.BUDGET_STOP_EXIT
    assert "42" in out and "STOP" in out
    d._CALL_COUNT[0] = 0


def test_budget_cap_enforced_in_client(d, monkeypatch):
    client = d._DNoRetryClient.__new__(d._DNoRetryClient)
    client.model = d.LLM_MODEL
    d._CALL_COUNT[0] = d._ACTIVE_CAP[0]  # budget exhausted
    resp = client._real_call("sys", "user", temperature=0.1, max_tokens=10)
    assert resp.error and "budget exhausted" in resp.error
    assert resp.text == ""
    d._CALL_COUNT[0] = 0


# --------------------------------------------------------------------------- #
# Stub client — stage-aware JSON emission
# --------------------------------------------------------------------------- #
def test_stub_client_stage_awareness(d):
    stub = d._StubDClient.__new__(d._StubDClient)
    r1 = stub._real_call(d.REASONING_SYSTEM_PROMPT, "analyse this", temperature=0.1, max_tokens=10)
    payload = d.extract_json_object(r1.text)
    assert payload and d.validate_reasoning_payload(payload)[0]
    r2 = stub._real_call(d.AUDITOR_SYSTEM_PROMPT, "audit this", temperature=0.1, max_tokens=10)
    audit = d.extract_json_object(r2.text)
    assert audit and d.validate_audit(audit)[0]
    r3 = stub._real_call(d.REASONING_SYSTEM_PROMPT, "Auditor findings on that analysis", temperature=0.1, max_tokens=10)
    revised = d.extract_json_object(r3.text)
    assert revised and d.validate_reasoning(revised)[0]
    r4 = stub._real_call("other system", "x", temperature=0.1, max_tokens=10)
    assert "[1]" in r4.text  # answer-style stub text carries a citation marker


# --------------------------------------------------------------------------- #
# Call accounting records (spec sec 9)
# --------------------------------------------------------------------------- #
def test_log_call_shape(d, tmp_path):
    import threading

    calls = tmp_path / "calls.jsonl"
    lock = threading.Lock()
    d._log_call(
        calls,
        lock,
        qid="Q001",
        condition="D2",
        stage="reasoning",
        input_tokens=10,
        output_tokens=20,
        latency_ms=5,
        success=True,
    )
    rec = json.loads(calls.read_text(encoding="utf-8").strip())
    for key in (
        "qid",
        "condition",
        "stage",
        "model",
        "temperature",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "success",
        "revision_count",
    ):
        assert key in rec
    assert rec["model"] == d.LLM_MODEL and rec["temperature"] == d.LLM_TEMPERATURE
    assert rec["stage"] == "reasoning" and rec["success"] is True


def test_log_backoff_marked_separately(d, tmp_path):
    import threading

    calls = tmp_path / "calls.jsonl"
    lock = threading.Lock()
    d._log_backoff(calls, lock, "Q001", "D2", "HTTP 429")
    rec = json.loads(calls.read_text(encoding="utf-8").strip())
    assert rec["kind"] == "transport_backoff" and "stage" not in rec
