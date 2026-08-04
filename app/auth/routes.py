from datetime import UTC, datetime
from urllib.parse import urlparse

from flask import Response, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.auth import auth_bp
from app.extensions import db
from app.models import RecordAudit, User


def _is_safe_redirect_url(target):
    """Validate that a redirect URL is safe — only relative paths are accepted.
    Prevents open-redirect attacks that forward to external domains.
    """
    if not target:
        return False
    parsed = urlparse(target)
    # A relative URL has no scheme and no netloc; only those are safe.
    return not parsed.scheme and not parsed.netloc


def _log_login_event(action, user_id=None):
    """Record a login_success or login_failed event to the audit log."""
    try:
        entry = RecordAudit(
            user_id=user_id,
            action=action,
            record_type="auth",
            record_id=str(user_id) if user_id else "anonymous",
            timestamp=datetime.now(UTC),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Audit logging must never break the login flow — swallow errors


def _log_user_audit(action, actor_id, target_user_id):
    """Record an admin action against a user (e.g. password reset)."""
    try:
        entry = RecordAudit(
            user_id=actor_id,
            action=action,
            record_type="user",
            record_id=str(target_user_id),
            timestamp=datetime.now(UTC),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Audit logging must never break the admin flow — swallow errors


def _guard_admin_action(target, self_message, last_admin_message) -> Response | None:
    """Block self-actions and last-admin actions in user management.

    Shared by ``toggle_admin`` and ``delete_user``. Returns a redirect
    response when the action is blocked, otherwise ``None`` (caller may
    proceed). The last-admin check is defense-in-depth: given
    ``@admin_required`` plus the self-guard above, it is unreachable by
    construction (a different target being an admin implies >= 2 admins), but
    it protects the invariant that the system always keeps at least one
    administrator if the guard order ever changes.
    """
    if target.id == current_user.id:
        flash(self_message, "error")
        return redirect(url_for("auth.users"))

    if target.is_admin:
        admin_count = User.query.filter_by(is_admin=True).count()
        if admin_count <= 1:
            flash(last_admin_message, "error")
            return redirect(url_for("auth.users"))

    return None


from app.utils.auth import admin_required


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # If already logged in, redirect to the main app
    if current_user.is_authenticated:
        return redirect(url_for("case_file_generator.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("auth/login.html")

        user = User.query.filter_by(username=username).first()

        if user is None or not check_password_hash(user.password_hash, password):
            _log_login_event("login_failed")
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        login_user(user, remember=False)
        session.permanent = True  # Activate PERMANENT_SESSION_LIFETIME

        _log_login_event("login_success", user.id)

        # Safe next-page redirect
        next_page = request.args.get("next")
        if next_page and _is_safe_redirect_url(next_page):
            return redirect(next_page)

        return redirect(url_for("case_file_generator.index"))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Authenticated self-service password change.

    Requires the current password, enforces a minimum length, and records an
    audit event. The session is preserved — the user stays logged in.
    """
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not current or not new_password or not confirm:
            flash("All fields are required.", "error")
            return render_template("auth/change_password.html")

        if not check_password_hash(current_user.password_hash, current):
            _log_login_event("pwd_change_failed", current_user.id)
            flash("Current password is incorrect.", "error")
            return render_template("auth/change_password.html")

        if len(new_password) < 8:
            flash("New password must be at least 8 characters long.", "error")
            return render_template("auth/change_password.html")

        if new_password != confirm:
            flash("New password and confirmation do not match.", "error")
            return render_template("auth/change_password.html")

        if check_password_hash(current_user.password_hash, new_password):
            flash("New password must be different from the current password.", "error")
            return render_template("auth/change_password.html")

        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        _log_login_event("pwd_changed", current_user.id)
        flash("Password changed successfully.", "success")
        return redirect(url_for("auth.change_password"))

    return render_template("auth/change_password.html")


@auth_bp.route("/users")
@login_required
@admin_required
def users():
    """Admin user management: list all accounts with a reset action."""
    all_users = User.query.order_by(User.username.asc()).all()
    return render_template("auth/users.html", users=all_users)


@auth_bp.route("/users/create", methods=["GET", "POST"])
@login_required
@admin_required
def create_user():
    """Admin creates a new user account from the UI (no CLI script needed)."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        make_admin = request.form.get("is_admin") == "on"

        if not username or not password or not confirm:
            flash("All fields are required.", "error")
            return render_template("auth/create_user.html")

        if len(username) > 80:
            flash("Username must be 80 characters or fewer.", "error")
            return render_template("auth/create_user.html")

        existing = User.query.filter_by(username=username).first()
        if existing is not None:
            flash(f"Username '{username}' is already taken.", "error")
            return render_template("auth/create_user.html")

        if len(password) < 8:
            flash("New password must be at least 8 characters long.", "error")
            return render_template("auth/create_user.html")

        if password != confirm:
            flash("New password and confirmation do not match.", "error")
            return render_template("auth/create_user.html")

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=make_admin,
        )
        db.session.add(user)
        db.session.commit()
        _log_user_audit("user_created", current_user.id, user.id)
        role_desc = "admin" if make_admin else "user"
        flash(f"User '{username}' created ({role_desc}).", "success")
        return redirect(url_for("auth.users"))

    return render_template("auth/create_user.html")


@auth_bp.route("/users/<int:user_id>/reset-password", methods=["GET", "POST"])
@login_required
@admin_required
def reset_password(user_id):
    """Admin sets a new password for another user (no current password needed)."""
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not new_password or not confirm:
            flash("All fields are required.", "error")
            return render_template("auth/reset_password.html", target=target)

        if len(new_password) < 8:
            flash("New password must be at least 8 characters long.", "error")
            return render_template("auth/reset_password.html", target=target)

        if new_password != confirm:
            flash("New password and confirmation do not match.", "error")
            return render_template("auth/reset_password.html", target=target)

        target.password_hash = generate_password_hash(new_password)
        db.session.commit()
        _log_user_audit("admin_pwd_reset", current_user.id, target.id)
        flash(f"Password reset for {target.username}.", "success")
        return redirect(url_for("auth.users"))

    return render_template("auth/reset_password.html", target=target)


@auth_bp.route("/users/<int:user_id>/toggle-admin", methods=["POST"])
@login_required
@admin_required
def toggle_admin(user_id):
    """Grant or revoke admin rights for another user.

    Self-actions and last-admin demotions are blocked by ``_guard_admin_action``
    so an admin can never lock themselves out; the change is written to the
    audit log.
    """
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)

    blocked = _guard_admin_action(
        target,
        "You cannot change your own admin role.",
        "Cannot demote the last admin account.",
    )
    if blocked is not None:
        return blocked

    target.is_admin = not target.is_admin
    db.session.commit()
    action = "admin_promoted" if target.is_admin else "admin_demoted"
    _log_user_audit(action, current_user.id, target.id)
    role_desc = "granted admin rights" if target.is_admin else "admin rights revoked"
    flash(f"{target.username}: {role_desc}.", "success")
    return redirect(url_for("auth.users"))


@auth_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    """Delete a user account.

    Self-deletion and last-admin deletion are blocked by
    ``_guard_admin_action`` so an admin can never remove their own login; the
    deletion is written to the audit log.
    """
    target = db.session.get(User, user_id)
    if target is None:
        abort(404)

    blocked = _guard_admin_action(
        target,
        "You cannot delete your own account.",
        "Cannot delete the last admin account.",
    )
    if blocked is not None:
        return blocked

    username = target.username
    db.session.delete(target)
    db.session.commit()
    _log_user_audit("user_deleted", current_user.id, user_id)
    flash(f"User '{username}' deleted.", "success")
    return redirect(url_for("auth.users"))
