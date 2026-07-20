import os
from flask import Flask, redirect, url_for
from flask_migrate import Migrate
from app.extensions import db

def create_app():
    app = Flask(__name__)
    
    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)
    
    # Database configuration - PostgreSQL primary, SQLite fallback
    db_path = os.path.join(app.instance_path, 'app.db')
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        # Normalize postgres:// to postgresql:// for SQLAlchemy compatibility
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
        app.logger.warning('DATABASE_URL not set - falling back to SQLite')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Google Sheets configuration (can be set via environment variable)
    app.config['SPREADSHEET_ID'] = os.environ.get('SPREADSHEET_ID')
    
    # Initialize SQLAlchemy database
    db.init_app(app)
    
    # Initialize Flask-Migrate
    migrate = Migrate(app, db)
    
    # Register custom Jinja filters globally
    from app.utils.filters import to_words, format_date_indian
    app.jinja_env.filters['to_words'] = to_words
    app.jinja_env.filters['format_date'] = format_date_indian
    app.jinja_env.filters['format_date_indian'] = format_date_indian
    
    # Register blueprints
    from app.case_file_generator.routes import case_file_generator_bp
    from app.adjudication.routes import adjudication_bp
    from app.bill_generator.routes import bill_generator_bp
    from app.fbo_issue.routes import fbo_issue_bp
    from app.sample.routes import sample_bp
    from app.billing.routes import billing_bp
    from app.settings.routes import settings_bp
    from app.inspection.routes import inspection_bp
    
    app.register_blueprint(case_file_generator_bp, url_prefix='/case_file_generator')
    app.register_blueprint(adjudication_bp, url_prefix='/adjudication')
    app.register_blueprint(bill_generator_bp, url_prefix='/bill_generator')
    app.register_blueprint(fbo_issue_bp, url_prefix='/fbo-issue')
    app.register_blueprint(sample_bp, url_prefix='/sample')
    app.register_blueprint(billing_bp, url_prefix='/billing')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(inspection_bp, url_prefix='/inspection')
    
    # FSO sync on startup - import and run sync in app context
    # This ensures FSO names are available as soon as the app starts
    from app.utils.fso_data import sync_fso_from_markdown
    
    @app.before_request
    def sync_fso_on_startup():
        """Sync FSO list from markdown on first request (app startup)."""
        # Use a flag to only run once
        if not hasattr(app, '_fso_synced'):
            with app.app_context():
                result = sync_fso_from_markdown()
                if result.get('errors'):
                    app.logger.warning(f"FSO startup sync completed with warnings: {result['errors']}")
                else:
                    app.logger.info(f"FSO startup sync: {result['inserted']} inserted, {result['updated']} updated")
                app._fso_synced = True
    
    # Redirect root to first tab (Sample -adjudication)
    @app.route('/')
    def root():
        return redirect(url_for('case_file_generator.index'))
        
    # Initialize database tables (models must be imported first)
    # Import models so they're registered with SQLAlchemy metadata
    from app import models  # noqa: F401 — registers all models with db.metadata
    
    # Temporarily disabled for Alembic migration generation
    # with app.app_context():
    #     db.create_all()
        
    return app


# Create the Flask application instance for Gunicorn
app = create_app()
