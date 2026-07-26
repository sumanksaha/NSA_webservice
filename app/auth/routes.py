from urllib.parse import urlparse
from datetime import datetime

from flask import render_template, redirect, url_for, request, flash, session
from werkzeug.security import check_password_hash
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models import User, RecordAudit
from app.auth import auth_bp
import json


def _is_safe_redirect_url(target):
    """
    Validate that a redirect URL is safe — only relative paths are accepted.
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
            timestamp=datetime.utcnow(),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Audit logging must never break the login flow — swallow errors


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
