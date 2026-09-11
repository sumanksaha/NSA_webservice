"""D7 audit caller seam — ``app.services.audit_context``.

Thin, best-effort caller-facing layer on top of the hash-chained audit core
(``app.services.audit``). Centralises the three concerns that were previously
*duplicated* across route modules as ad-hoc ``log_audit`` wrappers:

* **entity-type binding** — ``audit_logger("annexure")`` returns an
  ``AuditLogger`` bound to that entity type, so callers never pass it again;
* **actor normalization** — the actor is resolved from flask-login's
  ``current_user`` (authenticated + active → ``username``, otherwise
  ``"anonymous"``), with an explicit ``actor=`` kwarg override; resolution
  tolerates ``current_user`` being unavailable or raising (Celery tasks,
  shell sessions, unit tests without a request context);
* **error swallowing** — audit writes are best-effort: any failure of the
  shared writer (transient DB error, core ``log_audit`` raising, etc.) is
  contained so the originating route/save operation is never aborted.

Patch surface (for tests)::

    patch("app.services.audit_context._default_writer")
    patch("app.services.audit_context.current_user", stub)

``_default_writer`` is resolved *at call time* (a bare module-global lookup
inside ``AuditLogger.log``), so a single patch point redirects every instance
— including long-lived module-level bindings such as
``app.annexure.routes._audit = audit_logger("annexure")``.
"""

from __future__ import annotations

import contextlib
from typing import Any

from flask_login import current_user

__all__ = ["AuditLogger", "audit_logger", "_default_writer"]


def _resolve_actor() -> str:
    """Resolve the audit actor from the current flask request.

    Returns the authenticated user's ``username`` when the user is both
    authenticated and active; otherwise ``"anonymous"``.  Any failure while
    touching ``current_user`` (no request context, flask-login raising, a
    stub whose property blows up) degrades to ``"anonymous"`` — auditing is
    best-effort and must never crash the caller.
    """
    try:
        user = current_user
        if user is not None and user.is_authenticated and getattr(user, "is_active", False):
            name = getattr(user, "username", None)
            if name:
                return str(name)
    except Exception:
        # ``current_user`` access can raise outside a request context
        # (e.g. inside a Celery task); fall through to "anonymous".
        pass
    return "anonymous"


def _default_writer(
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    details: dict,
) -> None:
    """Write a single audit entry to the hash-chained core.

    Delegates to :func:`app.services.audit.log_audit`.  The import is lazy so
    that patching ``app.services.audit.log_audit`` (used by the
    ``test_real_core_failure_is_swallowed`` path) takes effect at call time,
    and to keep ``audit_context`` importable before the DB/extensions are
    wired up.
    """
    from app.services.audit import log_audit

    log_audit(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        details=details,
    )


class AuditLogger:
    """Caller-facing audit logger bound to a single ``entity_type``.

    Constructed either directly (``AuditLogger("case_file")``) or via the
    :func:`audit_logger` factory, which is the form used by route modules.
    """

    __slots__ = ("entity_type",)

    def __init__(self, entity_type: str) -> None:
        self.entity_type = entity_type

    def log(self, entity_id: Any, action: str, **details: Any) -> None:
        """Log ``action`` for ``entity_id`` with best-effort resilience.

        ``entity_id`` is ``str()``-coerced (the core contract).  The reserved
        ``actor`` kwarg, if supplied and truthy, overrides actor resolution;
        otherwise the actor is derived from ``current_user``.  Everything is
        written through :data:`_default_writer`, whose failures are swallowed.

        The writer is referenced as a bare module global (``_default_writer``)
        rather than captured at construction, so patching
        ``app.services.audit_context._default_writer`` redirects every
        instance, including pre-existing module-level bindings.
        """
        actor = details.pop("actor", None) or _resolve_actor()
        entity_id = str(entity_id)
        try:
            _default_writer(
                entity_type=self.entity_type,
                entity_id=entity_id,
                action=action,
                actor=actor,
                details=details,
            )
        except Exception:
            # Audit is best-effort: never propagate a writer failure to the
            # route / save operation that triggered this log entry.
            with contextlib.suppress(Exception):
                pass


def audit_logger(entity_type: str) -> AuditLogger:
    """Return an :class:`AuditLogger` bound to ``entity_type``.

    Convenience factory used by route modules so the entity type reads at the
    call site: ``audit_logger("photo").log(image_id, "UPLOAD_RECEIVED", ...)``.
    """
    return AuditLogger(entity_type)
