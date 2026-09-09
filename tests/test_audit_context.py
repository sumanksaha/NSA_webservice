"""Tests for the D7 audit caller seam — ``app/services/audit_context.py``.

Verifies the factory contract from task.md D7:

* ``audit_logger(entity_type)`` binds ``entity_type`` once;
* ``actor`` is normalized from flask-login's ``current_user`` (authenticated
  username, ``"anonymous"`` otherwise, explicit override wins);
* core-writer failures are swallowed (best-effort — never fails the caller);
* details are passed through to the hash-chained core, end to end.

Mock surface: the shared writer is patched at
``app.services.audit_context._default_writer`` (call-time resolved, so one
patch point redirects every instance, including module-level bindings).
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import AuditLog
from app.services.audit import verify_audit_chain
from app.services.audit_context import AuditLogger, audit_logger


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def test_app():
    """Create a minimal Flask app with an in-memory SQLite database."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_PDF_GENERATION"] = "1"
    with app.app_context():
        db.drop_all()
        db.create_all()
        with app.test_request_context():
            yield app
        db.session.remove()
        db.drop_all()


def _login_as(username="alice", authenticated=True, active=True):
    """Replace the module-level ``current_user`` with a plain stub.

    Patching ``app.services.audit_context.current_user`` (the name the
    resolution logic actually reads) keeps the test independent of
    flask-login internals across versions.
    """
    stub = SimpleNamespace(
        is_authenticated=authenticated,
        is_active=active,
        username=username,
    )
    return patch("app.services.audit_context.current_user", stub)


# --------------------------------------------------------------------------- #
# Factory binding                                                             #
# --------------------------------------------------------------------------- #


class TestFactoryBinding:
    def test_binds_entity_type(self):
        logger = audit_logger("annexure")
        assert isinstance(logger, AuditLogger)
        assert logger.entity_type == "annexure"

    def test_each_binding_is_independent(self):
        assert audit_logger("evidence").entity_type == "evidence"
        assert audit_logger("case_file").entity_type == "case_file"

    def test_entity_id_coerced_to_str_and_details_passed_through(self):
        """entity_id is str()-coerced (core contract) and kwargs become the
        details dict verbatim."""
        with patch("app.services.audit_context._default_writer") as writer:
            audit_logger("annexure").log(42, "ANNEXURE_UPLOADED", filename="a.pdf", letter="A")

        kwargs = writer.call_args.kwargs
        assert kwargs["entity_type"] == "annexure"
        assert kwargs["entity_id"] == "42"
        assert kwargs["action"] == "ANNEXURE_UPLOADED"
        assert kwargs["details"] == {"filename": "a.pdf", "letter": "A"}


# --------------------------------------------------------------------------- #
# Actor normalization                                                         #
# --------------------------------------------------------------------------- #


class TestActorNormalization:
    def test_actor_from_authenticated_user(self):
        with _login_as(username="alice"):
            with patch("app.services.audit_context._default_writer") as writer:
                audit_logger("annexure").log("x1", "ANNEXURE_DELETED")

        assert writer.call_args.kwargs["actor"] == "alice"

    def test_actor_anonymous_when_not_authenticated(self):
        with _login_as(authenticated=False):
            with patch("app.services.audit_context._default_writer") as writer:
                audit_logger("annexure").log("x1", "ANNEXURE_DELETED")

        assert writer.call_args.kwargs["actor"] == "anonymous"

    def test_actor_anonymous_when_user_inactive(self):
        with _login_as(username="alice", active=False):
            with patch("app.services.audit_context._default_writer") as writer:
                audit_logger("annexure").log("x1", "ANNEXURE_DELETED")

        assert writer.call_args.kwargs["actor"] == "anonymous"

    def test_actor_anonymous_when_current_user_raises(self):
        """Outside a request context (Celery task, shell) flask-login's
        current_user can raise on attribute access — degrade, don't crash."""

        class Boom:
            @property
            def is_authenticated(self):
                raise RuntimeError("no request context")

        with patch("app.services.audit_context.current_user", Boom()):
            with patch("app.services.audit_context._default_writer") as writer:
                audit_logger("annexure").log("x1", "ANNEXURE_DELETED")

        assert writer.call_args.kwargs["actor"] == "anonymous"

    def test_explicit_actor_overrides_current_user(self):
        with _login_as(username="alice"):
            with patch("app.services.audit_context._default_writer") as writer:
                audit_logger("annexure").log("x1", "ANNEXURE_DELETED", actor="celery-bot")

        assert writer.call_args.kwargs["actor"] == "celery-bot"


# --------------------------------------------------------------------------- #
# Best-effort error swallowing                                                #
# --------------------------------------------------------------------------- #


class TestBestEffortSwallowing:
    def test_shared_writer_failure_is_swallowed(self):
        with patch(
            "app.services.audit_context._default_writer",
            side_effect=RuntimeError("DB exploded"),
        ):
            audit_logger("annexure").log("x1", "ANNEXURE_DELETED")  # must not raise

    def test_real_core_failure_is_swallowed(self):
        """Patching the core ``log_audit`` itself (the lazy-import path) must
        also be contained — this is exactly the triplicated wrapper's
        behaviour that D7 replaces."""
        with patch("app.services.audit.log_audit", side_effect=RuntimeError("chain broken")):
            audit_logger("evidence").log("ev1", "EVIDENCE_DELETED")  # must not raise

    def test_swallowing_does_not_fail_the_caller_operation(self):
        """The route-layer motivation: a failed audit write must not abort the
        request that triggered it."""
        completed = False
        with patch("app.services.audit_context._default_writer", side_effect=OSError("disk full")):
            audit_logger("annexure").log("x1", "ANNEXURE_RENAMED", caption="new")
            completed = True
        assert completed is True


# --------------------------------------------------------------------------- #
# End-to-end with the hash-chained core                                       #
# --------------------------------------------------------------------------- #


class TestEndToEndWithCore:
    def test_row_persisted_and_details_serialized(self, test_app):
        """Real writer, real DB: the row lands with the bound entity_type,
        anonymous actor (no login in the fixture), and JSON-serialized
        details."""
        audit_logger("annexure").log("ann-1", "ANNEXURE_UPLOADED", filename="a.pdf", letter="A")

        row = AuditLog.query.filter_by(entity_id="ann-1").one()
        assert row.entity_type == "annexure"
        assert row.action == "ANNEXURE_UPLOADED"
        assert row.actor == "anonymous"
        assert json.loads(row.details_json) == {"filename": "a.pdf", "letter": "A"}

    def test_hash_chain_stays_verifiable(self, test_app):
        """Two events on the same entity must form a verifiable chain."""
        audit_logger("annexure").log("ann-1", "ANNEXURE_UPLOADED", filename="a.pdf")
        audit_logger("annexure").log("ann-1", "ANNEXURE_DELETED", filename="a.pdf")

        assert verify_audit_chain("ann-1") is True
        assert AuditLog.query.filter_by(entity_id="ann-1").count() == 2
