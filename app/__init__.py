import os
import secrets
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, redirect, request, url_for
from flask_login import current_user
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import csrf, db, login_manager, talisman

_fso_sync_lock = threading.Lock()

# Module-level Celery instance — populated after app factory runs
celery = None


class App(Flask):
    """Flask app subclass with a typed ``celery`` attribute."""

    celery: Any = None


def create_app():
    app = App(__name__)

    # Load environment variables from .env file before any config
    load_dotenv()

    # ------------------------------------------------------------------
    # Production detection — shared by the SECRET_KEY guard below and the
    # TLS/security-header config further down. Render sets RENDER on every
    # service; APP_ENV / FLASK_ENV cover other hosts. Hoisted here so the
    # SECRET_KEY guard can reuse it instead of only checking RENDER.
    # ------------------------------------------------------------------
    is_production = (
        bool(os.environ.get("RENDER"))
        or os.environ.get("APP_ENV", "").lower() in ("production", "prod")
        or os.environ.get("FLASK_ENV", "").lower() == "production"
    )

    # ------------------------------------------------------------------
    # Mandatory: SECRET_KEY — required for session signing, flash messages,
    #             CSRF tokens, and any cryptographic signing in Flask.
    # ------------------------------------------------------------------
    # In production this MUST be a long, random value.  Generate one with:
    #     python -c "import secrets; print(secrets.token_hex(32))"
    # Render: provision it as a managed value (render.yaml generateValue: true)
    # or via the Render dashboard. Never commit a real key to the repo.
    # ------------------------------------------------------------------
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if is_production:
            raise RuntimeError(
                "SECRET_KEY environment variable is not set. "
                "Provision a secure random key as a managed Render "
                "environment variable (render.yaml: generateValue: true) "
                "or add it in the Render dashboard before deploying.",
            )
        # In local development, use a fallback so the app can start without
        # requiring every developer to create a .env file immediately.
        # ponytail: gated behind is_production so production never silently falls back.
        secret_key = secrets.token_hex(32)
        app.logger.warning(
            "SECRET_KEY not set — using insecure local fallback. "
            "Set SECRET_KEY in your .env file for local development.",
        )
    app.config["SECRET_KEY"] = secret_key

    # Ensure instance folder exists
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        app.logger.warning(f"Could not create instance directory: {app.instance_path}")

    # Database configuration - PostgreSQL primary, SQLite fallback
    db_path = Path(app.instance_path) / "app.db"
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Normalize postgres:// to postgresql:// for SQLAlchemy compatibility
        # (Render still issues the old postgres:// scheme)
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        # Validate URL has a scheme (basic check for malformed URLs)
        if not any(
            database_url.startswith(proto) for proto in ["postgresql://", "sqlite://", "mysql://", "mariadb://"]
        ):
            app.logger.warning(f"DATABASE_URL malformed: '{database_url}' - falling back to SQLite")
            database_url = f"sqlite:///{db_path}"
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
        app.logger.warning("DATABASE_URL not set - falling back to SQLite")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Redis configuration (can be set via environment variable)
    app.config["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Google Sheets configuration (can be set via environment variables)
    app.config["SPREADSHEET_ID"] = os.environ.get("SPREADSHEET_ID")
    app.config["GOOGLE_CREDENTIALS_JSON"] = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    # ------------------------------------------------------------------
    # Security headers & HTTPS enforcement via Flask-Talisman
    # ------------------------------------------------------------------
    # Render terminates TLS at the edge.  We use ProxyFix so Flask/Talisman
    # trust the X-Forwarded-Proto header and don't create a redirect loop.
    # ------------------------------------------------------------------
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    csp = {
        "default-src": ["'self'"],
        "style-src": [
            "'self'",
            "'unsafe-inline'",
            "https://fonts.googleapis.com",
            "https://cdnjs.cloudflare.com",
        ],
        "font-src": [
            "'self'",
            "https://fonts.gstatic.com",
            "https://cdnjs.cloudflare.com",
        ],
        "script-src": [
            "'self'",
            "'unsafe-inline'",
        ],
        "img-src": [
            "'self'",
            "data:",
        ],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "base-uri": ["'self'"],
    }

    talisman.init_app(
        app,
        force_https=is_production,
        force_https_permanent=is_production,
        content_security_policy=csp,
        content_security_policy_report_only=False,
        content_security_policy_report_uri="/csp-report",
        strict_transport_security=is_production,
        strict_transport_security_max_age=31536000,
        strict_transport_security_include_subdomains=is_production,
        session_cookie_secure=is_production,
        session_cookie_http_only=True,
        session_cookie_samesite="Lax",
    )

    # Initialize CSRF protection (uses SECRET_KEY set above)
    csrf.init_app(app)

    # Initialize SQLAlchemy database
    db.init_app(app)

    # Initialize security extensions
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    talisman.init_app(app, force_https=False)

    # Initialize Flask-Migrate
    Migrate(app, db)

    # ------------------------------------------------------------------
    # Flask-Login: user_loader callback
    # ------------------------------------------------------------------
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return User.query.get(int(user_id))
        except (ValueError, TypeError):
            return None

    login_manager.init_app(app)

    # ------------------------------------------------------------------
    # Global login gate — every route requires authentication UNLESS it
    # is one of the public endpoints listed below.
    # ------------------------------------------------------------------
    public_endpoints = {
        "auth.login",
        "static",
        # Lookup endpoints - public for form prefill/autocomplete
        "case_file_generator.lookup_sample",
        "case_file_generator.list_samples_for_datalist",
        "adjudication.lookup_ce_route",
        "adjudication.lookup_fssai_route",
        "inspection.lookup_ce_route",
        "inspection.lookup_fssai_route",
        "sample.lookup_retailer",
        "bill_generator.lookup_fbo_issues",
        "adjudication.lookup_fbo_issues",
    }

    @app.before_request
    def set_audit_user():
        """Store the current user ID on ``db.session.info`` so that audit
        event hooks can read it without depending on the request context.
        """
        try:
            db.session.info["audit_user_id"] = current_user.get_id() if current_user.is_authenticated else None
        except (RuntimeError, AttributeError):
            db.session.info["audit_user_id"] = None

    @app.before_request
    def require_login():
        if request.endpoint and request.endpoint not in public_endpoints and not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))

    # Register custom Jinja filters globally
    from app.utils.filters import format_date_indian, to_words

    app.jinja_env.filters["to_words"] = to_words
    app.jinja_env.filters["format_date"] = format_date_indian
    app.jinja_env.filters["format_date_indian"] = format_date_indian

    # Flask-Login already exposes current_user in all templates,
    # no need for a custom context_processor.

    # ------------------------------------------------------------------
    # Wire up SQLAlchemy audit event hooks for Adjudication, Bill, CaseFile
    # ------------------------------------------------------------------
    from app.audit_hooks import register_audit_hooks

    register_audit_hooks()

    # Register blueprints (auth first so login page is available)
    from app.adjudication.routes import adjudication_bp
    from app.audit import audit_bp
    from app.auth.routes import auth_bp
    from app.bill_generator.routes import bill_generator_bp
    from app.billing.routes import billing_bp
    from app.case_file_generator.routes import case_file_generator_bp
    from app.fbo_issue.routes import fbo_issue_bp
    from app.inspection.routes import inspection_bp
    from app.legal_analysis import legal_analysis_bp
    from app.sample.routes import sample_bp
    from app.settings.routes import settings_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(case_file_generator_bp, url_prefix="/case_file_generator")
    app.register_blueprint(adjudication_bp, url_prefix="/adjudication")
    from app.document_viewer import document_viewer_bp
    app.register_blueprint(document_viewer_bp, url_prefix="/document_viewer")
    app.register_blueprint(bill_generator_bp, url_prefix="/bill_generator")
    app.register_blueprint(fbo_issue_bp, url_prefix="/fbo-issue")
    app.register_blueprint(sample_bp, url_prefix="/sample")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(inspection_bp, url_prefix="/inspection")
    app.register_blueprint(legal_analysis_bp, url_prefix="/legal")
    app.register_blueprint(audit_bp, url_prefix="/admin")

    # Initialize database tables (models must be imported first)
    # Import models so they're registered with SQLAlchemy metadata
    from app import models

    # Fallback safeguard: if core tables are missing (e.g., fresh local DB
    # without migrations applied), create them so startup sync doesn't fail.
    with app.app_context():
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect

        engine = create_engine(app.config["SQLALCHEMY_DATABASE_URI"])
        inspector = sa_inspect(engine)
        if "fso" not in inspector.get_table_names():
            db.create_all()
            app.logger.info("Created missing tables via db.create_all() fallback")

    # FSO sync on startup - import and run sync in app context
    # This ensures FSO names are available as soon as the app starts
    # Can be skipped via SKIP_FSO_STARTUP_SYNC env var (e.g. for fresh-DB migrations)
    if not os.environ.get("SKIP_FSO_STARTUP_SYNC"):
        from app.utils.fso_data import sync_fso_from_markdown

        with app.app_context(), _fso_sync_lock:
            try:
                result = sync_fso_from_markdown()
                if result.errors:
                    app.logger.warning(f"FSO startup sync completed with warnings: {result.errors}")
                else:
                    app.logger.info(f"FSO startup sync: {result.inserted} inserted, {result.updated} updated")
            except Exception as e:
                app.logger.error(f"FSO startup sync failed: {e!s}")

    # Redirect root to first tab (Sample -adjudication)
    @app.route("/")
    def root():
        return redirect(url_for("case_file_generator.index"))

    # Initialize Celery with Flask app context support
    # Lazy import to avoid ModuleNotFoundError in deployment environments
    try:
        from celery_app import make_celery

        app.celery = make_celery(app)
    except ImportError:
        # Celery not available (e.g., in minimal deployment)
        app.celery = None

    return app


# Create the Flask application instance for Gunicorn
app = create_app()

# Export celery at module level so it can be imported elsewhere
celery = app.celery
