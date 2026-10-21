"""Sentry error tracking — one guarded init, one scrubber, zero call-site edits.

Why this earns its keep here (spec §3.3, research §3): the codebase
**deliberately swallows exceptions**. Ruff's ``S110``/``S112`` are ignored
project-wide (``pyproject.toml``), and the RAG loggers describe themselves as
"best-effort … logged and swallowed". Those warnings and errors already exist —
the SDK's default ``LoggingIntegration`` turns them into grouped, stack-traced,
owned issues *without editing a single call site*. That is the whole win of this
module; do not "improve" it by sprinkling ``capture_exception()`` into existing
``except`` blocks. Deliberate degradation is the design, and Sentry stays a pure
consumer of the signals the app already emits.

Three SDK facts shape this module. All three were verified against the installed
``sentry-sdk==2.72.0`` (spec §16):

1. **``dsn=None`` is not an off switch.** ``client._get_options()`` ends with
   ``if rv["dsn"] is None: rv["dsn"] = os.environ.get("SENTRY_DSN")``. A blank
   DSN must therefore *skip* ``init()`` entirely: no DSN, no transport, no
   network. (An empty *string* also yields ``transport is None``, but skipping
   is cheaper and leaves no client on the global hub.)
2. **A malformed DSN raises.** ``BadDsn`` comes out of ``Transport.__init__``
   unguarded, so ``init()`` is wrapped: a typo in a dashboard field must never
   take a deploy down (precedent: ``app/utils/logging.py``).
3. **There is no ``error_sample_rate`` option.** ``traces_sample_rate`` governs
   tracing only; errors are always reported, which is what this slice wants.

Tracing stays off in this slice (spec INV-11): ``FlaskIntegration`` patches
``Flask.__call__``, and ``a2wsgi`` invokes exactly that entry point, so one
request can produce two server transactions (outer ASGI, inner WSGI).
De-duplication is the precondition for turning tracing on, not a follow-up.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["init_sentry"]

logger = logging.getLogger(__name__)

#: Replacement written in place of anything identifying.
REDACTED = "[Filtered]"

#: Keys whose *value* is replaced outright, at any nesting depth. Chosen for
#: this domain: FSSAI queries are legal questions about named firms, so the
#: identifying data is not just credentials — it is firm names, licence
#: numbers and query text. The SDK's own denylist (33 entries: ``password``,
#: ``token``, ``authorization``, …) covers credentials but none of these.
SENSITIVE_KEYS = frozenset({
    # Statutory identifiers and firm/person identity.
    "fso_name",
    "officer_name",
    "full_name",
    "license_no",
    "license_number",
    "licence_no",
    "licence_number",
    "registration_no",
    "registration_number",
    "fssai_number",
    "fssai_licence",
    "gstin",
    "pan",
    "aadhaar",
    # Contact and location.
    "address",
    "full_address",
    "email",
    "phone",
    "mobile",
    # Query / answer text and audit material.
    "query",
    "query_text",
    "question",
    "prompt",
    "response_text",
    "audit_body",
    "chain_hash",
    "content_hash",
    # Credentials the built-in denylist may miss inside nested containers.
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "x-api-key",
    "token",
    "password",
    "secret",
})

#: Headers scrubbed from the captured request (the body is dropped entirely).
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key"})

#: Request keys removed outright — where licence numbers and queries live.
DROPPED_REQUEST_KEYS = ("data", "cookies", "query_string")

#: User keys dropped. ``id``/``username`` are kept: the issue's own guidance is
#: "prefer id/username over email", and an opaque id is the least we can bind.
DROPPED_USER_KEYS = ("email", "ip_address", "name", "geo")

#: Exceptions that are noise, not incidents (spec §5.1). Also passed as the
#: SDK's ``ignore_errors`` so they never reach the transport.
IGNORED_EXCEPTIONS = (
    "werkzeug.exceptions.NotFound",
    "werkzeug.exceptions.Unauthorized",
)

_ROLE_ANONYMOUS = "anonymous"
_ROLE_ADMIN = "admin"
_ROLE_USER = "user"

_MAX_TAG_LENGTH = 64

#: Process-level latch. ``create_app()`` runs twice in the ASGI process
#: (``app/__init__.py``'s module-level ``app`` and ``asgi.py``'s explicit call),
#: and ``init()`` spawns a backpressure-monitor thread per client — so the
#: second call must not build a second client. Deliberately latched only on a
#: *successful* init: while the DSN is blank we stay uninitialised, so a later
#: call (env loaded from a different source) can still bring Sentry up.
_initialised = False


def init_sentry() -> None:
    """Initialise error tracking once per process. **Never raises.**

    Returns immediately, without importing or calling ``sentry_sdk.init``, when
    the DSN is blank — that is the supported off switch (see module docstring,
    fact 1). Safe to call from ``create_app()`` on every boot.
    """
    global _initialised

    if _initialised:
        return

    # Local import: keeps ``app``'s import graph free of this module, and keeps
    # ``app.shared.config`` from being pulled in before the factory needs it.
    from app.shared.config import cfg

    dsn = (cfg.sentry_dsn or "").strip()
    if not dsn:
        # No DSN => no transport => no network egress. Do not latch: a later
        # call with a DSN configured must still be able to initialise.
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.scrubber import EventScrubber

        sentry_sdk.init(
            dsn=dsn,
            # The SDK's get_default_release() knows Heroku/Vercel/CircleCI/
            # GitLab/GAE — not Render. Pass it explicitly or every issue is
            # filed against an unknown release.
            release=f"nsa-webservice@{os.environ.get('RENDER_GIT_COMMIT', 'dev')}",
            environment=cfg.sentry_environment or ("production" if os.environ.get("RENDER") else "development"),
            # Hard-wired, not a flag: an FSSAI/legal-records app is the last
            # place to make PII capture a configuration choice (spec D5, FR-7).
            # NOTE (sentry-sdk 2.72.0): passing this emits a DeprecationWarning
            # pointing at `data_collection`. We keep it anyway, deliberately:
            # `data_collection` *opts into* collection ("most categories are
            # collected" for any omitted field), whereas `send_default_pii=False`
            # makes the SDK derive conservative behaviour. Switching would be a
            # privacy regression dressed as a modernisation.
            send_default_pii=False,
            max_request_body_size="never",
            # Errors only by default (0.0). No traces_sampler is added.
            traces_sample_rate=cfg.sentry_traces_sample_rate,
            integrations=[FlaskIntegration(http_methods_to_capture=("GET", "POST", "PUT", "PATCH", "DELETE"))],
            ignore_errors=list(IGNORED_EXCEPTIONS),
            # Belt-and-braces alongside before_send: the built-in denylist is
            # NOT recursive by default (verified: ``recursive: bool = False``).
            event_scrubber=EventScrubber(recursive=True),
            before_send=before_send,
            # enable_backpressure_handling left at its default (True).
        )
    except Exception:
        logger.warning("Sentry init failed; error tracking disabled.", exc_info=True)
        return

    _initialised = True
    logger.info(
        "Sentry error tracking enabled (environment=%s, traces_sample_rate=%s).",
        cfg.sentry_environment or ("production" if os.environ.get("RENDER") else "development"),
        cfg.sentry_traces_sample_rate,
    )


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Sentry hook: drop noise, then scrub identifiers before anything leaves.

    Returning ``None`` drops the event. This runs for *every* event, including
    the ones the logging integration synthesises from existing
    ``logger.warning(...)`` calls — which is where most of the value is, since
    the codebase logs-and-swallows rather than raising.

    Residual risk, stated rather than hidden: free text is not scrubbed
    (an exception *message* or log line could quote a licence number verbatim).
    Key-based redaction plus a body-less request is the achievable boundary;
    scrubbing prose would also destroy the debuggability that justifies Sentry.
    """
    del hint  # accepted per the SDK contract; unused
    if is_ignored_exception(event):
        return None
    return scrub_event(event)


def is_ignored_exception(event: dict[str, Any]) -> bool:
    """True for 404/401s and CSRF failures — noise, not incidents."""
    for value in (event.get("exception") or {}).get("values") or []:
        module = str(value.get("module") or "")
        name = str(value.get("type") or "")
        if module.startswith("werkzeug.exceptions"):
            return True
        if module.startswith("flask_wtf") or "CSRF" in name.upper():
            return True
    return False


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return *event* with bodies, secrets and identifiers removed (spec §9)."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in DROPPED_REQUEST_KEYS:
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for header in list(headers):
                if header.lower() in SENSITIVE_HEADERS:
                    headers[header] = REDACTED

    user = event.get("user")
    if isinstance(user, dict):
        for key in DROPPED_USER_KEYS:
            user.pop(key, None)

    for key in ("extra", "contexts", "tags", "logentry", "breadcrumbs", "modules"):
        if key in event:
            event[key] = scrub_value(event[key])

    return event


def scrub_value(value: Any) -> Any:
    """Recursively replace sensitive *keys*' values with :data:`REDACTED`."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in SENSITIVE_KEYS else scrub_value(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item) for item in value)
    return value


def actor_role_for(user: Any) -> str:
    """Bounded, non-identifying role label for the audit context.

    Never a name, never a firm: ``anonymous`` / ``admin`` / ``user`` only.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return _ROLE_ANONYMOUS
    return _ROLE_ADMIN if getattr(user, "is_admin", False) else _ROLE_USER


def bind_request_context(user: Any = None, request_id: str = "") -> None:
    """Bind request correlation onto the active Sentry scope.

    ``request_id`` becomes a tag (indexed — searchable in the UI); the audit
    context is not indexed, which is why the role lives there. No-op unless
    Sentry is actually initialised, so it costs a boolean check per request when
    error tracking is off.

    The app has no server-generated request id today, so this binds an inbound
    ``X-Request-ID`` when a client or proxy supplies one and stays silent
    otherwise — it never fabricates an id that correlates to nothing.
    """
    if not _initialised:
        return

    import sentry_sdk

    if request_id:
        sentry_sdk.set_tag("request_id", request_id[:_MAX_TAG_LENGTH])
    sentry_sdk.set_context("audit", {"actor_role": actor_role_for(user)})
