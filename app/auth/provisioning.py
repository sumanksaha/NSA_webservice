"""FSO account provisioning (Phase 18 RBAC).

Single seam for creating bound `fso`-role accounts — used by the admin
Add User form and by the bulk seed script. Rules enforced here:

- the FSO name must exist in the ``fso`` table (the 21 canonical names);
- one FSO ↔ one active account: a second binding raises
  :class:`ProvisioningError` ("already bound");
- every created account receives the ``fso`` role.
"""

from __future__ import annotations

from werkzeug.security import generate_password_hash

from app.shared.rbac import FSO_ROLE, ensure_roles


class ProvisioningError(Exception):
    """Raised when an account cannot be provisioned (user-facing message)."""


def create_fso_account(username: str, fso_name: str, password: str, *, creator_id: int | None = None):
    """Create a bound fso-role account and return the new :class:`User`."""
    from flask_login import current_user

    from app.extensions import db
    from app.models import FSO, Role, User

    username = (username or "").strip()
    fso_name = (fso_name or "").strip()

    if not username:
        raise ProvisioningError("Username is required.")
    if User.query.filter_by(username=username).first() is not None:
        raise ProvisioningError(f"Username '{username}' is already taken.")
    if db.session.query(FSO).filter_by(fso_name=fso_name).count() == 0:
        raise ProvisioningError(f"Unknown FSO '{fso_name}'.")
    if User.query.filter_by(fso_name=fso_name).count() > 0:
        raise ProvisioningError(f"FSO '{fso_name}' is already bound to another account.")

    ensure_roles()
    fso_role = Role.query.filter_by(name=FSO_ROLE).one()

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        is_admin=False,
        fso_name=fso_name,
    )
    user.roles.append(fso_role)
    db.session.add(user)
    db.session.commit()

    try:
        creator = creator_id if creator_id is not None else current_user.id
        from app.auth.routes import _log_user_audit

        _log_user_audit("fso_user_created", creator, user.id)
    except Exception:  # audit is best-effort
        pass
    return user


def seed_fso_users(default_password: str) -> dict:
    """Create bound fso accounts for every not-yet-bound FSO. Idempotent.

    Returns ``{"created": [names], "skipped": [names]}``.
    """
    from app.utils.fso_data import get_all_fso_names

    created: list[str] = []
    skipped: list[str] = []
    for name in get_all_fso_names():
        try:
            create_fso_account(
                username=name.lower().replace(" ", "."),
                fso_name=name,
                password=default_password,
            )
            created.append(name)
        except ProvisioningError:
            skipped.append(name)
    return {"created": created, "skipped": skipped}
