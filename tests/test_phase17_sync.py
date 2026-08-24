"""Phase 17: Supabase Bridge + conflict resolution + sync-status UI tests.

Tests do NOT require the ``supabase`` package to be installed — the service
uses lazy imports and degrades gracefully when the package or credentials
are absent.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest import mock

import pytest

from app import create_app
from app.extensions import db
from app.models import FSO, Inspection, User
from app.shared.config import cfg
from app.sync.models import SyncConflict, SyncLog, SyncState
from app.sync.supabase_sync import SupabaseSyncService, SyncResult, get_sync_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(enable_sync: bool = False):
    """Create a test app with DB tables, a user, and an FSO."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ENABLE_SUPABASE_SYNC"] = enable_sync
    app.config["SUPABASE_URL"] = "https://test.supabase.co" if enable_sync else ""
    app.config["SUPABASE_API_KEY"] = "test-key" if enable_sync else ""

    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(FSO(fso_name="Test FSO"))
        db.session.add(User(username="raguser", password_hash="pbkdf2:sha256$test$dummy"))
        db.session.commit()
    return app


def _authed_client(app):
    """Return an authenticated test client for *app* (mirrors test_rag_routes.py)."""
    with app.app_context():
        user = User.query.first()
        uid = str(user.id) if user else "1"
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = uid
    return client


def _make_inspection(fso_name="Test FSO"):
    """Create a minimal Inspection record for sync tests."""
    return Inspection(
        inspection_code="INS-001",
        fso_name=fso_name,
        fssai_license="FSSAI-001",
        fbo_name="Test FBO",
        fbo_address="123 Test St",
        concerned_food="Biscuits",
        problem="Adulteration",
        inspection_date=datetime(2025, 1, 1),
        compliance_deadline=datetime(2025, 2, 1),
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestSyncModels:
    """Verify SyncState, SyncConflict, SyncLog can be created and persisted."""

    def test_sync_state_create(self):
        app = _make_app()
        with app.app_context():
            state = SyncState(
                table_name="inspections",
                local_id=1,
                sync_version=1,
                synced_at=None,
            )
            db.session.add(state)
            db.session.commit()
            assert state.id is not None
            assert state.local_id == 1
            assert state.sync_version == 1

    def test_sync_conflict_create(self):
        app = _make_app()
        with app.app_context():
            conf = SyncConflict(
                table_name="inspections",
                local_id=1,
                local_version=2,
                remote_version=3,
                direction="push",
                remote_snapshot='{"id": 1, "name": "test"}',
            )
            db.session.add(conf)
            db.session.commit()
            assert conf.id is not None
            assert conf.direction == "push"

    def test_sync_log_create_and_errors_property(self):
        app = _make_app()
        with app.app_context():
            log = SyncLog(
                operation="push",
                status="partial",
                pushed=5,
                pulled=0,
                conflicts=1,
                errors_json=json.dumps(["error one", "error two"]),
            )
            db.session.add(log)
            db.session.commit()
            assert log.id is not None
            assert log.errors == ["error one", "error two"]

    def test_sync_log_errors_empty(self):
        app = _make_app()
        with app.app_context():
            log = SyncLog(operation="push", status="ok", pushed=3)
            db.session.add(log)
            db.session.commit()
            assert log.errors == []

    def test_sync_log_errors_invalid_json(self):
        app = _make_app()
        with app.app_context():
            log = SyncLog(
                operation="push",
                status="error",
                errors_json="not-json",
            )
            db.session.add(log)
            db.session.commit()
            assert log.errors == []


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class TestSyncService:
    """Verify SupabaseSyncService behaviour without a real Supabase client."""

    def test_service_is_disabled_by_default(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            assert service.is_enabled() is False

    def test_service_enabled_with_config(self):
        app = _make_app(enable_sync=True)
        with app.app_context():
            service = get_sync_service()
            assert service.is_enabled() is True

    def test_get_client_returns_none_when_disabled(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            assert service.get_client() is None

    def test_get_client_returns_none_without_supabase_package(self):
        """Service degrades gracefully when supabase package is missing."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            service = get_sync_service()
            with mock.patch.dict("sys.modules", {"supabase": None}):
                service._client = None
                assert service.get_client() is None

    def test_push_when_disabled(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            result = service.push()
            assert result.status == "disabled"
            assert result.pushed == 0

    def test_pull_when_disabled(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            result = service.pull()
            assert result.status == "disabled"
            assert result.pulled == 0

    def test_push_with_mock_client(self):
        """Push uses the mock client and marks SyncState as synced."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            insp = _make_inspection()
            db.session.add(insp)
            db.session.commit()

            mock_client = mock.MagicMock()
            mock_client.table().upsert().execute.return_value = mock.MagicMock(data=[{"local_id": insp.id}])
            mock_client.table().select().in_().execute.return_value = mock.MagicMock(data=[])
            mock_client.table().select().eq().execute.return_value = mock.MagicMock(data=[])

            service = SupabaseSyncService()
            with mock.patch.object(service, "_client", mock_client):
                result = service.push()

            assert result.status in ("ok", "partial")
            assert result.pushed >= 1

            state = SyncState.query.filter_by(table_name="inspections", local_id=insp.id).first()
            assert state is not None
            assert state.sync_version > 0

    def test_push_uses_batch_upsert(self):
        """Push batches all dirty records into a single upsert call."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            insp1 = _make_inspection()
            insp2 = Inspection(
                inspection_code="INS-002",
                fso_name="Test FSO",
                inspection_date=datetime(2025, 3, 1),
                compliance_deadline=datetime(2025, 4, 1),
            )
            db.session.add_all([insp1, insp2])
            db.session.commit()

            mock_client = mock.MagicMock()
            execute_mock = mock.MagicMock(return_value=mock.MagicMock(data=[{"local_id": 1}]))
            # Track calls to upsert(chunk) — the chunk is passed to upsert, not execute.
            mock_client.table().upsert().execute = execute_mock
            mock_client.table().select().in_().execute.return_value = mock.MagicMock(data=[])
            mock_client.table().select().eq().execute.return_value = mock.MagicMock(data=[])

            service = SupabaseSyncService()
            with mock.patch.object(service, "_client", mock_client):
                result = service.push()

            # Only 1 upsert call (batch), not 2 separate calls.
            assert execute_mock.call_count == 1
            assert result.pushed >= 2

    def test_pull_with_mock_client(self):
        """Pull ingests remote rows as new local records."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            mock_client = mock.MagicMock()
            remote_rows = [
                {
                    "sync_version": 1,
                    "inspection_code": "REMOTE-001",
                    "fssai_license": "FSSAI-R1",
                    "fbo_name": "Remote FBO",
                    "fbo_address": "456 Remote St",
                    "concerned_food": "Remote Food",
                    "problem": "Remote Problem",
                    "inspection_date": "2025-06-01T10:00:00+00:00",
                    "compliance_deadline": "2025-07-01T10:00:00+00:00",
                    "fso_name": "Test FSO",
                    "local_id": None,
                }
            ]
            mock_client.table().select().execute.return_value = mock.MagicMock(data=remote_rows)

            service = SupabaseSyncService()
            with mock.patch.object(service, "_client", mock_client):
                result = service.pull()

            assert result.pulled >= 1

            pulled = Inspection.query.filter_by(inspection_code="REMOTE-001").first()
            assert pulled is not None
            assert pulled.fbo_name == "Remote FBO"

    def test_conflict_recorded_on_version_mismatch(self):
        """When local and remote versions diverge, a SyncConflict is created."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            insp = _make_inspection()
            db.session.add(insp)
            db.session.commit()

            state = SyncState(table_name="inspections", local_id=insp.id, sync_version=2)
            db.session.add(state)
            db.session.commit()

            mock_client = mock.MagicMock()
            # Remote has version 5 (diverges from local 2).
            mock_client.table().select().in_().execute.return_value = mock.MagicMock(
                data=[{"local_id": insp.id, "sync_version": 5}]
            )
            mock_client.table().select().eq().execute.return_value = mock.MagicMock(data=[{"sync_version": 5}])
            mock_client.table().select().execute.return_value = mock.MagicMock(data=[])

            service = SupabaseSyncService()
            with mock.patch.object(service, "_client", mock_client):
                result = service.push()

            conflict = SyncConflict.query.filter_by(table_name="inspections", local_id=insp.id).first()
            assert conflict is not None
            assert conflict.local_version == 2
            assert conflict.remote_version == 5
            assert conflict.direction == "push"
            assert result.conflicts >= 1

    def test_resolve_conflict_local_wins(self):
        """Resolving with winner='local' re-pushes the local version."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            insp = _make_inspection()
            db.session.add(insp)
            db.session.commit()

            conflict = SyncConflict(
                table_name="inspections",
                local_id=insp.id,
                local_version=3,
                remote_version=5,
                direction="push",
                remote_snapshot='{"id":1,"inspection_code":"X"}',
            )
            db.session.add(conflict)
            db.session.commit()
            conflict_id = conflict.id

            mock_client = mock.MagicMock()
            mock_client.table().upsert().execute.return_value = mock.MagicMock(data=[{"local_id": insp.id}])

            service = get_sync_service()
            with mock.patch.object(service, "_client", mock_client):
                result = service.resolve_conflict(conflict_id, "local")

            assert result.status == "ok"
            with db.session.no_autoflush:
                assert SyncConflict.query.get(conflict_id) is None

    def test_resolve_conflict_remote_wins(self):
        """Resolving with winner='remote' applies the remote snapshot locally."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            insp = _make_inspection()
            db.session.add(insp)
            db.session.commit()
            insp_id = insp.id

            conflict = SyncConflict(
                table_name="inspections",
                local_id=insp_id,
                local_version=3,
                remote_version=5,
                direction="pull",
                remote_snapshot=json.dumps({
                    "inspection_code": "CASE-RESOLVED",
                    "concerned_food": "Resolved Food",
                }),
            )
            db.session.add(conflict)
            db.session.commit()
            conflict_id = conflict.id

            service = get_sync_service()
            with mock.patch.object(service, "_client", None):
                result = service.resolve_conflict(conflict_id, "remote")

            assert result.status == "ok"
            with db.session.no_autoflush:
                assert SyncConflict.query.get(conflict_id) is None

            updated = db.session.get(Inspection, insp_id)
            assert updated.inspection_code == "CASE-RESOLVED"
            assert updated.concerned_food == "Resolved Food"

    def test_resolve_conflict_invalid_winner(self):
        app = _make_app(enable_sync=True)
        with app.app_context():
            conflict = SyncConflict(
                table_name="inspections",
                local_id=1,
                local_version=3,
                remote_version=5,
                direction="push",
            )
            db.session.add(conflict)
            db.session.commit()
            conflict_id = conflict.id

            service = get_sync_service()
            result = service.resolve_conflict(conflict_id, "invalid")
            assert result.status == "error"
            assert len(result.errors) == 1

    def test_resolve_conflict_not_found(self):
        app = _make_app(enable_sync=True)
        with app.app_context():
            service = get_sync_service()
            result = service.resolve_conflict(99999, "local")
            assert result.status == "error"
            assert len(result.errors) == 1

    def test_status_disabled(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            status = service.status()
            assert status["enabled"] is False
            assert status["client_connected"] is False
            assert "inspections" in status["row_counts"]

    def test_status_enabled(self):
        app = _make_app(enable_sync=True)
        with app.app_context():
            service = get_sync_service()
            with mock.patch.object(service, "_client", mock.MagicMock()):
                status = service.status()
            assert status["enabled"] is True
            assert status["client_connected"] is True

    def test_push_partial_on_error(self):
        """A push that partially succeeds returns 'partial' status."""
        app = _make_app(enable_sync=True)
        with app.app_context():
            insp1 = _make_inspection()
            insp2 = Inspection(
                inspection_code="INS-002",
                fso_name="Test FSO",
                fssai_license="FSSAI-002",
                fbo_name="Test FBO 2",
                fbo_address="789 Another St",
                concerned_food="Snacks",
                problem="Misbranding",
                inspection_date=datetime(2025, 3, 1),
                compliance_deadline=datetime(2025, 4, 1),
            )
            db.session.add_all([insp1, insp2])
            db.session.commit()

            service = SupabaseSyncService()
            mock_client = mock.MagicMock()
            call_count = [0]

            def upsert_side(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return mock.MagicMock(data=[{"local_id": insp1.id}])
                raise RuntimeError("network error")

            mock_client.table().upsert().execute.side_effect = upsert_side
            mock_client.table().select().in_().execute.return_value = mock.MagicMock(data=[])
            mock_client.table().select().eq().execute.return_value = mock.MagicMock(data=[])

            with mock.patch.object(service, "_client", mock_client):
                with mock.patch.object(SupabaseSyncService, "_BATCH_SIZE", 1):
                    result = service.push()

            assert result.status in ("partial", "error")
            assert len(result.errors) >= 1
            assert call_count[0] >= 2

    def test_sync_result_to_dict(self):
        result = SyncResult(
            status="partial",
            pushed=3,
            pulled=2,
            conflicts=1,
            errors=["err1"],
            detail={"model": "Inspection"},
        )
        d = result.to_dict()
        assert d["status"] == "partial"
        assert d["pushed"] == 3
        assert d["errors"] == ["err1"]

    def test_singleton_service(self):
        s1 = get_sync_service()
        s2 = get_sync_service()
        assert s1 is s2


# ---------------------------------------------------------------------------
# Route tests — disabled (default)
# ---------------------------------------------------------------------------


class TestSyncRoutesDisabled:
    @pytest.fixture
    def client(self):
        app = _make_app(enable_sync=False)
        return _authed_client(app)

    def test_index_returns_200(self, client):
        resp = client.get("/sync/")
        assert resp.status_code == 200

    def test_status_returns_200(self, client):
        resp = client.get("/sync/status")
        assert resp.status_code == 200

    def test_push_returns_503_when_disabled(self, client):
        resp = client.post("/sync/push")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "disabled"

    def test_pull_returns_503_when_disabled(self, client):
        resp = client.post("/sync/pull")
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["status"] == "disabled"

    def test_resolve_conflict_returns_503_when_disabled(self, client):
        resp = client.post("/sync/resolve-conflict/1", json={"winner": "local"})
        assert resp.status_code == 503

    def test_template_renders_disabled_message(self, client):
        resp = client.get("/sync/")
        assert b"Disabled" in resp.data


# ---------------------------------------------------------------------------
# Route tests — enabled (with mocked Supabase client)
# ---------------------------------------------------------------------------


class TestSyncRoutesEnabled:
    @pytest.fixture
    def enabled_client(self):
        app = _make_app(enable_sync=True)
        client = _authed_client(app)
        return client, app

    def test_index_renders_when_enabled(self, enabled_client):
        c, _ = enabled_client
        resp = c.get("/sync/")
        assert resp.status_code == 200
        assert b"Cloud Sync" in resp.data

    def test_status_json_when_enabled(self, enabled_client):
        c, _ = enabled_client
        resp = c.get("/sync/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        assert "row_counts" in data
        assert "dirty_counts" in data
        assert "pending_conflicts" in data

    def test_push_endpoint_success(self, enabled_client):
        c, app = enabled_client
        with app.app_context():
            service = get_sync_service()
            mock_client = mock.MagicMock()
            mock_client.table().upsert().execute.return_value = mock.MagicMock(data=[{"local_id": 1}])
            mock_client.table().select().in_().execute.return_value = mock.MagicMock(data=[])
            mock_client.table().select().eq().execute.return_value = mock.MagicMock(data=[])
            with mock.patch.object(service, "_client", mock_client):
                resp = c.post("/sync/push")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] in ("ok", "partial", "disabled")

    def test_pull_endpoint_success(self, enabled_client):
        c, _ = enabled_client
        resp = c.post("/sync/pull", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] in ("ok", "partial", "disabled")

    def test_resolve_conflict_endpoint(self, enabled_client):
        c, app = enabled_client
        with app.app_context():
            conflict = SyncConflict(
                table_name="inspections",
                local_id=1,
                local_version=3,
                remote_version=5,
                direction="push",
                remote_snapshot='{"inspection_code":"X"}',
            )
            db.session.add(conflict)
            db.session.commit()
            conflict_id = conflict.id

            service = get_sync_service()
            with mock.patch.object(service, "_client", mock.MagicMock()):
                resp = c.post(
                    f"/sync/resolve-conflict/{conflict_id}",
                    json={"winner": "local"},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_resolve_conflict_invalid_winner(self, enabled_client):
        c, app = enabled_client
        with app.app_context():
            conflict = SyncConflict(
                table_name="inspections",
                local_id=1,
                local_version=3,
                remote_version=5,
                direction="push",
            )
            db.session.add(conflict)
            db.session.commit()
            conflict_id = conflict.id
        resp = c.post(
            f"/sync/resolve-conflict/{conflict_id}",
            json={"winner": "bogus"},
        )
        assert resp.status_code == 400

    def test_resolve_conflict_not_found(self, enabled_client):
        c, _ = enabled_client
        resp = c.post(
            "/sync/resolve-conflict/99999",
            json={"winner": "local"},
        )
        assert resp.status_code == 404

    def test_push_logs_to_sync_log(self, enabled_client):
        c, app = enabled_client
        with app.app_context():
            service = get_sync_service()
            mock_client = mock.MagicMock()
            mock_client.table().upsert().execute.return_value = mock.MagicMock(data=[])
            mock_client.table().select().in_().execute.return_value = mock.MagicMock(data=[])
            mock_client.table().select().eq().execute.return_value = mock.MagicMock(data=[])
            with mock.patch.object(service, "_client", mock_client):
                c.post("/sync/push")

            log = SyncLog.query.order_by(SyncLog.id.desc()).first()
            assert log is not None
            assert log.operation == "push"


# ---------------------------------------------------------------------------
# Config / docs-parity tests
# ---------------------------------------------------------------------------


class TestPhase17Config:
    """Verify the Phase 17 settings declared in app/shared/config.py."""

    def test_enable_supabase_sync_declared(self):
        assert hasattr(cfg, "supabase_sync_enabled")
        assert cfg.get_bool("ENABLE_SUPABASE_SYNC") is False

    def test_supabase_url_declared(self):
        assert hasattr(cfg, "supabase_url")
        assert cfg.get_str("SUPABASE_URL") == ""

    def test_supabase_api_key_declared(self):
        assert hasattr(cfg, "supabase_api_key")
        assert cfg.get_str("SUPABASE_API_KEY") == ""

    def test_supabase_sync_interval_declared(self):
        assert hasattr(cfg, "supabase_sync_interval")
        assert cfg.supabase_sync_interval == 300


# ---------------------------------------------------------------------------
# Integration — no-network smoke test
# ---------------------------------------------------------------------------


class TestSyncNoNetwork:
    """Ensure the service never raises when supabase is unavailable."""

    def test_push_disabled_does_not_raise(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            result = service.push()
            assert result.status == "disabled"

    def test_pull_disabled_does_not_raise(self):
        app = _make_app()
        with app.app_context():
            service = get_sync_service()
            result = service.pull()
            assert result.status == "disabled"
