"""Tests for the FBO Compliance Auditor Agent (app/auditor/).

Pure seams (severity/context) run with no app, no LLM, no Qdrant. Service
and route seams use stubbed collaborators. Expected values are literals
from docs/FBO_AUDITOR_AGENT_BLUEPRINT.md §3, not recomputed from the code.
"""


# ---------------------------------------------------------------------------
# Severity stratification (pure)
# ---------------------------------------------------------------------------


def test_critical_fields_stratify_critical():
    from app.auditor.severity import severity_of

    assert severity_of("Pest_report") == "critical"
    assert severity_of("Water_report") == "critical"
    assert severity_of("refrigerator_clean") == "critical"
    assert severity_of("Expired_item") == "critical"


def test_other_fields_stratify_major():
    from app.auditor.severity import severity_of

    assert severity_of("clean_premise") == "major"
    assert severity_of("proper_attire") == "major"
    assert severity_of("unknown_field") == "major"


def test_stratify_counts_by_severity():
    from app.auditor.severity import stratify

    shortcomings = [
        {"item": "pest_report", "observation": "pest intrusion near flour storage"},
        {"item": "temperature_logging", "observation": "fridge at 11C"},
        {"item": "water_report", "observation": "no NABL report available"},
    ]
    counts = stratify(shortcomings)
    assert counts["critical"] == 2  # pest_report + water_report (field-inferred)
    assert counts["major"] == 1  # temperature_logging (no inference rule)
    assert counts["minor"] == 0


def test_stratify_honours_explicit_severity():
    from app.auditor.severity import stratify

    counts = stratify([{"item": "x", "observation": "y", "severity": "critical"}])
    assert counts == {"critical": 1, "major": 0, "minor": 0}


# ---------------------------------------------------------------------------
# FBO audit context builder (pure)
# ---------------------------------------------------------------------------


def _violations():
    return [
        {
            "title": "Pest Control Report Missing",
            "observation": "No pest-control record was produced.",
            "field": "Pest_report",
        },
        {
            "title": "Water Test Report Missing",
            "observation": "Potable-water safety unverified.",
            "field": "Water_report",
        },
    ]


def test_build_shortcomings_carries_severity():
    from app.auditor.context import build_shortcomings

    shortcomings = build_shortcomings(_violations())
    assert shortcomings == [
        {
            "item": "Pest_report",
            "observation": "No pest-control record was produced.",
            "severity": "critical",
        },
        {
            "item": "Water_report",
            "observation": "Potable-water safety unverified.",
            "severity": "critical",
        },
    ]


def test_build_fbo_context_shape():
    from app.auditor.context import build_fbo_context

    ctx = build_fbo_context(
        fbo_name="Sunrise Bakers",
        business_type="Bakery / Food Manufacturer",
        scale="Small Enterprise",
        compliance_deadline_days=15,
        violations=_violations(),
    )
    assert ctx["fbo_name"] == "Sunrise Bakers"
    assert ctx["business_type"] == "Bakery / Food Manufacturer"
    assert ctx["scale"] == "Small Enterprise"
    assert ctx["compliance_deadline_days"] == 15
    assert len(ctx["shortcomings"]) == 2
    assert {s["severity"] for s in ctx["shortcomings"]} == {"critical"}


def test_build_fbo_context_defaults():
    from app.auditor.context import build_fbo_context

    ctx = build_fbo_context(violations=_violations())
    assert ctx["business_type"] == "General Food Establishment"
    assert ctx["scale"] == "Standard FBO"
    assert ctx["compliance_deadline_days"] == 15


# ---------------------------------------------------------------------------
# Regulation search adapter (RAG bridge, best-effort)
# ---------------------------------------------------------------------------


def test_search_adapter_maps_chunks_to_evidence(monkeypatch):
    import app.rag.tasks as tasks
    from app.auditor.search_adapter import search_regulations

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [
                {
                    "chunk_id": "c1",
                    "text": "Schedule 4 hygiene requirements",
                    "section_number": "56",
                    "document_title": "FSS Act",
                }
            ]
        },
    )
    evidence = search_regulations("pest control hygiene", top_k=3)
    assert evidence == [{"citation": "FSS Act §56", "text": "Schedule 4 hygiene requirements"}]


def test_search_adapter_degrades_to_empty_on_rag_failure(monkeypatch):
    import app.rag.tasks as tasks
    from app.auditor.search_adapter import search_regulations

    def boom(query, **kw):
        raise RuntimeError("Qdrant down")

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", boom)
    assert search_regulations("pest control") == []


# ---------------------------------------------------------------------------
# FBOAuditorAgent service (stubbed LLM)
# ---------------------------------------------------------------------------


class _FakeAI:
    """Stubbed LLM surface: complete_json only."""

    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls: list[dict] = []

    def is_enabled(self):
        return True

    def complete_json(self, system_prompt, user_prompt, **kw):
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.exc is not None:
            raise self.exc
        return self.payload


def _fbo_context():
    from app.auditor.context import build_fbo_context

    return build_fbo_context(
        fbo_name="Sunrise Bakers",
        business_type="Bakery / Food Manufacturer",
        violations=_violations(),
    )


def test_agent_rejects_empty_shortcomings():
    from app.auditor.service import FBOAuditorAgent

    out = FBOAuditorAgent(ai_service=_FakeAI()).generate_remediation_plan({"shortcomings": []})
    assert out["error"] == "No shortcomings provided for audit plan generation."


def test_agent_builds_grounded_prompt_and_returns_plan():
    from app.auditor.service import FBOAuditorAgent

    plan = {"plan_id": "CAPA-1", "remediation_phases": [], "dossier_checklist_for_fso": []}
    fake = _FakeAI(payload=plan)
    agent = FBOAuditorAgent(ai_service=fake)
    out = agent.generate_remediation_plan(
        _fbo_context(),
        regulatory_evidence=[{"citation": "FSS Act §56", "text": "hygiene rules"}],
    )
    assert out["plan_id"] == "CAPA-1"
    assert out["remediation_phases"] == []
    assert out["dossier_checklist_for_fso"] == []
    prompt = fake.calls[0]["user_prompt"]
    assert "Sunrise Bakers" in prompt
    assert "FSS Act §56" in prompt
    assert "Lead Food Safety Auditor" in fake.calls[0]["system_prompt"]


def test_agent_fail_closed_on_unparseable_llm():
    from app.auditor.service import FBOAuditorAgent

    fake = _FakeAI(exc=ValueError("AI did not return valid JSON"))
    out = FBOAuditorAgent(ai_service=fake).generate_remediation_plan(_fbo_context())
    assert out["error"] == "Failed to parse structured audit plan."


def test_agent_raises_when_kill_switch_off(monkeypatch):
    import pytest

    from app.auditor.service import FBOAuditorAgent
    from app.shared.config import cfg

    monkeypatch.setattr(cfg, "auditor_enabled", False, raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        FBOAuditorAgent(ai_service=_FakeAI()).generate_remediation_plan(_fbo_context())


# ---------------------------------------------------------------------------
# Routes (Flask test client, stubbed agent)
# ---------------------------------------------------------------------------


def _setup_test_env():
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    user = User(username="auditoruser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
    return app, client, ctx


def _make_inspection(db, inspection_cls, checklist):
    import json
    from datetime import UTC, datetime

    inspection = inspection_cls(
        inspection_code="AUD-001",
        fso_name="Test Officer",
        fbo_name="Sunrise Bakers",
        concerned_food="Bakery",
        inspection_date=datetime.now(UTC),
        compliance_deadline=datetime.now(UTC),
        checklist_json=json.dumps(checklist),
    )
    db.session.add(inspection)
    db.session.commit()
    return inspection


def test_plan_api_rejects_empty_shortcomings():
    _app, client, ctx = _setup_test_env()
    try:
        resp = client.post("/auditor/plan", json={"shortcomings": []})
        assert resp.status_code == 400
    finally:
        ctx.pop()


def test_plan_api_503_when_kill_switch_off():
    app, client, ctx = _setup_test_env()
    try:
        app.config["AUDITOR_AI_ENABLED"] = False
        resp = client.post("/auditor/plan", json={"shortcomings": [{"item": "x"}]})
        assert resp.status_code == 503
    finally:
        ctx.pop()


def test_plan_api_returns_stubbed_plan(monkeypatch):
    import app.auditor.service as auditor_service

    _app, client, ctx = _setup_test_env()
    try:
        monkeypatch.setattr(
            auditor_service.FBOAuditorAgent,
            "generate_remediation_plan",
            lambda self, fbo_context, regulatory_evidence=None: {"plan_id": "CAPA-STUB"},
        )
        monkeypatch.setattr(
            "app.auditor.search_adapter.search_regulations",
            lambda query, top_k=3: [{"citation": "FSS Act §56", "text": "rules"}],
        )
        resp = client.post(
            "/auditor/plan",
            json={"fbo_name": "Sunrise Bakers", "shortcomings": [{"item": "Pest_report"}]},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["plan"]["plan_id"] == "CAPA-STUB"
    finally:
        ctx.pop()


def test_inspection_trigger_persists_plan(monkeypatch):
    import app.auditor.service as auditor_service
    from app.extensions import db
    from app.models import Inspection

    _app, client, ctx = _setup_test_env()
    try:
        inspection = _make_inspection(
            db, Inspection, {"Pest_report": "no", "Water_report": "no", "clean_premise": "yes"}
        )
        monkeypatch.setattr(
            auditor_service.FBOAuditorAgent,
            "generate_remediation_plan",
            lambda self, fbo_context, regulatory_evidence=None: {"plan_id": "CAPA-TRIG"},
        )
        resp = client.post(f"/inspection/{inspection.id}/auditor-plan")
        assert resp.status_code == 200
        assert resp.get_json()["plan"]["plan_id"] == "CAPA-TRIG"
        db.session.refresh(inspection)
        assert "CAPA-TRIG" in (inspection.auditor_plan_json or "")
    finally:
        ctx.pop()


def test_inspection_plan_view_renders_phases(monkeypatch):
    import json

    from app.extensions import db
    from app.models import Inspection

    _app, client, ctx = _setup_test_env()
    try:
        inspection = _make_inspection(db, Inspection, {"Pest_report": "no"})
        inspection.auditor_plan_json = json.dumps({
            "plan_id": "CAPA-VIEW",
            "remediation_phases": [{"phase": "Phase 1: Immediate Containment (Days 0-2)", "actions": []}],
            "dossier_checklist_for_fso": ["NABL water test report"],
        })
        db.session.commit()
        resp = client.get(f"/inspection/{inspection.id}/auditor-plan")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "CAPA-VIEW" in body
        assert "Phase 1: Immediate Containment" in body
        assert "NABL water test report" in body
    finally:
        ctx.pop()


def test_inspection_plan_view_404_without_plan():
    from app.extensions import db
    from app.models import Inspection

    _app, client, ctx = _setup_test_env()
    try:
        inspection = _make_inspection(db, Inspection, {"Pest_report": "no"})
        resp = client.get(f"/inspection/{inspection.id}/auditor-plan")
        assert resp.status_code == 404
    finally:
        ctx.pop()


def test_inspection_trigger_no_violations():
    from app.extensions import db
    from app.models import Inspection

    _app, client, ctx = _setup_test_env()
    try:
        inspection = _make_inspection(db, Inspection, {"clean_premise": "yes"})
        resp = client.post(f"/inspection/{inspection.id}/auditor-plan")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "no_violations"
    finally:
        ctx.pop()


def test_verify_closure_marks_corrective_measures():
    from datetime import datetime

    from app.extensions import db
    from app.models import Inspection

    _app, client, ctx = _setup_test_env()
    try:
        inspection = _make_inspection(db, Inspection, {"Pest_report": "no"})
        resp = client.post(f"/inspection/{inspection.id}/verify-closure")
        assert resp.status_code in (302, 303)
        db.session.refresh(inspection)
        assert inspection.is_dismissed is True
        assert inspection.dossier_verified is True
        assert inspection.dismissed_by is not None
        assert isinstance(inspection.dismissed_at, datetime)
    finally:
        ctx.pop()
