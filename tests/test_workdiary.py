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
    concerned_food: str | None = None,
    fssai_license: str | None = None,
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
            concerned_food=concerned_food,
            fssai_license=fssai_license,
            inspection_date=datetime(year, month, day, 10, 30),
            compliance_deadline=datetime(year, month, day, 0, 0),
            is_dismissed=False,
        )
    )
    db.session.commit()


def _make_diary_day(
    fso_name: str,
    day: int,
    activity: str,
    month: int = 3,
    year: int = 2026,
    place: str = "",
    premises: int = 0,
    samples: int = 0,
    notes: str = "",
    duty_seq: int = 1,
) -> None:
    """One Monthly Diary row written directly (bypasses the grid POST)."""
    from app.diary import _summary_line
    from app.extensions import db
    from app.models import WorkDiaryEntry

    work_date = f"{year:04d}-{month:02d}-{day:02d}"
    db.session.add(
        WorkDiaryEntry(
            fso_name=fso_name,
            work_date=work_date,
            duty_seq=duty_seq,
            activity=activity,
            premises=premises,
            samples=samples,
            notes=notes,
            place_of_visit=place or None,
            summary=_summary_line(work_date, activity, premises, samples, notes, place),
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
        assert "12 MG Road" in e["place_of_visit"]
        assert "Sweet Shop" in e["place_of_visit"]
        assert e["purpose"] == PURPOSE_ROUTINE
        assert "Routine inspection of Sweet Shop" in e["activity"]

    def test_place_includes_license(self, env):
        _make_inspection("INSP-WD-LIC", "Officer A", 5)
        from app.extensions import db
        from app.models import Inspection

        insp = db.session.query(Inspection).filter_by(inspection_code="INSP-WD-LIC").one()
        insp.fssai_license = "FSSAI-12345"
        db.session.commit()
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert "License: FSSAI-12345" in entries[0]["place_of_visit"]

    def test_place_falls_back_to_fbo_name_then_dash(self, env):
        _make_inspection("INSP-WD-2", "Officer A", 6, fbo_address=None, fbo_name="Kiosk")
        _make_inspection("INSP-WD-3", "Officer A", 7, fbo_address=None, fbo_name=None)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        place_texts = [e["place_of_visit"].replace("<br>", " ") for e in entries]
        assert "Kiosk" in place_texts[0]
        assert "\u2014" in place_texts[1]

    def test_complaint_activity_includes_problem(self, env):
        _make_inspection("INSP-WD-4", "Officer B", 8, problem="Milk adulteration")
        e = WorkDiaryEngine().build_entries(fso_name="Officer B")[0]
        assert e["purpose"] == PURPOSE_COMPLAINT
        assert "Milk adulteration" in e["activity"]
        assert "Enquiry into complaint" in e["activity"]

    def test_activity_includes_concerned_food(self, env):
        _make_inspection("INSP-WD-FOOD", "Officer A", 9, fbo_name="Dairy Farm")
        from app.extensions import db
        from app.models import Inspection

        insp = db.session.query(Inspection).filter_by(inspection_code="INSP-WD-FOOD").one()
        insp.concerned_food = "Milk"
        db.session.commit()
        e = WorkDiaryEngine().build_entries(fso_name="Officer A")[0]
        assert "(Milk)" in e["activity"]

    def test_activity_includes_notice_date(self, env):
        from datetime import datetime as dt

        _make_inspection("INSP-WD-NOTICE", "Officer A", 10, fbo_name="Café")
        from app.extensions import db
        from app.models import Inspection

        insp = db.session.query(Inspection).filter_by(inspection_code="INSP-WD-NOTICE").one()
        insp.notice_issued_at = dt(2026, 3, 10, 14, 0)
        db.session.commit()
        e = WorkDiaryEngine().build_entries(fso_name="Officer A")[0]
        assert "Notice issued: 10-03-2026" in e["activity"]

    def test_sorted_by_date_oldest_first(self, env):
        _make_inspection("INSP-WD-B", "Officer A", 20)
        _make_inspection("INSP-WD-A", "Officer A", 10)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert [e["inspection_code"] for e in entries] == ["INSP-WD-A", "INSP-WD-B"]


class TestDateGrouping:
    def test_same_date_entries_are_grouped(self, env):
        _make_inspection("INSP-WD-G1", "Officer A", 5, fbo_name="Shop A")
        _make_inspection("INSP-WD-G2", "Officer A", 5, fbo_name="Shop B")
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert len(entries) == 2
        assert entries[0]["is_first_in_date"] is True
        assert entries[0]["date_rowspan"] == 2
        assert entries[1]["is_first_in_date"] is False
        assert entries[1]["date_rowspan"] == 0

    def test_different_dates_get_separate_groups(self, env):
        _make_inspection("INSP-WD-G3", "Officer A", 5, fbo_name="Shop A")
        _make_inspection("INSP-WD-G4", "Officer A", 6, fbo_name="Shop B")
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert entries[0]["is_first_in_date"] is True
        assert entries[0]["date_rowspan"] == 1
        assert entries[1]["is_first_in_date"] is True
        assert entries[1]["date_rowspan"] == 1

    def test_single_entry_gets_rowspan_1(self, env):
        _make_inspection("INSP-WD-G5", "Officer A", 5)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert entries[0]["is_first_in_date"] is True
        assert entries[0]["date_rowspan"] == 1

    def test_three_same_date_entries(self, env):
        _make_inspection("INSP-WD-G6", "Officer A", 5, fbo_name="A")
        _make_inspection("INSP-WD-G7", "Officer A", 5, fbo_name="B")
        _make_inspection("INSP-WD-G8", "Officer A", 5, fbo_name="C")
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert entries[0]["date_rowspan"] == 3
        assert entries[1]["date_rowspan"] == 0
        assert entries[2]["date_rowspan"] == 0


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
# Engine: Monthly Diary union
# --------------------------------------------------------------------------- #


class TestMonthlyUnion:
    def test_monthly_rows_appear_with_place_purpose_activity(self, env):
        from app.workdiary.engine import PURPOSE_INSPECTION, PURPOSE_MEETING, PURPOSE_VVIP

        _make_diary_day("Officer A", 5, "vvip", place="Town Hall", premises=2, samples=1, notes="duty")
        _make_diary_day("Officer A", 6, "meeting", place="HQ Room 2")
        _make_diary_day("Officer A", 7, "field", place="Market", premises=3, samples=2)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        by_day = {e["date"].strftime("%d"): e for e in entries}
        assert by_day["05"]["purpose"] == PURPOSE_VVIP
        assert by_day["05"]["place_of_visit"] == "Town Hall"
        assert by_day["05"]["activity"] == (
            "VVIP duty at Town Hall. Inspected 2 premises, collected 1 sample(s). Remarks: duty"
        )
        assert by_day["06"]["purpose"] == PURPOSE_MEETING
        assert by_day["06"]["activity"] == "Meeting at HQ Room 2."
        assert by_day["07"]["purpose"] == PURPOSE_INSPECTION
        assert "On 2026" not in by_day["07"]["activity"]  # no date prefix at print

    def test_holiday_and_leave_are_skipped_in_print(self, env):
        _make_diary_day("Officer A", 8, "holiday", place="Home")
        _make_diary_day("Officer A", 9, "leave")
        assert WorkDiaryEngine().build_entries(fso_name="Officer A") == []

    def test_inspections_sort_first_within_a_date(self, env):
        _make_inspection("INSP-WD-90", "Officer A", 5)
        _make_diary_day("Officer A", 5, "field", place="Market")
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A")
        assert [e["inspection_code"] for e in entries] == ["INSP-WD-90", ""]
        assert entries[0]["is_first_in_date"] and entries[0]["date_rowspan"] == 2

    def test_monthly_rows_are_fso_scoped(self, env):
        _make_diary_day("Officer A", 5, "vvip", place="Town Hall")
        assert WorkDiaryEngine().build_entries(fso_name="Officer B") == []
        # "" is the deny-by-default sentinel for unbound non-admins: it must
        # match nothing, not everything.
        assert WorkDiaryEngine().build_entries(fso_name="") == []

    def test_monthly_purpose_filters(self, env):
        from app.workdiary.engine import PURPOSE_INSPECTION, PURPOSE_MEETING, PURPOSE_VVIP

        _make_diary_day("Officer A", 5, "vvip", place="Town Hall")
        _make_diary_day("Officer A", 6, "meeting", place="HQ")
        _make_diary_day("Officer A", 7, "field", place="Market")
        _make_inspection("INSP-WD-91", "Officer A", 8, problem="c")
        assert [e["purpose"] for e in WorkDiaryEngine().build_entries(purpose="vvip")] == [PURPOSE_VVIP]
        assert [e["purpose"] for e in WorkDiaryEngine().build_entries(purpose="meeting")] == [PURPOSE_MEETING]
        assert [e["purpose"] for e in WorkDiaryEngine().build_entries(purpose="complaint")] == [PURPOSE_COMPLAINT]
        assert [e["purpose"] for e in WorkDiaryEngine().build_entries(purpose="inspection")] == [PURPOSE_INSPECTION]
        # "routine" stays a working alias of "inspection" for old URLs.
        assert [e["purpose"] for e in WorkDiaryEngine().build_entries(purpose="routine")] == [PURPOSE_INSPECTION]


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
        """Preview is the Monthly-only official report (inspections excluded)."""
        _, client = env
        _make_diary_day("Officer B", 11, "field", place="5 Park St", premises=2, samples=1)
        _make_inspection("INSP-WD-70", "Officer B", 11, fbo_address="Inspection Addr")
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
        assert "Inspection Addr" not in body

    def test_preview_monthly_only_excludes_inspections(self, env):
        """PDF buildup contract: preview never merges Inspection rows."""
        from app.workdiary.engine import WorkDiaryEngine

        _make_inspection("INSP-WD-70B", "Officer B", 11, fbo_address="5 Park St")
        assert WorkDiaryEngine().build_entries(fso_name="Officer B", include_inspections=False) == []
        assert len(WorkDiaryEngine().build_entries(fso_name="Officer B")) == 1

    def test_preview_drops_complaint_filter(self, env):
        """No Monthly row carries Complaint purpose: preview with
        purpose=complaint renders the unfiltered monthly report with an
        explicit notice — never a blank signed document, never silent."""
        _, client = env
        _make_diary_day("Officer B", 11, "field", place="Monthly Market")
        _make_inspection("INSP-WD-70C", "Officer B", 11, problem="adulteration")
        resp = client.get(
            "/workdiary/preview", query_string={"fso_name": "Officer B", "purpose": "complaint"}
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Monthly Market" in body
        assert "official report is built from the Monthly Diary" in body

    def test_preview_without_complaint_filter_has_no_notice(self, env):
        _, client = env
        _make_diary_day("Officer B", 11, "field", place="Monthly Market")
        resp = client.get("/workdiary/preview", query_string={"fso_name": "Officer B"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Monthly Market" in body
        assert "official report is built from the Monthly Diary" not in body

    def test_split_duty_day_renders_two_rows_with_merged_date(self, env):
        """A split-duty day is two rows; Date merges, Place/Purpose/Activity stay separate."""
        _make_diary_day("Officer A", 5, "field", place="Market", premises=3, samples=2)
        _make_diary_day("Officer A", 5, "vvip", place="Town Hall", duty_seq=2)
        entries = WorkDiaryEngine().build_entries(fso_name="Officer A", include_inspections=False)
        assert len(entries) == 2
        assert entries[0]["is_first_in_date"] is True
        assert entries[0]["date_rowspan"] == 2
        assert entries[1]["is_first_in_date"] is False
        assert entries[0]["purpose"] == "Inspection"
        assert entries[1]["purpose"] == "VVIP duty"
        assert "Market" in entries[0]["place_of_visit"]
        assert "Town Hall" in entries[1]["place_of_visit"]

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
