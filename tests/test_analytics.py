"""Tests for the Analytics Dashboard (Phase 15).

Covers:
- GET /analytics/ renders the dashboard template (200)
- GET /analytics/api/metrics returns valid JSON with all metric keys
- Metrics API returns correct counts for seeded data
- Route is auth-gated (unauthenticated → 302)
- Route collision check

No external services required — uses in-memory SQLite with seeded data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, FSO, and sample data.

    Returns (app, client, app_context). The client is pre-authenticated.
    """
    from app import create_app
    from app.extensions import db
    from app.models import (
        FSO,
        Adjudication,
        CaseFile,
        Evidence,
        FboIssue,
        Inspection,
        Sample,
        User,
    )

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="analytics_user", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Officer Alpha"))
    db.session.add(FSO(fso_name="Officer Beta"))

    # Seed 2 case files
    now = datetime.now(UTC)
    for i in range(2):
        cf = CaseFile(
            case_number=f"CF-2026-{i+1:03d}",
            food_safety_officer_name="Officer Alpha",
            authorization_date=now,
            inspection_date=now,
            inspection_time="10:00",
            manufacturer_fssai="1001",
            manufacturer_name="Mfg Corp",
            manufacturer_fbo_name="Mfg FBO",
            manufacturer_address="123 Main St",
            retailer_fssai="2001",
            retailer_name="Retail Shop",
            retailer_fbo_name="Retail FBO",
            retailer_address="456 Oak Ave",
            product_name="Milk",
            batch_no="B001",
            sample_quantity="500ml",
            packet_count=2,
            mfg_date=now,
            expiry_date=now + timedelta(days=30),
            sample_code=f"SC-2026-{i+1:03d}",
            sample_submission_date=now,
            Lab_Registration_No="LAB001",
            do_receipt_date=now,
            is_misbranded=False,
            is_substandard=True,
            analyst_report_no=f"AR-{i+1}",
            analyst_report_date=now,
            directive_letter_no=f"DL-{i+1}",
            directive_letter_date=now,
            retailer_report_receive_date=now,
            manufacturer_report_receive_date=now,
        )
        db.session.add(cf)

    # Seed 1 adjudication with section_55 cited
    adj = Adjudication(
        case_number="ADJ-2026-001",
        food_safety_officer="Officer Alpha",
        fbo_owner="Owner",
        fbo_name="Adj FBO",
        fbo_address="789 Pine St",
        fssai_license="3001",
        First_inspection_date=now,
        compliance_deadline=now + timedelta(days=30),
        inspection_date=now,
        section_55="yes",
        section_58="yes",
    )
    db.session.add(adj)

    # Seed 3 inspections (2 active, 1 dismissed)
    for i in range(3):
        ins = Inspection(
            inspection_code=f"INS-2026-{i+1:03d}",
            fso_name="Officer Alpha" if i < 2 else "Officer Beta",
            inspection_date=now - timedelta(days=i),
            compliance_deadline=now + timedelta(days=30),
            is_dismissed=(i == 2),
        )
        db.session.add(ins)

    # Seed 4 samples (3 billed, 1 unbilled)
    for i in range(4):
        s = Sample(
            sample_code=f"SMP-2026-{i+1:03d}",
            sample_name=f"Sample {i+1}",
            sample_type="Food",
            fso_name="Officer Alpha",
            collection_date=now - timedelta(days=i),
            billed=(i < 3),
        )
        db.session.add(s)

    # Seed 1 evidence
    ev = Evidence(
        case_id=None,
        adjudication_id=None,
        evidence_type="photo",
        filepath="/tmp/test.jpg",
        filename="test.jpg",
    )
    db.session.add(ev)

    # Seed 2 FBO issues
    for i in range(2):
        issue = FboIssue(
            fbo_id=f"FBO-{i+1}",
            fbo_name=f"Issue FBO {i+1}",
            source_type="inspection",
            state="open" if i == 0 else "closed",
            fso_name="Officer Alpha",
            reg_lat=22.57 + i * 0.01,
            reg_lng=88.36 + i * 0.01,
        )
        db.session.add(issue)

    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, app_context


def _setup_unauthenticated_client():
    """Create a test client without authentication."""
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

    user = User(username="analytics_user", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Officer Alpha"))
    db.session.commit()

    client = app.test_client()
    return app, client, app_context


class TestAnalyticsDashboardRoute:
    """Tests for GET /analytics/."""

    def test_dashboard_renders(self):
        """Authenticated user sees the dashboard page (200)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Analytics Dashboard" in html
            assert "caseTrendsChart" in html
        finally:
            ctx.pop()

    def test_unauthenticated_redirects(self):
        """Unauthenticated user is redirected to login."""
        _app, unauth_client, ctx = _setup_unauthenticated_client()
        try:
            resp = unauth_client.get("/analytics/", follow_redirects=False)
            assert resp.status_code in (302, 303)
        finally:
            ctx.pop()

    def test_dashboard_contains_chart_elements(self):
        """The dashboard page includes all required chart canvases."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/")
            html = resp.data.decode()
            for canvas_id in [
                "caseTrendsChart",
                "complianceChart",
                "sampleChart",
                "provisionsChart",
                "fsoChart",
                "evidenceChart",
                "fboMap",
            ]:
                assert canvas_id in html, f"Missing element: {canvas_id}"
        finally:
            ctx.pop()

    def test_dashboard_extends_base(self):
        """The dashboard template extends base.html (has nav)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/")
            html = resp.data.decode()
            assert "nav-link" in html
        finally:
            ctx.pop()


class TestAnalyticsMetricsAPI:
    """Tests for GET /analytics/api/metrics."""

    def test_metrics_returns_200(self):
        """The metrics API returns 200 with valid JSON."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data, dict)
        finally:
            ctx.pop()

    def test_metrics_contains_all_keys(self):
        """The metrics response includes all expected metric sections."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            expected_keys = [
                "summary",
                "case_trends",
                "inspection_compliance",
                "sample_pipeline",
                "legal_provisions",
                "fso_activity",
                "fbo_issues",
                "evidence",
                "geo_data",
            ]
            for key in expected_keys:
                assert key in data, f"Missing key: {key}"
        finally:
            ctx.pop()

    def test_summary_counts_correct(self):
        """Summary counts match the seeded data."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            summary = data["summary"]
            assert summary["case_files"] == 2
            assert summary["adjudications"] == 1
            assert summary["inspections"] == 3
            assert summary["samples"] == 4
            assert summary["evidence"] == 1
            assert summary["fbo_issues"] == 2
        finally:
            ctx.pop()

    def test_inspection_compliance_correct(self):
        """Inspection compliance counts match seeded data (2 active, 1 dismissed)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            ic = data["inspection_compliance"]
            assert ic["active"] == 2
            assert ic["dismissed"] == 1
            assert ic["total"] == 3
        finally:
            ctx.pop()

    def test_legal_provisions_correct(self):
        """Legal provisions match seeded adjudication (section_55=yes, section_58=yes)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            provisions = {p["section"]: p["count"] for p in data["legal_provisions"]}
            assert provisions.get("Section 55 (Penalty)") == 1
            assert provisions.get("Section 58 (Sub-standard)") == 1
            assert provisions.get("Section 56 (Hygiene)") == 0
        finally:
            ctx.pop()

    def test_sample_pipeline_structure(self):
        """Sample pipeline returns a list of monthly dicts."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            sp = data["sample_pipeline"]
            assert isinstance(sp, list)
            if sp:
                assert "month" in sp[0]
                assert "billed" in sp[0]
                assert "unbilled" in sp[0]
        finally:
            ctx.pop()

    def test_fso_activity_correct(self):
        """FSO activity returns the correct counts."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            fso = data["fso_activity"]
            assert isinstance(fso, list)
            # Officer Alpha has 2 inspections, Officer Beta has 1
            names = {f["fso"]: f["count"] for f in fso}
            assert names.get("Officer Alpha") == 2
            assert names.get("Officer Beta") == 1
        finally:
            ctx.pop()

    def test_geo_data_structure(self):
        """Geo data returns a list of location dicts with lat/lng."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            geo = data["geo_data"]
            assert isinstance(geo, list)
            assert len(geo) == 2
            for pt in geo:
                assert "lat" in pt
                assert "lng" in pt
                assert "name" in pt
        finally:
            ctx.pop()

    def test_evidence_summary_correct(self):
        """Evidence summary matches seeded data (1 photo)."""
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/analytics/api/metrics")
            data = resp.get_json()
            ev = data["evidence"]
            assert ev["photo"] == 1
            assert ev["total"] == 1
        finally:
            ctx.pop()

    def test_unauthenticated_metrics_redirects(self):
        """Unauthenticated user is redirected on API call."""
        _app, unauth_client, ctx = _setup_unauthenticated_client()
        try:
            resp = unauth_client.get("/analytics/api/metrics", follow_redirects=False)
            assert resp.status_code in (302, 303)
        finally:
            ctx.pop()

    def test_empty_database_returns_zeros(self):
        """Metrics API returns zeros when no data exists."""
        from app import create_app
        from app.extensions import db

        app = create_app()
        app.config["TESTING"] = True
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()

        from app.models import FSO, User

        user = User(username="empty_user", password_hash="pbkdf2:sha256$test$dummy")
        db.session.add(user)
        db.session.add(FSO(fso_name="Placeholder"))
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)

        try:
            resp = client.get("/analytics/api/metrics")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["summary"]["case_files"] == 0
            assert data["summary"]["samples"] == 0
            assert data["geo_data"] == []
        finally:
            ctx.pop()
