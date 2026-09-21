"""Tests for the authorization-date workflow.

Flow: first data entry has no authorization date (it is issued by the
Designated Officer when the permission file is submitted, and the petition
records it).

Covers:
- ``validate_case_file_form`` accepts a missing authorization date
- CaseFile persists with ``authorization_date=None``
- Petition endpoints (PDF / DOCX / ZIP / copy-letter) return 403 until
  authorization is issued; permission endpoints stay open
- Preview reports the authorization state without blocking
"""

from __future__ import annotations

from datetime import date


_VALID_FORM_NO_AUTH = {
    "case_number": "2026/FSS/201",
    "food_safety_officer_name": "Test Officer",
    "inspection_date": "2026-07-02",
    "inspection_time": "12:40",
    "manufacturer_fssai": "10012345678901",
    "manufacturer_name": "Mfg",
    "manufacturer_fbo_name": "Mfg FBO",
    "manufacturer_address": "Addr",
    "retailer_fssai": "20012345678901",
    "retailer_name": "Ret",
    "retailer_fbo_name": "Ret FBO",
    "retailer_address": "Addr",
    "product_name": "Test Product",
    "batch_no": "B1",
    "sample_quantity": "1000g",
    "packet_count": "4",
    "mfg_date": "2026-01-01",
    "expiry_date": "2026-12-31",
    "sample_code": "SL201",
    "lab_registration_no": "WB/FOOD/2025/001",
    "do_receipt_date": "2026-07-04",
    "analyst_report_no": "PK/1",
    "analyst_report_date": "2026-07-05",
    "directive_letter_no": "H/FSSA/1",
    "directive_letter_date": "2026-07-06",
    # Handover long past → 30-day embargo already elapsed.
    "retailer_report_receive_date": "2026-07-07",
    "manufacturer_report_receive_date": "2026-07-08",
}


def _setup_client():
    from app import create_app
    from app.extensions import db
    from app.models import User

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False

    client = app.test_client()
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    user = User(username="authgateuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
    db.session.add(user)
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
    return app, client, ctx


def _teardown(ctx):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    ctx.pop()


def _make_case(db, **overrides):
    from app.models import CaseFile

    fields = {
        "case_number": "CF/AUTH/001",
        "food_safety_officer_name": "Test Officer",
        "authorization_date": None,
        "inspection_date": date(2026, 7, 2),
        "inspection_time": "12:40",
        "manufacturer_fssai": "10012345678901",
        "manufacturer_name": "Mfg",
        "manufacturer_fbo_name": "Mfg FBO",
        "manufacturer_address": "Addr",
        "retailer_fssai": "20012345678901",
        "retailer_name": "Ret",
        "retailer_fbo_name": "Ret FBO",
        "retailer_address": "Addr",
        "product_name": "Test Product",
        "batch_no": "B1",
        "sample_quantity": "1000g",
        "packet_count": 4,
        "mfg_date": date(2026, 1, 1),
        "expiry_date": date(2026, 12, 31),
        "sample_code": "SL201",
        "sample_submission_date": date(2026, 7, 3),
        "Lab_Registration_No": "WB/FOOD/2025/001",
        "do_receipt_date": date(2026, 7, 4),
        "analyst_report_no": "PK/1",
        "analyst_report_date": date(2026, 7, 5),
        "directive_letter_no": "H/FSSA/1",
        "directive_letter_date": date(2026, 7, 6),
        "retailer_report_receive_date": date(2026, 7, 7),
        "manufacturer_report_receive_date": date(2026, 7, 8),
    }
    fields.update(overrides)
    case = CaseFile(**fields)
    db.session.add(case)
    db.session.commit()
    return case


class TestAuthorizationOptional:
    def test_missing_authorization_date_passes_validation(self):
        from app.case_file_generator.routes import validate_case_file_form

        errors = validate_case_file_form(dict(_VALID_FORM_NO_AUTH))
        assert "authorization_date" not in errors
        assert errors == {}

    def test_invalid_authorization_date_still_flagged(self):
        from app.case_file_generator.routes import validate_case_file_form

        form = dict(_VALID_FORM_NO_AUTH, authorization_date="not-a-date")
        errors = validate_case_file_form(form)
        assert "authorization_date" in errors

    def test_case_persists_without_authorization(self):
        from app.extensions import db

        _app, _client, ctx = _setup_client()
        try:
            case = _make_case(db)
            assert case.id is not None
            assert case.authorization_date is None
        finally:
            _teardown(ctx)


class TestPetitionAuthorizationGate:
    def test_petition_endpoints_blocked_until_issued(self):
        from app.extensions import db

        _app, client, ctx = _setup_client()
        try:
            case = _make_case(db)
            for url in (
                f"/case_file_generator/case/{case.id}/pdf/petition",
                f"/case_file_generator/case/{case.id}/docx/petition",
                f"/case_file_generator/case/{case.id}/docx/zip",
                f"/case_file_generator/case/{case.id}/copy-letter/petition",
            ):
                resp = client.get(url)
                assert resp.status_code == 403, url
                assert "authorization" in resp.get_json()["error"].lower()

            # Permission endpoints stay open pre-authorization.
            resp = client.get(f"/case_file_generator/case/{case.id}/docx/permission")
            assert resp.status_code == 200
            resp = client.get(f"/case_file_generator/case/{case.id}/copy-letter/permission")
            assert resp.status_code == 200
        finally:
            _teardown(ctx)

    def test_petition_allowed_once_issued(self):
        from app.extensions import db

        _app, client, ctx = _setup_client()
        try:
            case = _make_case(db, authorization_date=date(2026, 8, 1))
            resp = client.get(f"/case_file_generator/case/{case.id}/copy-letter/petition")
            assert resp.status_code == 200
            assert "2026" in resp.get_data(as_text=True)
        finally:
            _teardown(ctx)

    def test_preview_flags_authorization_state(self):
        _app, client, ctx = _setup_client()
        try:
            resp = client.post("/case_file_generator/preview", data=dict(_VALID_FORM_NO_AUTH))
            assert resp.status_code == 200
            assert resp.get_json()["authorization_issued"] is False

            resp = client.post(
                "/case_file_generator/preview",
                data=dict(_VALID_FORM_NO_AUTH, authorization_date="2026-08-01"),
            )
            assert resp.status_code == 200
            assert resp.get_json()["authorization_issued"] is True
        finally:
            _teardown(ctx)


class TestCreationDefersPdfInEmbargo:
    def _post_create(self, client, monkeypatch, **overrides):
        from unittest import mock

        monkeypatch.setattr("app.case_file_generator.routes.sync_row", mock.Mock())
        pdf_mock = mock.Mock(return_value={"status": "ok"})
        monkeypatch.setattr("app.case_file_generator.tasks.generate_case_file_pdf", pdf_mock)
        form = dict(_VALID_FORM_NO_AUTH, **overrides)
        resp = client.post("/case_file_generator/generate_case_file", data=form)
        return resp, pdf_mock

    def test_creation_within_embargo_saves_and_defers_pdf(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from app.extensions import db
        from app.models import CaseFile

        _app, client, ctx = _setup_client()
        try:
            recent = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")
            resp, pdf_mock = self._post_create(
                client,
                monkeypatch,
                retailer_report_receive_date=recent,
                manufacturer_report_receive_date=recent,
                case_number="2026/FSS/202",
                sample_code="SL202",
            )
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["pdf_deferred"] is True
            assert data["earliest_allowed_date"] is not None
            pdf_mock.assert_not_called()
            assert db.session.get(CaseFile, data["case_file_id"]) is not None
        finally:
            _teardown(ctx)

    def test_creation_past_embargo_generates_pdf(self, monkeypatch):
        from app.extensions import db
        from app.models import CaseFile

        _app, client, ctx = _setup_client()
        try:
            resp, pdf_mock = self._post_create(
                client,
                monkeypatch,
                case_number="2026/FSS/203",
                sample_code="SL203",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pdf_deferred"] is False
            pdf_mock.assert_called_once()
            assert db.session.get(CaseFile, data["case_file_id"]) is not None
        finally:
            _teardown(ctx)


def _make_adjudication(db, **overrides):
    from app.models import Adjudication

    fields = {
        "case_number": "ADJ/AUTH/001",
        "food_safety_officer": "Test Officer",
        "non_license": "no",
        "pre_authorization": "no",
        "complaint_lodged": "no",
        "fbo_owner": "Owner",
        "fbo_name": "FBO",
        "fbo_address": "Addr",
        "fssai_license": "10012345678901",
        "First_inspection_date": date(2026, 7, 2),
        "compliance_deadline": date(2026, 8, 2),
        "inspection_date": date(2026, 7, 20),
        "authorization_date": None,
    }
    fields.update(overrides)
    adj = Adjudication(**fields)
    db.session.add(adj)
    db.session.commit()
    return adj


class TestAdjudicationAuthorizationGate:
    def test_missing_authorization_date_passes_validation(self):
        from app.adjudication.routes import validate_adjudication_form

        form = {
            "case_number": "2026/ADJ/1",
            "food_safety_officer_name": "Test Officer",
            "fbo_owner": "Owner",
            "fbo_name": "FBO",
            "fbo_address": "Addr",
            "fssai_license": "10012345678901",
            "first_inspection_date": "2026-07-02",
            "compliance_deadline": "2026-08-02",
            "followup_inspection_date": "2026-07-20",
            "pre_authorization": "no",
        }
        errors = validate_adjudication_form(form)
        assert "authorization_date" not in errors
        assert errors == {}

    def test_petition_endpoints_blocked_until_issued(self):
        from app.extensions import db

        _app, client, ctx = _setup_client()
        try:
            adj = _make_adjudication(db)
            for url in (
                f"/adjudication/case/{adj.id}/pdf/petition",
                f"/adjudication/case/{adj.id}/docx/petition",
                f"/adjudication/case/{adj.id}/docx/zip",
                f"/adjudication/case/{adj.id}/copy-letter/petition",
            ):
                resp = client.get(url)
                assert resp.status_code == 403, url
                assert "authorization" in resp.get_json()["error"].lower()

            # Permission endpoints stay open pre-authorization.
            resp = client.get(f"/adjudication/case/{adj.id}/docx/permission")
            assert resp.status_code == 200
            resp = client.get(f"/adjudication/case/{adj.id}/copy-letter/permission")
            assert resp.status_code == 200
        finally:
            _teardown(ctx)

    def test_petition_allowed_once_issued(self):
        from app.extensions import db

        _app, client, ctx = _setup_client()
        try:
            adj = _make_adjudication(db, authorization_date=date(2026, 8, 1))
            resp = client.get(f"/adjudication/case/{adj.id}/copy-letter/petition")
            assert resp.status_code == 200
        finally:
            _teardown(ctx)

    def test_blank_or_garbage_date_stays_blocked(self):
        """Raw form strings are normalized — '   ' / 'not-a-date' ≠ issued."""
        from unittest import mock

        from app.extensions import db

        _app, client, ctx = _setup_client()
        try:
            _make_adjudication(db)
            with mock.patch(
                "app.adjudication.routes.sync_row", mock.Mock()
            ), mock.patch(
                "app.adjudication.routes.generate_pdf_from_html", mock.Mock(return_value=(b"pdf", None))
            ):
                for bad in ("   ", "not-a-date"):
                    resp = client.post(
                        "/adjudication/generate_all",
                        data={
                            "case_number": "ADJ/AUTH/002",
                            "food_safety_officer_name": "Test Officer",
                            "fbo_owner": "Owner",
                            "fbo_name": "FBO",
                            "fbo_address": "Addr",
                            "fssai_license": "10012345678901",
                            "first_inspection_date": "2026-07-02",
                            "compliance_deadline": "2026-08-02",
                            "followup_inspection_date": "2026-07-20",
                            "pre_authorization": "no",
                            "authorization_date": bad,
                        },
                    )
                    assert resp.status_code == 403, bad
        finally:
            _teardown(ctx)

    def test_pre_authorization_has_no_petition_anywhere(self):
        from app.extensions import db

        _app, client, ctx = _setup_client()
        try:
            adj = _make_adjudication(db, pre_authorization="yes")
            for url in (
                f"/adjudication/case/{adj.id}/pdf/petition",
                f"/adjudication/case/{adj.id}/docx/petition",
                f"/adjudication/case/{adj.id}/docx/zip",
                f"/adjudication/case/{adj.id}/copy-letter/petition",
            ):
                resp = client.get(url)
                assert resp.status_code == 400, url
                assert "no petition" in resp.get_json()["error"].lower()
        finally:
            _teardown(ctx)

    def test_pre_authorization_has_no_pending_warning(self):
        _app, client, ctx = _setup_client()
        try:
            from app.extensions import db

            adj = _make_adjudication(db, pre_authorization="yes")
            resp = client.get(f"/timeline/api/case/{adj.id}?kind=adjudication")
            assert resp.status_code == 200
            data = resp.get_json()
            assert not any("Authorization pending" in w["message"] for w in data["warnings"])
        finally:
            _teardown(ctx)

    def test_non_preauth_pending_warning(self):
        _app, client, ctx = _setup_client()
        try:
            from app.extensions import db

            adj = _make_adjudication(db)
            resp = client.get(f"/timeline/api/case/{adj.id}?kind=adjudication")
            assert resp.status_code == 200
            data = resp.get_json()
            assert any("Authorization pending" in w["message"] for w in data["warnings"])
        finally:
            _teardown(ctx)
