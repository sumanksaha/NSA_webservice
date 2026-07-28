from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from app.auth import auth_bp
from app.extensions import login_manager
from app.models import User


@login_manager.user_loader
def load_user(user_id: str):
    """Load user by ID for Flask-Login session management."""
    return User.query.get(int(user_id))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Handle user login with username/password authentication."""
    if current_user.is_authenticated:
        return redirect(url_for("case_file_generator.index"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("case_file_generator.index"))
        flash("Invalid username or password", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Handle user logout."""
    logout_user()
    return redirect(url_for("auth.login"))
