"""Best-effort audit-log caller seam (D7 deepening task).

The hash-chained core :func:`app.services.audit.log_audit` is already a deep
module — but every caller historically re-implemented the same shallow wrapper:
bind ``entity_type``, normalize ``actor`` from the request context, wrap in
``try/except`` so an audit failure never fails the operation, and log a
warning.  That wrapper was duplicated 3x (``annexure/routes.py``,
``evidence/routes.py``, ``document_lifecycle.py``) with inconsistent ``actor``
sources.

:func:`audit_logger` is the single factory for that wrapper.  Callers bind
once per module and never touch :func:`log_audit`'s five positional
parameters again:

    from app.services.audit_context import audit_logger

    _audit = audit_logger("annexure")   # bind entity_type once at import
    _audit.log(annexure.id, "ANNEXURE_DELETED", filename=annexure.filename)

Behaviour (identical to the wrappers it replaces):

* ``actor`` defaults to the authenticated user's username, ``"anonymous"``
  otherwise (an explicit ``actor=`` kwarg wins).  An inactive user also
  degrades to ``"anonymous"``.
* Any exception raised by the core writer — including the DB rollback +
  re-raise it performs on failure — is swallowed and logged at WARNING so
  the caller's operation proceeds ("best-effort" semantics).
* Details are passed through as keyword arguments and serialized to the
  ``details_json`` column by the core.
"""

from __future__ import annotations

import logging
from typing import Any

from flask_login import current_user

logger = logging.getLogger(__name__)

__all__ = ["AuditLogger", "audit_logger"]


class AuditLogger:
    """Best-effort audit writer bound to one ``entity_type``.

    Prefer the :func:`audit_logger` factory; instantiate this class directly
    only when dependency-injecting the core writer in tests.
    """

    def __init__(
        self,
        entity_type: str,
        *,
        writer=None,
    ) -> None:
        self._entity_type = entity_type
        # Stored unbound: when ``writer`` is not injected, the module-level
        # ``_default_writer`` is resolved at *call* time, so patching
        # ``app.services.audit_context._default_writer`` redirects every
        # instance (including module-level bindings) in tests.
        self._writer = writer

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def log(self, entity_id: Any, action: str, *, actor: str | None = None, **details: Any) -> None:
        """Record an audit event; never raises.

        Args:
            entity_id: Primary key of the audited record (str()-coerced to
                match the core writer's ``entity_id: str`` contract).
            action: Event name, e.g. ``"ANNEXURE_DELETED"``.
            actor: Explicit actor override; resolved from the request
                context when omitted.
            **details: Structured event details, stored as JSON.
        """
        try:
            (self._writer or _default_writer)(
                entity_type=self._entity_type,
                entity_id=str(entity_id),
                action=action,
                actor=actor if actor is not None else self._resolve_actor(),
                details=details,
            )
        except Exception:
            logger.warning(
                "Audit log write failed for %s %s (%s); continuing.",
                self._entity_type,
                entity_id,
                action,
            )

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_actor() -> str:
        """Current user's username, or ``"anonymous"`` outside a request."""
        try:
            if current_user.is_authenticated and current_user.is_active:
                return current_user.username
        except Exception:
            # No request context (Celery task, shell, ...) — flask-login's
            # current_user raises or is anonymous there.
            pass
        return "anonymous"

    @property
    def entity_type(self) -> str:
        """The bound entity type (read-only, for tests)."""
        return self._entity_type


def _default_writer(**kwargs: Any) -> None:
    """Import the hash-chained core lazily to avoid import cycles at app
    factory bootstrap (blueprints import this module before extensions are
    fully wired)."""
    from app.services.audit import log_audit

    log_audit(**kwargs)


def audit_logger(entity_type: str) -> AuditLogger:
    """Return an :class:`AuditLogger` bound to a fixed ``entity_type``.

    Usage::

        _audit = audit_logger("annexure")
        _audit.log(annexure.id, "ANNEXURE_DELETED", filename=annexure.filename)
    """
    return AuditLogger(entity_type)
