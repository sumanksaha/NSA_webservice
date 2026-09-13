import os
import secrets
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, redirect, url_for
from flask_login import current_user  # noqa: F401 — used by set_audit_user / template exposure
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import csrf, db, login_manager, talisman

_fso_sync_lock = threading.Lock()


class App(Flask):
    """Flask app subclass."""


def _load_or_create_production_secret_key(app: Flask) -> str:
    """Return a stable production SECRET_KEY, generating + persisting one if needed.

    Render's ``generateValue: true`` only mints a value when the env var is
    FIRST created on the service, so services that predate the setting (or
    were created from the dashboard) can boot with no SECRET_KEY. Instead of
    crashing the deploy, generate a strong key once and persist it so
    sessions stay valid across restarts and redeploys.

    Persistence order (first success wins):
      1. ``app_secrets`` key/value table in the primary DB (survives
         redeploys on Render's persistent Postgres).
      2. ``<instance_path>/.secret_key`` file (survives restarts).
      3. Ephemeral in-memory key — sessions reset on restart, but the app
         still boots (never blocks a deploy).

    An explicit ``SECRET_KEY`` env var (dashboard-managed or a
    generateValue-minted value) always takes precedence and bypasses this.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Normalize postgres:// -> postgresql:// (same as create_app does)
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(database_url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS app_secrets (name VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)",
                    ),
                )
                row = conn.execute(
                    text("SELECT value FROM app_secrets WHERE name = 'secret_key'"),
                ).fetchone()
                if row:
                    app.logger.info("SECRET_KEY auto-provisioned (DB — reuse)")
                    return str(row[0])
                new_key = secrets.token_hex(32)
                conn.execute(
                    text(
                        "INSERT INTO app_secrets (name, value) VALUES ('secret_key', :v) ON CONFLICT(name) DO NOTHING",
                    ),
                    {"v": new_key},
                )
                row = conn.execute(
                    text("SELECT value FROM app_secrets WHERE name = 'secret_key'"),
                ).fetchone()
                app.logger.info("SECRET_KEY auto-provisioned (DB — new)")
                return str(row[0]) if row else new_key
        except Exception as exc:
            app.logger.warning("SECRET_KEY DB persistence unavailable: %s", exc)

    # Instance-folder file fallback (survives restarts on the same instance)
    try:
        key_file = Path(app.instance_path) / ".secret_key"
        if key_file.exists():
            stored = key_file.read_text().strip()
            if stored:
                app.logger.info("SECRET_KEY auto-provisioned (file — reuse)")
                return stored
        new_key = secrets.token_hex(32)
        key_file.write_text(new_key)
        app.logger.info("SECRET_KEY auto-provisioned (file — new)")
        return new_key
    except OSError as exc:
        app.logger.warning("SECRET_KEY file fallback unavailable: %s", exc)

    # Ephemeral last resort — never block a deploy; sessions reset on restart
    app.logger.warning(
        "SECRET_KEY not set and no persistence available — using ephemeral key; "
        "sessions will reset on restart. Provision SECRET_KEY in Render "
        "(render.yaml generateValue: true for new services, or set it in the "
        "dashboard for existing services).",
    )
    return secrets.token_hex(32)


def create_app(db_uri: str | None = None):
    """Application factory.

    Parameters
    ----------
    db_uri:
        Optional explicit SQLAlchemy database URI. When given it overrides
        ``DATABASE_URL`` — used by tests that need an isolated database.
    """
    app = App(__name__)

    # Cache-busting for static assets: append the file's mtime as ?v= to every
    # url_for('static', ...) so deploys invalidate browser caches.
    @app.context_processor
    def _dated_url_for():
        from flask import url_for as _url_for

        def dated_url_for(endpoint, **values):
            if endpoint == "static" and values.get("filename"):
                file_path = os.path.join(app.root_path, "static", values["filename"])
                if os.path.exists(file_path):
                    values["v"] = int(os.stat(file_path).st_mtime)
            return _url_for(endpoint, **values)

        return dict(url_for=dated_url_for)

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

    # Ensure instance folder exists before the SECRET_KEY guard — the
    # production fallback may write a key file there on first boot.
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        app.logger.warning(f"Could not create instance directory: {app.instance_path}")

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
            # Auto-provision so the deploy can never be blocked (Render only
            # mints generateValue secrets when the env var is first created).
            secret_key = _load_or_create_production_secret_key(app)
        else:
            # In local development, use a fallback so the app can start without
            # requiring every developer to create a .env file immediately.
            # Gated behind is_production so production never silently falls
            # back to a fresh random key (that would rotate every session).
            secret_key = secrets.token_hex(32)
            app.logger.warning(
                "SECRET_KEY not set — using insecure local fallback. "
                "Set SECRET_KEY in your .env file for local development.",
            )
    app.config["SECRET_KEY"] = secret_key

    # Database configuration - PostgreSQL primary, SQLite fallback
    # An explicit ``db_uri`` argument (tests) wins over the environment.
    db_path = Path(app.instance_path) / "app.db"
    database_url = os.environ.get("DATABASE_URL")
    if db_uri:
        app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    elif database_url:
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

    # Pooler-safe Postgres connection options (Supabase/pgbouncer, Render PG):
    # pre_ping drops dead connections; sslmode=require is mandatory for both
    # hosts; modest pool sizing respects free-tier connection caps.
    if str(app.config["SQLALCHEMY_DATABASE_URI"]).startswith(("postgresql://", "postgres://")):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 5,
            "pool_recycle": 280,
            "connect_args": {"sslmode": "require"},
        }

    # Redis configuration (can be set via environment variable)
    app.config["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    # Phase 18: tests disable the RBAC gate session-wide via conftest; per-app
    # override stays possible by setting this key after create_app().
    app.config["DISABLE_RBAC"] = os.environ.get("DISABLE_RBAC", "") == "1"

    # Google Sheets configuration (can be set via environment variables)
    app.config["SPREADSHEET_ID"] = os.environ.get("SPREADSHEET_ID")
    app.config["GOOGLE_CREDENTIALS_JSON"] = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    # Priority 7 — Multi-Target Sheets Redundancy configuration
    app.config["AIRTABLE_API_KEY"] = os.environ.get("AIRTABLE_API_KEY")
    app.config["AIRTABLE_BASE_ID"] = os.environ.get("AIRTABLE_BASE_ID")

    # Microsoft Excel Online configuration (Priority 7)
    app.config["MS_TENANT_ID"] = os.environ.get("MS_TENANT_ID")
    app.config["MS_CLIENT_ID"] = os.environ.get("MS_CLIENT_ID")
    app.config["MS_CLIENT_SECRET"] = os.environ.get("MS_CLIENT_SECRET")
    app.config["MS_DRIVE_ID"] = os.environ.get("MS_DRIVE_ID")
    app.config["MS_SPREADSHEET_ID"] = os.environ.get("MS_SPREADSHEET_ID")

    # ------------------------------------------------------------------
    # Phase 11: AI Assistant configuration
    # ------------------------------------------------------------------
    app.config["AI_ASSISTANT_PROVIDER"] = os.environ.get("AI_ASSISTANT_PROVIDER", "")
    app.config["AI_ASSISTANT_API_KEY"] = os.environ.get("AI_ASSISTANT_API_KEY", "")
    app.config["AI_ASSISTANT_BASE_URL"] = os.environ.get("AI_ASSISTANT_BASE_URL")
    app.config["AI_ASSISTANT_MODEL"] = os.environ.get("AI_ASSISTANT_MODEL")

    # ------------------------------------------------------------------
    # RAG configuration - single configuration seam (app/shared/config.py).
    # Every declared setting is seeded below from env (or its declared
    # default), so soft readers (current_app.config.get) inside an app
    # context see exactly what out-of-context callers resolve under
    # Pattern A. Never hand-roll os.environ resolvers here again.
    # ------------------------------------------------------------------
    from app.shared.config import seed_config_from_env

    seed_config_from_env(app)

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
    #
    # NOTE: talisman.init_app() must be called EXACTLY ONCE (above). A second
    # bare call here (`talisman.init_app(app, force_https=False)`) silently
    # reset the CSP to flask-talisman's default
    # (`default-src 'self'; object-src 'none'`), stripping `script-src
    # 'unsafe-inline'` and blocking every inline <script> block in the
    # templates: FSSAI/CE lookup buttons stopped populating fields and the
    # document editor booted with an undefined window.CASE_ID.
    # Regression-pinned by tests/test_csp_headers.py.
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."

    # Initialize Flask-Migrate
    Migrate(app, db)

    # ------------------------------------------------------------------
    # Flask-Login: user_loader callback
    # ------------------------------------------------------------------
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Global login gate — every route requires authentication UNLESS it
    # is one of the public endpoints listed below.
    # ------------------------------------------------------------------
    public_endpoints = {
        "auth.login",
        "auth.first_setup",
        "static",
        "health.health",
        "health.cloudinary",
        # Backup monitoring — public dead-man's-switch for uptime probes
        "health.backups",
        # CSP violation collector — browsers post here without session/CSRF
        "health.csp_report",
        # Lookup endpoints - public for form prefill/autocomplete
        "case_file_generator.lookup_sample",
        "case_file_generator.list_samples_for_datalist",
        "case_file_generator.lookup_fssai_route",
        "adjudication.lookup_ce_route",
        "adjudication.lookup_fssai_route",
        "inspection.lookup_ce_route",
        "inspection.lookup_fssai_route",
        "sample.lookup_retailer",
        "bill_generator.lookup_fbo_issues",
        "adjudication.lookup_fbo_issues",
        # QStash webhook — authenticated by Upstash-Signature, not session
        "tasks_webhook.run_task",
        # QStash failure callback — authenticated by Upstash-Signature, not session
        "tasks_webhook.delivery_failed",
        # RAG health probe — public for monitoring
        "rag.health",
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

    # Auth gates (global login + Phase 18 RBAC) live in app/shared/rbac.py —
    # the single home for access policy (2026-09-12 review: the factory was
    # carrying auth logic that belonged in the RBAC seam).
    from app.shared.rbac import register_auth_gates

    register_auth_gates(app, public_endpoints)

    # Register custom Jinja filters globally
    from app.utils.filters import format_date_indian, to_words

    app.jinja_env.filters["to_words"] = to_words
    app.jinja_env.filters["format_date"] = format_date_indian
    app.jinja_env.filters["format_date_indian"] = format_date_indian

    # Inspection templates call datetime.datetime.now() directly; expose the
    # module as a global so they don't 500 (open_issues.html did).
    import datetime as _datetime_mod

    app.jinja_env.globals["datetime"] = _datetime_mod

    # Flask-Login already exposes current_user in all templates,
    # no need for a custom context_processor.

    # ------------------------------------------------------------------
    # Wire up SQLAlchemy audit event hooks for Adjudication, Bill, CaseFile
    # ------------------------------------------------------------------
    from app.audit_hooks import register_audit_hooks

    register_audit_hooks()

    # ------------------------------------------------------------------
    # Wire up SQLite FTS5 search event hooks (auto-index on CRUD)
    # ------------------------------------------------------------------
    from app.search.indexer import register_search_hooks

    register_search_hooks()

    # ------------------------------------------------------------------
    # Wire up Qdrant vector-store event hooks (Agent A Phase 1, Day 3).
    # Inert until chunk/document models are registered via
    # app.rag.qdrant_indexer.register_chunk_model / register_document_model
    # (planned LegalChunk / LegalDocument models — Phase 3, Day 12).
    # ------------------------------------------------------------------
    from app.rag.qdrant_indexer import register_qdrant_hooks

    register_qdrant_hooks()

    # Register blueprints (auth first so login page is available)
    from app.blueprints import register_blueprints

    register_blueprints(app)

    # Phase 20: Register default plugin providers (OCR, AI, Rules, PDF)
    # Lazy-imported so the app boots without optional deps (torch, httpx, etc.)
    from app.plugins import register_default_plugins

    register_default_plugins()

    # Database bootstrap (create_all fallback, alembic stamp, self-heal,
    # FTS5 table, admin seed) lives in app/db_bootstrap.py — extracted from
    # the factory by the 2026-09-12 review (create_app was a 500-line body).
    from app.db_bootstrap import bootstrap_database

    bootstrap_database(app)

    # ------------------------------------------------------------------
    # Auto-restore on empty database (Render free-tier rotation safety net):
    # when AUTO_RESTORE_ON_EMPTY_DB=true and every mapped table has zero rows,
    # replenish at boot — full ZIP archive from R2 first (complete fidelity),
    # then the Airtable→Excel→Sheets CSV chain. Best-effort: any failure is
    # logged and startup continues.
    # ------------------------------------------------------------------
    from app.shared.config import cfg

    if cfg.auto_restore_on_empty_db:
        with app.app_context():
            try:
                from app.utils.sync import auto_restore_if_empty

                result = auto_restore_if_empty()
                app.logger.info("Auto-restore on empty DB: %s", result)
            except Exception as e:
                app.logger.warning("Auto-restore failed (startup continues): %s", e)

    # FSO sync on startup - import and run sync in app context
    # This ensures FSO names are available as soon as the app starts
    # Can be skipped via SKIP_FSO_STARTUP_SYNC env var (e.g. for fresh-DB migrations)
    if not os.environ.get("SKIP_FSO_STARTUP_SYNC"):
        from app.utils.fso_data import sync_fso_from_markdown

        with app.app_context(), _fso_sync_lock:
            try:
                result = sync_fso_from_markdown()
                if result["errors"]:
                    app.logger.warning(f"FSO startup sync completed with warnings: {result['errors']}")
                else:
                    app.logger.info(f"FSO startup sync: {result['inserted']} inserted, {result['updated']} updated")
            except Exception as e:
                app.logger.error(f"FSO startup sync failed: {e!s}")

    # Redirect root to first tab (Sample -adjudication)
    @app.route("/")
    def root():
        return redirect(url_for("case_file_generator.index"))

    # Scheduled background jobs - enumerable registry
    # (app/services/scheduled_jobs.py). register_all reads enable-flags and
    # cron expressions through cfg; tests can call it with a fake publisher.
    from app.services.scheduled_jobs import register_all

    for entry in register_all(app):
        if entry["status"] == "registered":
            app.logger.info("Scheduled job registered: %s -> %s", entry["job"], entry["result"])
        else:
            app.logger.warning("Scheduled job %s failed: %s", entry["job"], entry["error"])

    return app


# Create the Flask application instance for Gunicorn
app = create_app()
