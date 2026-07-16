import os
from flask import Flask, redirect, url_for
from app.extensions import db

def create_app():
    app = Flask(__name__)
    
    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)
    
    # SQLite configuration
    db_path = os.path.join(app.instance_path, 'app.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Google Sheets configuration (can be set via environment variable)
    app.config['SPREADSHEET_ID'] = os.environ.get('SPREADSHEET_ID')
    
    # Initialize SQLAlchemy database
    db.init_app(app)
    
    # Register custom Jinja filters globally
    from app.utils.filters import to_words, format_date_indian
    app.jinja_env.filters['to_words'] = to_words
    app.jinja_env.filters['format_date'] = format_date_indian
    app.jinja_env.filters['format_date_indian'] = format_date_indian
    
    # Register blueprints
    from app.case_file_generator.routes import case_file_generator_bp
    from app.adjudication.routes import adjudication_bp
    from app.bill_generator.routes import bill_generator_bp
    
    app.register_blueprint(case_file_generator_bp, url_prefix='/case_file_generator')
    app.register_blueprint(adjudication_bp, url_prefix='/adjudication')
    app.register_blueprint(bill_generator_bp, url_prefix='/bill_generator')
    
    # Redirect root to first tab (Sample Adjudication)
    @app.route('/')
    def root():
        return redirect(url_for('case_file_generator.index'))
        
    # Initialize database tables (models must be imported first)
    with app.app_context():
        from app import models  # noqa: F401 — registers CaseFile, Adjudication, Bill
        db.create_all()
        
    return app
