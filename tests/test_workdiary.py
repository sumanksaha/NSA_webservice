"""Tests for the Work Diary feature (app/workdiary/).

Covers:
- Purpose derivation (problem recorded -> Complaint, else Routine Inspection)
- Diary row shaping (date / place of visit / purpose / activity)
- Filtering: per-FSO, date range (inclusive), purpose
- HTTP endpoints: index table, print preview, PDF download (success + failure)
- Auth gate: unauthenticated access redirects to login
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.workdiary.engine import PURPOSE_COMPLAINT, PURPOSE_ROUTINE, WorkDiaryEngine

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def env():
    """App + logged-in client + in-memory schema with two FSOs."""
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

    user = User(username="diaryuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    db.session.add(user)
    db.session.add(FSO(fso_name="Officer A"))
    db.session.add(FSO(fso_name="Officer B"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)  # Flask-Login key

    yield app, client

    from app.extensions import db

    db.session.remove()
    db.drop_all()
    ctx.pop()


def _make_inspection(
    code: str,
    fso_name: str,
    day: int,
    month: int = 3,
    year: int = 2026,
    fbo_name: str | None = "Sweet Shop",
    fbo_address: str | None = "12 MG Road",
    problem: str | None = None,
    visit_purpose: str | None = None,
) -> None:
    from app.extensions import db
    from app.models import Inspection

    db.session.add(
        Inspection(
            inspection_code=code,
            fso_name=fso_name,
            fbo_name=fbo_name,
            fbo_address=fbo_address,
            problem=problem,
            visit_purpose=visit_purpose,
            inspection_date=datetime(year, month, day, 10, 30),
            compliance_deadline=datetime(year, month, day, 0, 0),
            is_dismissed=False,
        )
    )
    db.session.commit()


# --------------------------------------------------------------------------- #
# Engine: purpose derivation + row shaping
# --------------------------------------------------------------------------- #


class TestPurposeDerivation:
    def test_problem_present_is_complaint(self):
        assert WorkDiaryEngine.derive_purpose("Adulteration suspected") == PURPOSE_COMPLAINT

    def test_whitespace_only_problem_is_routine(self):
        assert WorkDiaryEngine.derive_purpose("   ") == PURPOSE_ROUTINE

    def test_no_problem_is_routine(self):
        assert WorkDiaryEngine.derive_purpose(None) == PURPOSE_ROUTINE

    def test_explicit_complaint_overrides_heuristic(self):
        """FSO picked "complaint" even though no problem text was recorded."""
        assert WorkDiaryEngine.derive_purpose(None, "complaint") == PURPOSE_COMPLAINT
        assert WorkDiaryEngine.derive_purpose("", "complaint") == PURPOSE_COMPLAINT

    def test_explicit_routine_overrides_problem_text(self):
        """FSO picked "routine" — problem notes must not flip it to complaint."""
        assert WorkDiaryEngine.derive_purpose("Minor labelling issue", "routine") == PURPOSE_ROUTINE

    def test_unknown_visit_purpose_falls_back_to_heuristic(self):
        assert WorkDiaryEngine.derive_purpose(None, "typo") == PURPOSE_ROUTINE
        assert WorkDiaryEngine.derive_purpose("x", "typo") == PURPOSE_COMPLAINT

    def test_purposes_are_closed_vocabulary(self):
        engine = WorkDiaryEngine()
        for problem in (None, "", "x"):
            for vp in (None, "routine", "complaint", "other"):
                assert engine.derive_purpose(problem, vp) in (PURPOSE_ROUTINE, PURPOSE_COMPLAINT)


class TestRowShaping:
    def test_entry_fields(self, env):
        _make_inspection("INSP-WD-1", "Officer A", 5, problem=None)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert len(entries) == 1
        e = entries[0]
        assert e["inspection_code"] == "INSP-WD-1"
        assert e["date"] == datetime(2026, 3, 5, 10, 30)
        assert e["place_of_visit"] == "12 MG Road"
        assert e["purpose"] == PURPOSE_ROUTINE
        assert e["activity"] == "Routine inspection of Sweet Shop"

    def test_place_falls_back_to_fbo_name_then_dash(self, env):
        _make_inspection("INSP-WD-2", "Officer A", 6, fbo_address=None, fbo_name="Kiosk")
        _make_inspection("INSP-WD-3", "Officer A", 7, fbo_address=None, fbo_name=None)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert [e["place_of_visit"] for e in entries] == ["Kiosk", "\u2014"]

    def test_complaint_activity_includes_problem(self, env):
        _make_inspection("INSP-WD-4", "Officer B", 8, problem="Milk adulteration")
        e = WorkDiaryEngine().build_entries(fso_name="Officer B")[0]
        assert e["purpose"] == PURPOSE_COMPLAINT
        assert "Milk adulteration" in e["activity"]

    def test_sorted_by_date_oldest_first(self, env):
        _make_inspection("INSP-WD-B", "Officer A", 20)
        _make_inspection("INSP-WD-A", "Officer A", 10)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert [e["inspection_code"] for e in entries] == ["INSP-WD-A", "INSP-WD-B"]


# --------------------------------------------------------------------------- #
# Engine: filters
# --------------------------------------------------------------------------- #


class TestFilters:
    def test_per_fso_filtering(self, env):
        _make_inspection("INSP-WD-10", "Officer A", 1)
        _make_inspection("INSP-WD-11", "Officer B", 2)
        a = WorkDiaryEngine().build_entries(fso_name="Officer A")
        b = WorkDiaryEngine().build_entries(fso_name="Officer B")
        assert [e["fso_name"] for e in a] == ["Officer A"]
        assert [e["fso_name"] for e in b] == ["Officer B"]
        assert len(WorkDiaryEngine().build_entries()) == 2

    def test_date_range_inclusive(self, env):
        _make_inspection("INSP-WD-20", "Officer A", 1)
        _make_inspection("INSP-WD-21", "Officer A", 15)
        _make_inspection("INSP-WD-22", "Officer A", 28)
        entries = WorkDiaryEngine().build_entries(date_from="2026-03-01", date_to="2026-03-15")
        assert [e["inspection_code"] for e in entries] == ["INSP-WD-20", "INSP-WD-21"]

    def test_open_ended_ranges(self, env):
        _make_inspection("INSP-WD-30", "Officer A", 1, month=1)
        _make_inspection("INSP-WD-31", "Officer A", 1, month=4)
        assert len(WorkDiaryEngine().build_entries(date_from="2026-03-01")) == 1
        assert len(WorkDiaryEngine().build_entries(date_to="2026-03-01")) == 1

    def test_purpose_filter_routine_vs_complaint(self, env):
        _make_inspection("INSP-WD-40", "Officer A", 1, problem="Complaint text")
        _make_inspection("INSP-WD-41", "Officer A", 2, problem=None)
        complaints = WorkDiaryEngine().build_entries(purpose="complaint")
        routines = WorkDiaryEngine().build_entries(purpose="routine")
        all_rows = WorkDiaryEngine().build_entries(purpose=None)
        assert [e["purpose"] for e in complaints] == [PURPOSE_COMPLAINT]
        assert [e["purpose"] for e in routines] == [PURPOSE_ROUTINE]
        assert len(all_rows) == 2

    def test_combined_fso_and_purpose(self, env):
        _make_inspection("INSP-WD-50", "Officer A", 1, problem="c1")
        _make_inspection("INSP-WD-51", "Officer A", 2)
        _make_inspection("INSP-WD-52", "Officer B", 3, problem="c2")
        rows = WorkDiaryEngine().build_entries(fso_name="Officer A", purpose="complaint")
        assert [e["inspection_code"] for e in rows] == ["INSP-WD-50"]

    def test_explicit_visit_purpose_drives_filter(self, env):
        """Explicit picks are honoured even when they contradict the problem text."""
        _make_inspection("INSP-WD-53", "Officer A", 4, visit_purpose="complaint")
        _make_inspection("INSP-WD-54", "Officer A", 5, problem="note", visit_purpose="routine")
        _make_inspection("INSP-WD-55", "Officer A", 6)  # legacy NULL -> heuristic
        complaints = WorkDiaryEngine().build_entries(purpose="complaint")
        routines = WorkDiaryEngine().build_entries(purpose="routine")
        assert [e["inspection_code"] for e in complaints] == ["INSP-WD-53"]
        assert [e["inspection_code"] for e in routines] == ["INSP-WD-54", "INSP-WD-55"]


# --------------------------------------------------------------------------- #
# HTTP endpoints
# --------------------------------------------------------------------------- #


class TestRoutes:
    def test_index_lists_entries_and_filters(self, env):
        _, client = env
        _make_inspection("INSP-WD-60", "Officer A", 9, problem="Rotten stock")
        resp = client.get("/workdiary/", query_string={"fso_name": "Officer A"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "12 MG Road" in body
        assert "Routine Inspection" in body or "Complaint" in body

    def test_index_empty_state(self, env):
        _, client = env
        resp = client.get("/workdiary/")
        assert resp.status_code == 200
        assert "No inspections match" in resp.get_data(as_text=True)

    def test_index_rejects_unknown_fso_gracefully(self, env):
        _, client = env
        resp = client.get("/workdiary/", query_string={"fso_name": "Ghost Officer"})
        assert resp.status_code == 200
        assert "No inspections match" in resp.get_data(as_text=True)

    def test_preview_renders_official_report(self, env):
        _, client = env
        _make_inspection("INSP-WD-70", "Officer B", 11, fbo_address="5 Park St")
        resp = client.get("/workdiary/preview", query_string={"fso_name": "Officer B"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Official template markers (FSO_Work_Diary_Template.html)
        assert "Work Diary of Food Safety Officer (FSO)" in body
        assert "(i)" in body and "(iv)" in body  # roman-numeral column headers
        assert "Place of Posting" in body
        assert "Area of Jurisdiction" in body
        assert "Signature of Food Safety Officer (FSO)" in body
        assert "Countersigned" in body and "Designated Officer (DO)" in body
        assert "5 Park St" in body

    def test_report_pads_to_minimum_rows(self, env):
        _, client = env
        _make_inspection("INSP-WD-71", "Officer A", 12)
        resp = client.get("/workdiary/preview", query_string={"fso_name": "Officer A"})
        body = resp.get_data(as_text=True)
        assert 'class="empty-row"' in body  # blank rows keep the printed form height

    def test_create_route_persists_visit_purpose(self, env):
        """The inspection entry form's Visit Purpose pick lands in the DB."""
        _, client = env
        payload = {
            "food_safety_officer_name": "Officer A",
            "inspection_date": "2026-03-20",
            "visit_purpose": "complaint",
            "problem": "Complaint received",
        }
        resp = client.post("/inspection/create", data=payload)
        assert resp.status_code == 201, resp.get_data(as_text=True)

        from app.extensions import db
        from app.models import Inspection

        insp = db.session.query(Inspection).filter_by(inspection_code=resp.get_json()["inspection_code"]).one()
        assert insp.visit_purpose == "complaint"

        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert [e["purpose"] for e in entries] == [PURPOSE_COMPLAINT]

    def test_create_route_rejects_invalid_purpose(self, env):
        _, client = env
        payload = {
            "food_safety_officer_name": "Officer A",
            "inspection_date": "2026-03-21",
            "visit_purpose": "surprise-visit",
        }
        resp = client.post("/inspection/create", data=payload)
        assert resp.status_code == 400
        assert "visit_purpose" in resp.get_json()["error"]

    def test_pdf_download_success(self, env, monkeypatch):
        from app.workdiary import routes as wd_routes

        _make_inspection("INSP-WD-80", "Officer A", 12)
        monkeypatch.setattr(wd_routes, "generate_pdf_from_html", lambda html: (b"%PDF-fake-bytes", None))
        _, client = env
        resp = client.get("/workdiary/pdf", query_string={"fso_name": "Officer A"})
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert "filename=workdiary_Officer_A.pdf" in resp.headers["Content-Disposition"]
        assert resp.data.startswith(b"%PDF")

    def test_pdf_filename_sanitized(self):
        from app.workdiary.routes import _pdf_filename

        assert (
            _pdf_filename({"fso_name": "Officer A/B x", "date_from": "2026-03-01"})
            == "workdiary_Officer_A_B_x_2026-03-01.pdf"
        )

    def test_pdf_failure_returns_503(self, env, monkeypatch):
        from app.workdiary import routes as wd_routes

        monkeypatch.setattr(wd_routes, "generate_pdf_from_html", lambda html: (None, "weasyprint missing"))
        _, client = env
        resp = client.get("/workdiary/pdf")
        assert resp.status_code == 503
        assert resp.get_json()["error"].startswith("PDF generation failed")

    def test_requires_login(self, env):
        app, _client = env
        anon = app.test_client()
        resp = anon.get("/workdiary/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]
