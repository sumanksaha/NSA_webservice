import os
import threading

from dotenv import load_dotenv
from flask import Flask, redirect, url_for
from flask_migrate import Migrate

from app.extensions import db

_fso_sync_lock = threading.Lock()

# Module-level Celery instance — populated after app factory runs
celery = None


def create_app():
    app = Flask(__name__)

    # Load environment variables from .env file before any config
    load_dotenv()

    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    # Database configuration - PostgreSQL primary, SQLite fallback
    db_path = os.path.join(app.instance_path, "app.db")
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Normalize postgres:// to postgresql:// for SQLAlchemy compatibility
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
        app.logger.warning("DATABASE_URL not set - falling back to SQLite")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Redis configuration (can be set via environment variable)
    app.config["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Google Sheets configuration (can be set via environment variable)
    app.config["SPREADSHEET_ID"] = os.environ.get("SPREADSHEET_ID")

    # Initialize SQLAlchemy database
    db.init_app(app)

    # Initialize Flask-Migrate
    migrate = Migrate(app, db)

    # Register custom Jinja filters globally
    from app.utils.filters import format_date_indian, to_words

    app.jinja_env.filters["to_words"] = to_words
    app.jinja_env.filters["format_date"] = format_date_indian
    app.jinja_env.filters["format_date_indian"] = format_date_indian

    # Register blueprints
    from app.adjudication.routes import adjudication_bp
    from app.bill_generator.routes import bill_generator_bp
    from app.billing.routes import billing_bp
    from app.case_file_generator.routes import case_file_generator_bp
    from app.fbo_issue.routes import fbo_issue_bp
    from app.inspection.routes import inspection_bp
    from app.sample.routes import sample_bp
    from app.settings.routes import settings_bp

    app.register_blueprint(case_file_generator_bp, url_prefix="/case_file_generator")
    app.register_blueprint(adjudication_bp, url_prefix="/adjudication")
    app.register_blueprint(bill_generator_bp, url_prefix="/bill_generator")
    app.register_blueprint(fbo_issue_bp, url_prefix="/fbo-issue")
    app.register_blueprint(sample_bp, url_prefix="/sample")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(inspection_bp, url_prefix="/inspection")

    # Initialize database tables (models must be imported first)
    # Import models so they're registered with SQLAlchemy metadata
    from app import models  # noqa: F401 — registers all models with db.metadata

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

        with app.app_context():
            with _fso_sync_lock:
                try:
                    result = sync_fso_from_markdown()
                    if result.get("errors"):
                        app.logger.warning(
                            f"FSO startup sync completed with warnings: {result['errors']}"
                        )
                    else:
                        app.logger.info(
                            f"FSO startup sync: {result['inserted']} inserted, {result['updated']} updated"
                        )
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
