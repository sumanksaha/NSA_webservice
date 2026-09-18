"""RBAC seam (Phase 18).

Single source of truth for role names and what each role may reach.
The `role` / `user_roles` tables predate this module; nothing read them
before Phase 18 was wired up here.

Design decision (2026-08-26): access changes ship with deploys via the
:class:`ROLE_BLUEPRINTS` map — no permissions admin UI until roles multiply
beyond `admin` / `fso`.
"""

from __future__ import annotations

from flask import current_app, flash, redirect, request, url_for
from flask_login import current_user

ADMIN_ROLE = "admin"
FSO_ROLE = "fso"

#: Blueprints each non-admin role may reach. Admins bypass the map entirely.
#: Blueprint name keys match Flask blueprint names as used in
#: ``request.blueprint`` (see app/__init__.py registrations).
ROLE_BLUEPRINTS: dict[str, set[str]] = {
    FSO_ROLE: {
        "case_file_generator",  # Sample adjudication
        "adjudication",  # Non-sample adjudication
        "notepad",
        "inspection",
        "workdiary",
        "comments",
    },
}


#: Blueprints every authenticated user may reach regardless of role
#: (account self-service + infrastructure endpoints).
ALWAYS_ALLOWED_BLUEPRINTS = {"auth", "static", "health", "tasks_webhook"}


def register_auth_gates(app, public_endpoints: set[str]) -> None:
    """Wire the global auth gates onto ``app`` (Flask ``before_request``).

    Single home for access-policy wiring (moved from ``app/__init__.py`` by
    the 2026-09-12 review so the factory stops carrying auth logic). The
    ``public_endpoints`` set itself stays in the factory — a CI gate
    (``tests/test_cicd_gates.py``) greps ``app/__init__.py`` for
    ``health.health`` to keep the Render probe public.

    Gates run in registration order: audit-user stamping (registered by the
    factory before this), then the login gate, then the RBAC role gate.
    """

    @app.before_request
    def require_login():
        if request.endpoint and request.endpoint not in public_endpoints and not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))

    @app.before_request
    def enforce_rbac():
        """Phase 18 role gate — deny-by-default beyond ALWAYS_ALLOWED.

        Redirects (with an explanatory flash) instead of a dead 403 page;
        nav links for disallowed blueprints are hidden in base.html so this
        mostly guards direct URLs and stale bookmarks.
        """
        if current_app.config.get("DISABLE_RBAC"):
            return None
        if not current_user.is_authenticated:
            return None
        endpoint = request.endpoint
        if not endpoint or endpoint in public_endpoints:
            return None
        if blueprint_allowed(current_user, request.blueprint):
            return None
        flash("You do not have access to that section.", "error")
        return redirect(landing_endpoint(current_user))


def blueprint_allowed(user, blueprint_name: str | None) -> bool:
    """True when ``user`` may reach ``blueprint_name``.

    Admins bypass the map. Unauthenticated callers are the login gate's
    problem, not ours (treated as allowed here so the two gates compose).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return True
    if getattr(user, "is_admin", False):
        return True
    if blueprint_name is None or blueprint_name in ALWAYS_ALLOWED_BLUEPRINTS:
        return True
    return any(blueprint_name in ROLE_BLUEPRINTS.get(role.name, set()) for role in user.roles)


def landing_endpoint(user) -> str:
    """URL a user is sent to after login / when a blocked route bounces them."""
    return url_for("case_file_generator.index")


def scoped_officer_name(user) -> str | None:
    """The officer name a user's queries are restricted to, or ``None`` for no restriction.

    - Admins → ``None`` (see everything).
    - Unauthenticated → ``None`` (the login gate owns them).
    - Non-admins → their bound ``fso_name``; an unbound non-admin gets
      ``""`` (matches nothing — deny-by-default).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_admin", False):
        return None
    return getattr(user, "fso_name", None) or ""


def case_visible_to_user(user, case_type: str, case_id: int) -> bool:
    """Record-level scope check for a CaseFile/Adjudication (and its children).

    Unknown ids return ``False`` so callers can fail closed with a 404.
    """
    from app.extensions import db
    from app.models import Adjudication, CaseFile

    model = CaseFile if case_type == "case_file" else Adjudication
    case = db.session.get(model, case_id)
    if case is None:
        return False
    scope = scoped_officer_name(user)
    if scope is None:
        return True
    officer = case.food_safety_officer_name if case_type == "case_file" else case.food_safety_officer
    return officer == scope


def ensure_roles() -> None:
    """Create the base roles if missing. Idempotent."""
    from app.extensions import db
    from app.models import Role

    for name in (ADMIN_ROLE, FSO_ROLE):
        if db.session.query(Role).filter_by(name=name).count() == 0:
            db.session.add(Role(name=name))
    db.session.commit()


def backfill_admin_role() -> None:
    """Grant ADMIN_ROLE to every existing user lacking it. Idempotent.

    Migration backfill so no legacy account is locked out once the role
    gate starts denying non-admin roles.
    """
    from app.extensions import db
    from app.models import Role, User

    admin_role = Role.query.filter_by(name=ADMIN_ROLE).one()
    for user in db.session.query(User).all():
        if not user.has_role(ADMIN_ROLE):
            user.roles.append(admin_role)
    db.session.commit()
