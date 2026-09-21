"""Tests for the Phase 13 timeline engine + UI (app/timeline/).

Covers:
- Milestone extraction from CaseFile / Adjudication records
- Linked Sample / Annexure / Evidence events
- Chronological-sequence validation warnings
- timeline_event persistence (case_file only — adjudication is ephemeral)
- HTTP endpoints: page view, JSON API, refresh
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.timeline.engine import TimelineEngine

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dt(day: int, month: int = 1, year: int = 2026, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="timelineuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)  # Flask-Login key

    return app, client, app_context


def _teardown_test_env(app_context):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    app_context.pop()


def _make_case_file(db, **overrides):
    """Create a CaseFile with a valid, chronologically ordered date set."""
    from app.models import CaseFile

    defaults = dict(
        case_number="CF/TL/2026/001",
        food_safety_officer_name="Test Officer",
        authorization_date=_dt(5, 1),
        inspection_date=_dt(10, 1),
        inspection_time="10:30",
        manufacturer_fssai="MF-100",
        manufacturer_name="Acme Foods",
        manufacturer_fbo_name="Acme Foods Pvt Ltd",
        manufacturer_address="Kolkata",
        retailer_fssai="RT-200",
        retailer_name="Corner Store",
        retailer_fbo_name="Corner Store Pvt Ltd",
        retailer_address="Kolkata",
        product_name="Milk",
        batch_no="B-1",
        sample_quantity="500 ml",
        packet_count=10,
        mfg_date=_dt(1, 1),
        expiry_date=_dt(1, 3),
        sample_code="SMP-TL-001",
        sample_submission_date=_dt(15, 1),
        Lab_Registration_No="LAB-1",
        do_receipt_date=_dt(20, 1),
        analyst_report_no="AR-1",
        analyst_report_date=_dt(1, 2),
        directive_letter_no="DL-1",
        directive_letter_date=_dt(10, 2),
        retailer_report_receive_date=_dt(20, 2),
        manufacturer_report_receive_date=_dt(22, 2),
    )
    defaults.update(overrides)
    case = CaseFile(**defaults)
    db.session.add(case)
    db.session.commit()
    return case


def _make_sample(db, collection_date=None, submission_date=None):
    from app.models import Sample

    sample = Sample(
        sample_code="SMP-TL-002",
        sample_name="Water",
        sample_type="enforcement",
        fso_name="Test Officer",
        collection_date=collection_date or _dt(11, 1),
        submission_date=submission_date or _dt(12, 1),
    )
    db.session.add(sample)
    db.session.commit()
    return sample


def _make_annexure(db, case_id=None, adjudication_id=None, caption="Supporting Report", letter="A"):
    from app.models import Annexure

    annexure = Annexure(
        case_id=case_id,
        adjudication_id=adjudication_id,
        caption=caption,
        date=_dt(14, 1),
        file_hash="a" * 64,
        filepath="/tmp/annexure.pdf",
        filename="annexure.pdf",
        annexure_letter=letter,
    )
    db.session.add(annexure)
    db.session.commit()
    return annexure


def _make_adjudication(db):
    from app.models import Adjudication

    adj = Adjudication(
        case_number="ADJ/TL/2026/001",
        food_safety_officer="Test Officer",
        fbo_owner="Raj",
        fbo_name="Raj Traders",
        fbo_address="Kolkata",
        fssai_license="FSSAI-1",
        Complaint_date=_dt(3, 1),
        First_inspection_date=_dt(8, 1),
        inspection_date=_dt(10, 1),
        compliance_deadline=_dt(8, 2),
        authorization_date=_dt(5, 1),
    )
    db.session.add(adj)
    db.session.commit()
    return adj


def _resolved_case(case_id, kind):
    from app.shared.case_resolver import CaseResolver

    return CaseResolver().resolve(case_id, kind=kind)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class TestTimelineEngine:
    def test_case_file_milestones_extracted_and_sorted(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            entries = TimelineEngine().extract(case)

            types = {e.event_type for e in entries}
            assert "inspection" in types
            assert "sampling" in types
            assert "lab_receipt" in types
            assert "lab_report" in types
            assert "notice" in types
            assert "reply" in types
            assert "case_created" in types

            # Chronologically sorted ascending.
            stamps = [e.timestamp for e in entries]
            assert stamps == sorted(stamps)
        finally:
            _teardown_test_env(ctx)

    def test_linked_sample_and_annexure_events(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            sample = _make_sample(db)
            case = _make_case_file(db, sample_id=sample.id)
            annexure = _make_annexure(db, case_id=case.id)

            entries = TimelineEngine().extract(case)
            annexure_entries = [e for e in entries if e.event_type == "annexure"]
            assert annexure_entries
            assert annexure_entries[0].document_ref == f"annexure:{annexure.id}"
            assert annexure_entries[0].document_label is not None

            # Linked sample collection date surfaces as a sampling event.
            sampling_entries = [e for e in entries if e.event_type == "sampling"]
            assert any("Sample collected" in e.description for e in sampling_entries)
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_milestones_extracted(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            entries = TimelineEngine().extract(adj)

            types = {e.event_type for e in entries}
            assert "complaint" in types
            assert "inspection" in types
            assert "compliance" in types
            assert "authorization" in types
            assert "case_created" in types
        finally:
            _teardown_test_env(ctx)

    def test_sequence_warning_on_inverted_dates(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            # Analyst report (5 Jan) precedes sampling (15 Jan) — invalid.
            case = _make_case_file(
                db,
                analyst_report_date=_dt(5, 1),
                sample_submission_date=_dt(15, 1),
            )
            engine = TimelineEngine()
            entries = engine.extract(case)
            warnings = engine.validate_sequence(entries)
            assert warnings, "expected at least one sequence warning"
            assert any("Lab report" in w["message"] for w in warnings)
        finally:
            _teardown_test_env(ctx)

    def test_valid_case_has_no_warnings(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            engine = TimelineEngine()
            warnings = engine.validate_sequence(engine.extract(case))
            assert warnings == []
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_missing_optional_dates(self):
        """Nullable dates (Complaint_date, authorization_date) must not crash or emit events."""
        from app.extensions import db
        from app.models import Adjudication

        _app, _client, ctx = _setup_test_env()
        try:
            adj = Adjudication(
                case_number="ADJ/TL/2026/002",
                food_safety_officer="Test Officer",
                fbo_owner="Raj",
                fbo_name="Raj Traders",
                fbo_address="Kolkata",
                fssai_license="FSSAI-2",
                Complaint_date=None,
                authorization_date=None,
                First_inspection_date=_dt(8, 1),
                inspection_date=_dt(10, 1),
                compliance_deadline=_dt(8, 2),
            )
            db.session.add(adj)
            db.session.commit()

            entries = TimelineEngine().extract(adj)
            types = {e.event_type for e in entries}
            assert "complaint" not in types
            assert "authorization" not in types
            assert "inspection" in types
        finally:
            _teardown_test_env(ctx)

    def test_refresh_persists_case_file_events_idempotently(self):
        from app.extensions import db
        from app.models import TimelineEvent

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resolved = _resolved_case(case.id, "case_file")
            engine = TimelineEngine()

            count1 = engine.refresh(resolved)
            assert count1 > 0
            rows1 = TimelineEvent.query.filter_by(case_id=case.id).all()
            assert len(rows1) == count1
            assert all(r.case_type == "case_file" for r in rows1)
            assert all(r.timestamp is not None for r in rows1)

            # Idempotent — re-running replaces rows, count stays identical.
            count2 = engine.refresh(resolved)
            assert count2 == count1
            rows2 = TimelineEvent.query.filter_by(case_id=case.id).all()
            assert len(rows2) == count1
        finally:
            _teardown_test_env(ctx)

    def test_refresh_skips_adjudication(self):
        from app.extensions import db
        from app.models import TimelineEvent

        _app, _client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            resolved = _resolved_case(adj.id, "adjudication")
            engine = TimelineEngine()

            assert engine.refresh(resolved) == 0
            assert TimelineEvent.query.count() == 0
        finally:
            _teardown_test_env(ctx)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


class TestTimelineRoutes:
    def test_api_returns_payload(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            _make_annexure(db, case_id=case.id)
            resp = client.get(f"/timeline/api/case/{case.id}?kind=case_file")
            assert resp.status_code == 200

            data = resp.get_json()
            assert data["case_number"] == "CF/TL/2026/001"
            assert data["case_type"] == "case_file"
            assert data["persisted"] is True
            assert data["events"]
            assert data["events"][0]["timestamp"] is not None
            assert isinstance(data["warnings"], list)

            # Annexure events carry a download URL for direct document links.
            annexure_events = [e for e in data["events"] if e["event_type"] == "annexure"]
            assert annexure_events
            assert annexure_events[0]["document_ref"].startswith("annexure:")
            assert annexure_events[0]["document_url"].startswith("/annexure/")
        finally:
            _teardown_test_env(ctx)

    def test_api_404_for_unknown_case(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/timeline/api/case/99999?kind=case_file")
            assert resp.status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_api_is_ephemeral(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            resp = client.get(f"/timeline/api/case/{adj.id}?kind=adjudication")
            assert resp.status_code == 200

            data = resp.get_json()
            assert data["case_type"] == "adjudication"
            assert data["persisted"] is False
            types = {e["event_type"] for e in data["events"]}
            assert "complaint" in types
        finally:
            _teardown_test_env(ctx)

    def test_view_renders(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.get(f"/timeline/case/{case.id}?kind=case_file")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "Case Timeline" in html
            # Hero action jumps back to the document editor.
            assert f"/case_file_generator/{case.id}/editor" in html
        finally:
            _teardown_test_env(ctx)

    def test_view_edit_link_for_adjudication(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            resp = client.get(f"/timeline/case/{adj.id}?kind=adjudication")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "Case Timeline" in html
            assert f"/adjudication/{adj.id}/editor" in html
        finally:
            _teardown_test_env(ctx)

    def test_refresh_endpoint(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.post(f"/timeline/api/case/{case.id}/refresh?kind=case_file")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["persisted"] > 0
        finally:
            _teardown_test_env(ctx)

    def test_view_404_for_unknown_case(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/timeline/case/99999?kind=case_file")
            assert resp.status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_global_nav_picker_renders_on_all_pages(self):
        """The base layout exposes the Timeline case-picker on every page."""
        _app, client, ctx = _setup_test_env()
        try:
            # Annexure page — the picker lives in the shared base layout.
            page = client.get("/annexure/")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert 'id="timelinePicker"' in html
            assert 'id="tlKind"' in html
            assert 'id="tlSearch"' in html
            assert 'id="tlOpenBtn"' in html
            # Search dropdown (replaces the old datalist) is present too.
            assert 'id="tlResults"' in html
            assert 'id="tlEmpty"' in html

            # Audit log page.
            page = client.get("/admin/audit-log")
            assert page.status_code == 200
            assert 'id="timelinePicker"' in page.get_data(as_text=True)
        finally:
            _teardown_test_env(ctx)

    def test_timeline_entry_points_across_ui(self):
        """Timeline links/buttons render wherever a case surfaces in the UI."""
        from app.extensions import db
        from app.models import Evidence, Inspection

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            adj = _make_adjudication(db)

            sample = _make_sample(db)
            case.sample_id = sample.id
            db.session.commit()

            _make_annexure(db, case_id=case.id)
            db.session.add(
                Evidence(
                    evidence_type="report",
                    filepath="/tmp/evidence.pdf",
                    filename="evidence.pdf",
                    case_id=case.id,
                )
            )
            db.session.add(
                Inspection(
                    inspection_code="INSP-TL-001",
                    fso_name="Test Officer",
                    inspection_date=_dt(10, 1),
                    compliance_deadline=_dt(10, 2),
                    adjudication_id=adj.id,
                )
            )
            db.session.commit()

            case_tl = f"/timeline/case/{case.id}?kind=case_file"
            adj_tl = f"/timeline/case/{adj.id}?kind=adjudication"

            # Annexure index — timeline button per linked case.
            page = client.get("/annexure/")
            assert page.status_code == 200
            assert case_tl in page.get_data(as_text=True)

            # Evidence index — timeline icon per linked case.
            page = client.get("/evidence/")
            assert page.status_code == 200
            assert case_tl in page.get_data(as_text=True)

            # Inspection list — timeline button when adjudicated.
            page = client.get("/inspection/list")
            assert page.status_code == 200
            assert adj_tl in page.get_data(as_text=True)

            # Audit log — timeline link for CaseFile / Adjudication records.
            page = client.get("/admin/audit-log")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert case_tl in html
            assert adj_tl in html

            # Version-control history — header timeline button.
            page = client.get(f"/api/version-control/history/ui/{case.id}?kind=case_file")
            assert page.status_code == 200
            assert case_tl in page.get_data(as_text=True)

            # Sample list — timeline button when the sample has a linked case.
            page = client.get("/sample/list")
            assert page.status_code == 200
            assert case_tl in page.get_data(as_text=True)
        finally:
            _teardown_test_env(ctx)

    def test_sample_detail_json_includes_timeline_link(self):
        """GET /sample/<id> carries the linked case + timeline URL."""
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            sample = _make_sample(db)

            # Unlinked sample → no timeline link.
            data = client.get(f"/sample/{sample.id}").get_json()
            assert data["case_id"] is None
            assert data["timeline_url"] is None

            # Once linked to a case, the detail JSON surfaces the timeline URL.
            case = _make_case_file(db, sample_id=sample.id)
            data = client.get(f"/sample/{sample.id}").get_json()
            assert data["case_id"] == case.id
            assert data["timeline_url"] == f"/timeline/case/{case.id}?kind=case_file"
        finally:
            _teardown_test_env(ctx)

    def test_cases_json_feed_shape(self):
        """The picker's datalist depends on /cases returning a bare array
        with id / case_number / party fields — lock that contract in."""
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            _make_case_file(db)
            _make_adjudication(db)

            # Search page ships the client-side timeline builder for results.
            search_page = client.get("/search/")
            assert search_page.status_code == 200
            assert "/timeline/case/" in search_page.get_data(as_text=True)

            case_list = client.get("/case_file_generator/cases").get_json()
            assert isinstance(case_list, list)
            assert case_list
            c = case_list[0]
            assert "id" in c and "case_number" in c and "manufacturer_name" in c

            adj_list = client.get("/adjudication/cases").get_json()
            assert isinstance(adj_list, list)
            assert adj_list
            a = adj_list[0]
            assert "id" in a and "case_number" in a and "fbo_name" in a
        finally:
            _teardown_test_env(ctx)

    def test_editor_page_links_to_timeline(self):
        """The document-editor action bar exposes a Timeline button per case."""
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            page = client.get(f"/case_file_generator/{case.id}/editor")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert f"/timeline/case/{case.id}?kind=case_file" in html

            adj = _make_adjudication(db)
            page = client.get(f"/adjudication/{adj.id}/editor")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert f"/timeline/case/{adj.id}?kind=adjudication" in html
        finally:
            _teardown_test_env(ctx)

    def test_index_pages_link_to_timeline(self):
        """Case-file and adjudication index pages surface a Timeline button per case."""
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            _make_adjudication(db)

            page = client.get("/case_file_generator/")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert "Case Timelines" in html
            assert f"/timeline/case/{case.id}?kind=case_file" in html

            page = client.get("/adjudication/")
            assert page.status_code == 200
            html = page.get_data(as_text=True)
            assert "Case Timelines" in html
            assert "/timeline/case/" in html
            assert "kind=adjudication" in html
        finally:
            _teardown_test_env(ctx)


# --------------------------------------------------------------------------- #
# Legal deadlines (365-day petition limit + 30-day appeal embargo)
# --------------------------------------------------------------------------- #


class TestLegalDeadlines:
    def test_petition_deadline_is_report_plus_365(self):
        from app.timeline.engine import petition_deadline_for

        assert petition_deadline_for(_dt(1, 2)) == _dt(1, 2, year=2027)
        assert petition_deadline_for(None) is None

    def test_handover_max_and_allowed_from(self):
        from app.timeline.engine import generation_allowed_from, handover_max_date

        assert handover_max_date(_dt(20, 2), _dt(22, 2)) == _dt(22, 2)
        assert generation_allowed_from(_dt(20, 2), _dt(22, 2)) == _dt(24, 3)
        # RCM case — retailer handover alone drives the embargo.
        assert generation_allowed_from(_dt(20, 2), None) == _dt(22, 3)
        assert handover_max_date(None, None) is None
        assert generation_allowed_from(None, None) is None

    def test_generation_gate_blocks_within_30_days(self):
        from app.timeline.engine import generation_gate

        gate = generation_gate(_dt(20, 2), _dt(22, 2), now=_dt(1, 3))
        assert gate["blocked"] is True
        assert gate["earliest"] == _dt(24, 3)

        gate = generation_gate(_dt(20, 2), _dt(22, 2), now=_dt(24, 3))
        assert gate["blocked"] is False  # allowed on the 30th day itself

        gate = generation_gate(None, None, now=_dt(1, 3))
        assert gate["blocked"] is False

    def test_deadline_events_in_gantt_extraction(self):
        from app.extensions import db
        from app.timeline.engine import TimelineEngine

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            entries = TimelineEngine().extract(case)
            by_type = {e.event_type: e for e in entries}

            def _naive(dt):
                return dt.replace(tzinfo=None) if dt.tzinfo else dt

            assert _naive(by_type["petition_deadline"].timestamp) == _naive(_dt(1, 2, year=2027))
            assert _naive(by_type["generation_allowed"].timestamp) == _naive(_dt(24, 3))
            stamps = [e.timestamp for e in entries]
            assert stamps == sorted(stamps)
        finally:
            _teardown_test_env(ctx)

    def test_payload_deadlines_and_embargo_warning(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            # Handover within the last 30 days → embargo still active today.
            case = _make_case_file(
                db,
                retailer_report_receive_date=_dt(1, 9),
                manufacturer_report_receive_date=_dt(5, 9),
            )
            data = client.get(f"/timeline/api/case/{case.id}?kind=case_file").get_json()
            deadlines = data["deadlines"]
            assert deadlines["petition_deadline_date"] == "2027-02-01"
            assert deadlines["generation_allowed_from_date"] == "2026-10-05"
            assert deadlines["handover_max_date"] == "2026-09-05"
            assert deadlines["generation_blocked"] is True
            # Handover was recent relative to today → embargo warning present.
            assert any("cannot be generated" in w["message"] for w in data["warnings"])
        finally:
            _teardown_test_env(ctx)

    def test_overdue_petition_warning(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db, analyst_report_date=_dt(1, 1, year=2020))
            data = client.get(f"/timeline/api/case/{case.id}?kind=case_file").get_json()
            assert data["deadlines"]["petition_overdue"] is True
            assert any("deadline has passed" in w["message"] for w in data["warnings"])
        finally:
            _teardown_test_env(ctx)

    def test_authorization_event_present_when_issued(self):
        from app.extensions import db
        from app.timeline.engine import TimelineEngine

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            entries = TimelineEngine().extract(case)
            auth = [e for e in entries if e.event_type == "authorization"]
            assert len(auth) == 1
            assert auth[0].timestamp is not None
        finally:
            _teardown_test_env(ctx)

    def test_authorization_pending_warning_when_missing(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db, authorization_date=None)
            data = client.get(f"/timeline/api/case/{case.id}?kind=case_file").get_json()
            assert data["deadlines"]["authorization_issued"] is False
            assert data["deadlines"]["authorization_date"] is None
            assert any("Authorization pending" in w["message"] for w in data["warnings"])
            assert not any(e["event_type"] == "authorization" for e in data["events"])
        finally:
            _teardown_test_env(ctx)
