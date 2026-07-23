### Path: app.py

```python
import os
from app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host='127.0.0.1', port=port, debug=True)
```

### Path: app/__init__.py

```python
import os
from flask import Flask, redirect, url_for
from flask_migrate import Migrate
from app.extensions import db

def create_app():
    """Flask application factory. Configures DB, blueprints, filters, startup sync."""
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = ...  # PostgreSQL or SQLite fallback
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SPREADSHEET_ID'] = os.environ.get('SPREADSHEET_ID')
    db.init_app(app)
    migrate = Migrate(app, db)
    from app.utils.filters import to_words, format_date_indian
    app.jinja_env.filters['to_words'] = to_words
    app.jinja_env.filters['format_date'] = format_date_indian
    app.jinja_env.filters['format_date_indian'] = format_date_indian
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
    from app.utils.fso_data import sync_fso_from_markdown

    @app.before_request
    def sync_fso_on_startup():
        """Sync FSO list from markdown on first request (app startup)."""
        ...

    @app.route('/')
    def root():
        return redirect(url_for('case_file_generator.index'))

    from app import models
    return app

app = create_app()
```

### Path: app/extensions.py

```python
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
```

### Path: app/models.py

```python
from datetime import datetime
from app.extensions import db

class CaseFile(db.Model):
    __tablename__ = 'case_files'
    id = db.Column(db.Integer, primary_key=True)
    case_number = db.Column(db.String(100), nullable=False)
    food_safety_officer_name = db.Column(db.String(100), nullable=False)
    authorization_date = db.Column(db.String(100), nullable=False)
    inspection_date = db.Column(db.String(100), nullable=False)
    inspection_time = db.Column(db.String(100), nullable=False)
    sample_id = db.Column(db.Integer, db.ForeignKey('sample.id', ondelete='SET NULL'), nullable=True)
    manufacturer_fssai = db.Column(db.String(50), nullable=False)
    manufacturer_name = db.Column(db.String(200), nullable=False)
    manufacturer_fbo_name = db.Column(db.String(200), nullable=False)
    manufacturer_address = db.Column(db.Text, nullable=False)
    retailer_fssai = db.Column(db.String(50), nullable=False)
    retailer_name = db.Column(db.String(200), nullable=False)
    retailer_fbo_name = db.Column(db.String(200), nullable=False)
    retailer_address = db.Column(db.Text, nullable=False)
    product_name = db.Column(db.String(200), nullable=False)
    batch_no = db.Column(db.String(100), nullable=False)
    sample_quantity = db.Column(db.String(100), nullable=False)
    packet_count = db.Column(db.Integer, nullable=False)
    mfg_date = db.Column(db.String(100), nullable=False)
    expiry_date = db.Column(db.String(100), nullable=False)
    other_food_articles = db.Column(db.String(500))
    total_cost = db.Column(db.String(50))
    cost_in_words = db.Column(db.String(200))
    sample_code = db.Column(db.String(100), nullable=False)
    sample_submission_date = db.Column(db.String(100), nullable=False)
    Lab_Registration_No = db.Column(db.String(100), nullable=False)
    do_receipt_date = db.Column(db.String(100), nullable=False)
    is_misbranded = db.Column(db.Boolean, default=False)
    is_substandard = db.Column(db.Boolean, default=False)
    analyst_report_no = db.Column(db.String(100), nullable=False)
    analyst_report_date = db.Column(db.String(100), nullable=False)
    directive_letter_no = db.Column(db.String(100), nullable=False)
    directive_letter_date = db.Column(db.String(100), nullable=False)
    retailer_report_receive_date = db.Column(db.String(100), nullable=False)
    manufacturer_report_receive_date = db.Column(db.String(100), nullable=False)
    applicable_regulation = db.Column(db.String(200))
    applicable_clause = db.Column(db.String(200))
    sample_name = db.Column(db.String(200))
    applicable_sections = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)

class Adjudication(db.Model):
    __tablename__ = 'adjudications'
    id = db.Column(db.Integer, primary_key=True)
    case_number = db.Column(db.String(100), nullable=False)
    food_safety_officer = db.Column(db.String(100), nullable=False)
    non_license = db.Column(db.String(10), default='no')
    pre_authorization = db.Column(db.String(10), default='no')
    complaint_lodged = db.Column(db.String(10), default='no')
    ce_license_no = db.Column(db.String(100))
    ce_trade_name = db.Column(db.String(200))
    ce_proprietor = db.Column(db.String(200))
    ce_address = db.Column(db.Text)
    ce_status = db.Column(db.String(100))
    fbo_owner = db.Column(db.String(200), nullable=False)
    fbo_name = db.Column(db.String(200), nullable=False)
    fbo_address = db.Column(db.Text, nullable=False)
    fssai_license = db.Column(db.String(100), nullable=False)
    concerned_food = db.Column(db.String(200))
    problem = db.Column(db.Text)
    First_inspection_date = db.Column(db.String(100), nullable=False)
    compliance_deadline = db.Column(db.String(100), nullable=False)
    Complaint_date = db.Column(db.String(100))
    inspection_date = db.Column(db.String(100), nullable=False)
    authorization_date = db.Column(db.String(100))
    clean_premise = db.Column(db.String(10), default='yes')
    refrigerator_clean = db.Column(db.String(10), default='yes')
    proper_attire = db.Column(db.String(10), default='yes')
    proper_covered_utensil = db.Column(db.String(10), default='yes')
    date_tag = db.Column(db.String(10), default='yes')
    veg_nonveg_separation = db.Column(db.String(10), default='yes')
    food_segregation = db.Column(db.String(10), default='yes')
    license_display = db.Column(db.String(10), default='yes')
    artificial_colour = db.Column(db.String(10), default='no')
    Expired_item = db.Column(db.String(10), default='no')
    Pest_report = db.Column(db.String(10), default='yes')
    Water_report = db.Column(db.String(10), default='yes')
    section_55 = db.Column(db.String(10), default='no')
    section_56 = db.Column(db.String(10), default='no')
    section_58 = db.Column(db.String(10), default='no')
    section_63 = db.Column(db.String(10), default='no')
    section_64 = db.Column(db.String(10), default='no')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)

class Bill(db.Model):
    __tablename__ = 'bills'
    id = db.Column(db.Integer, primary_key=True)
    Name = db.Column(db.String(100), nullable=False)
    EMP_ID = db.Column(db.String(50), nullable=False)
    Designation = db.Column(db.String(100), nullable=False, default="Food Safety Officer")
    Enf_samp_No = db.Column(db.Integer, nullable=False, default=0)
    Surv_samp_No = db.Column(db.Integer, nullable=False, default=0)
    enforcement_price = db.Column(db.Float, nullable=False, default=0.0)
    surveillance_price = db.Column(db.Float, nullable=False, default=0.0)
    Total_bill = db.Column(db.String(50), nullable=False)
    No_of_enfbills = db.Column(db.String(50), nullable=False)
    No_of_survbills = db.Column(db.String(50), nullable=False)
    TR_Value = db.Column(db.String(100), nullable=False)
    TR_date = db.Column(db.String(100), nullable=False)
    Submission_date = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.String(100))
    end_date = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)
    samples = db.relationship('Sample', secondary='bill_sample', backref='bills')

class FboIssue(db.Model):
    __tablename__ = 'fbo_issue'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fbo_id = db.Column(db.String, nullable=False)
    manufacturer_fbo_id = db.Column(db.String, nullable=True)
    fbo_name = db.Column(db.String, nullable=False)
    source_type = db.Column(db.String, nullable=False)
    state = db.Column(db.String, nullable=False, default='open')
    fso_name = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    detail_json = db.Column(db.Text, nullable=True)
    reg_lat = db.Column(db.Float, nullable=True)
    reg_lng = db.Column(db.Float, nullable=True)
    geocoded_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (
        db.CheckConstraint("source_type IN ('inspection','sample')", name='ck_source_type'),
        db.CheckConstraint("state IN ('open','permission_pending','permission_granted','closed','dismissed')", name='ck_state'),
        db.CheckConstraint("NOT (source_type = 'sample' AND state = 'dismissed')", name='ck_sample_not_dismissed'),
        db.CheckConstraint("source_type = 'sample' OR manufacturer_fbo_id IS NULL", name='ck_sample_or_null_mfg'),
        db.Index('idx_fbo_issue_fbo_id', 'fbo_id'),
        db.Index('idx_fbo_issue_state', 'state'),
    )

class FboIssueAudit(db.Model):
    __tablename__ = 'fbo_issue_audit'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('fbo_issue.id'), nullable=False)
    from_state = db.Column(db.String, nullable=True)
    to_state = db.Column(db.String, nullable=False)
    asserted_by = db.Column(db.String, nullable=False)
    asserted_at = db.Column(db.String, nullable=False, default=datetime.utcnow().isoformat())
    note = db.Column(db.Text, nullable=True)
    __table_args__ = (
        db.Index('idx_fbo_issue_audit_issue_id', 'issue_id'),
    )

class FSO(db.Model):
    __tablename__ = 'fso'
    fso_name = db.Column(db.String(100), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (
        db.Index('idx_fso_name', 'fso_name'),
    )

class Sample(db.Model):
    __tablename__ = 'sample'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    sample_code = db.Column(db.String(50), nullable=False, unique=True)
    sample_name = db.Column(db.String(200), nullable=False)
    sample_type = db.Column(db.String(100), nullable=False)
    fso_name = db.Column(db.String(100), db.ForeignKey('fso.fso_name'), nullable=False)
    collection_date = db.Column(db.String(100), nullable=False)
    submission_date = db.Column(db.String(100), nullable=True)
    retailer_fssai = db.Column(db.String(50), nullable=True)
    retailer_name = db.Column(db.String(200), nullable=True)
    price = db.Column(db.String(50), nullable=True)
    billed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (
        db.Index('idx_sample_code', 'sample_code'),
        db.Index('idx_sample_collection_date', 'collection_date'),
        db.Index('idx_sample_fso_name', 'fso_name'),
        db.Index('idx_sample_billed', 'billed'),
    )

class BillSample(db.Model):
    __tablename__ = 'bill_sample'
    bill_id = db.Column(db.Integer, db.ForeignKey('bills.id'), primary_key=True)
    sample_id = db.Column(db.Integer, db.ForeignKey('sample.id'), primary_key=True)

class Inspection(db.Model):
    __tablename__ = 'inspection'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    inspection_code = db.Column(db.String(50), nullable=False, unique=True)
    fso_name = db.Column(db.String(100), db.ForeignKey('fso.fso_name'), nullable=False)
    fssai_license = db.Column(db.String(50), nullable=True)
    ce_license_no = db.Column(db.String(100), nullable=True)
    fbo_name = db.Column(db.String(200), nullable=True)
    fbo_address = db.Column(db.Text, nullable=True)
    concerned_food = db.Column(db.String(200), nullable=True)
    problem = db.Column(db.Text, nullable=True)
    inspection_date = db.Column(db.String(100), nullable=False)
    compliance_deadline = db.Column(db.String(100), nullable=False)
    is_dismissed = db.Column(db.Boolean, default=False)
    dismissed_by = db.Column(db.String(100), nullable=True)
    dismissed_at = db.Column(db.DateTime, nullable=True)
    adjudication_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (
        db.Index('idx_inspection_code', 'inspection_code'),
        db.Index('idx_inspection_date', 'inspection_date'),
        db.Index('idx_inspection_compliance_deadline', 'compliance_deadline'),
        db.Index('idx_inspection_fso_name', 'fso_name'),
    )

class PhotoEvidence(db.Model):
    __tablename__ = 'photo_evidence'
    image_id = db.Column(db.String, primary_key=True)
    case_id = db.Column(db.String, db.ForeignKey('case_files.id'), nullable=False)
    filepath = db.Column(db.String, nullable=False)
    raw_lat = db.Column(db.Float, nullable=False)
    raw_lng = db.Column(db.Float, nullable=False)
    accuracy = db.Column(db.Float, nullable=False)
    captured_at = db.Column(db.DateTime, nullable=False)
    uploaded_at = db.Column(db.DateTime, nullable=False)
    locality = db.Column(db.String, nullable=True)
    ip_region = db.Column(db.String, nullable=True)
    ip_match = db.Column(db.Boolean, nullable=True)
    distance_to_fbo_m = db.Column(db.Float, nullable=True)
    verification_status = db.Column(db.String, default='PENDING')
    stamped = db.Column(db.Boolean, default=False)

class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entity_type = db.Column(db.String, nullable=False)
    entity_id = db.Column(db.String, nullable=False)
    action = db.Column(db.String, nullable=False)
    actor = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    prev_hash = db.Column(db.String, nullable=True)
    curr_hash = db.Column(db.String, nullable=True)
    details_json = db.Column(db.Text, nullable=True)
```

### Path: app/adjudication/routes.py

```python
import io
import zipfile
from datetime import datetime, date
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import Adjudication, FboIssue
from app.utils.lookup import lookup_ce, lookup_fssai
from app.utils.suggester import suggest_sections
from app.services.sheets_sync import sync_to_sheets
import json
from app.utils.pdf_utils import generate_pdf_from_html
from app.shared.case_keys import (
    DERIVED_APPLICABLE_SECTIONS, DERIVED_SECTIONS_DISPLAY, DERIVED_CASE_TRACK,
    DERIVED_VIOLATIONS, DERIVED_SAME_ENTITY,
    SECTION_55, SECTION_56, SECTION_58, SECTION_63, SECTION_64,
    SHARED_NON_LICENSE, SHARED_PRE_AUTHORIZATION, SHARED_COMPLAINT_LODGED,
)
from app.shared.context_derivers import (
    derive_applicable_sections_from_adjudication,
    derive_sections_display, derive_case_track, derive_violations,
)

adjudication_bp = Blueprint('adjudication', __name__, template_folder='templates', static_folder='static')

CHECKLIST = ['clean_premise', 'refrigerator_clean', 'proper_attire', 'proper_covered_utensil', 'date_tag', 'veg_nonveg_separation', 'food_segregation', 'license_display', 'artificial_colour', 'Expired_item', 'Pest_report', 'Water_report']

RULES = {
    'clean_premise': ("Unclean Premises", "The premises were found inadequately maintained and unhygienic."),
    'refrigerator_clean': ("Improper Refrigerator Maintenance", "Refrigeration facilities were found unclean."),
    'proper_attire': ("Improper Protective Attire", "Food handlers lacked prescribed attire."),
    'proper_covered_utensil': ("Improper Covering of Food", "Food and utensils were uncovered."),
    'date_tag': ("Absence of Date Tagging", "Stored food items lacked traceability."),
    'veg_nonveg_separation': ("Improper Veg/Non-Veg Separation", "Segregation not maintained."),
    'food_segregation': ("Improper Food Segregation", "Risk of cross contamination."),
    'license_display': ("Improper License Display", "License not prominently displayed."),
    'Expired_item': ("Expired Items", "Expired items present."),
    'Pest_report': ("Pest Control Report Missing", "Routine pest control not documented."),
    'Water_report': ("Water Test Report Missing", "Potable water testing unavailable."),
}

def adjudication_to_dict(adj):
    """Convert an Adjudication model instance to a dictionary for JSON serialization."""
    return {...}  # All model fields mapped to canonical keys

@adjudication_bp.route('/')
def index():
    ...
    return render_template('adjudication/index.html', checklist=CHECKLIST, prefill=prefill_data)

@adjudication_bp.route('/lookup_ce', methods=['POST'])
def lookup_ce_route():
    ...
    return jsonify(result)

@adjudication_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    ...
    return jsonify({"identity": result})

@adjudication_bp.route('/lookup_fbo_issues', methods=['GET'])
def lookup_fbo_issues():
    """Lookup FBO issues by fbo_id to provide pre-fill options for adjudication cases."""
    ...
    return jsonify(result), 200

@adjudication_bp.route('/suggest_sections', methods=['POST'])
def suggest_sections_route():
    ...
    return jsonify(suggestions)

@adjudication_bp.route('/cases', methods=['GET'])
def list_adjudication_cases():
    """List all existing adjudication cases."""
    ...
    return jsonify([...])

@adjudication_bp.route('/case/<int:case_id>', methods=['GET'])
def get_adjudication_case(case_id):
    """Retrieve a specific adjudication case by ID."""
    ...
    return jsonify(adjudication_to_dict(adj))

@adjudication_bp.route('/case/by_number/<case_number>', methods=['GET'])
def get_adjudication_case_by_number(case_number):
    """Retrieve a specific adjudication case by case number."""
    ...
    return jsonify(adjudication_to_dict(adj))

@adjudication_bp.route('/regenerate/<int:case_id>', methods=['GET'])
def regenerate_adjudication_documents(case_id):
    """Regenerate documents from an existing adjudication case."""
    ...
    return send_file(zip_buffer, as_attachment=True, download_name=..., mimetype="application/zip")

@adjudication_bp.route('/generate_all', methods=['POST'])
def generate_all():
    ...
    return send_file(zip_buffer, as_attachment=True, download_name=..., mimetype="application/zip")
```

### Path: app/bill_generator/__init__.py

```python
# Empty file
```

### Path: app/bill_generator/routes.py

```python
import io
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import Bill, FboIssue, Sample
from app.bill_generator.utils import get_billable_samples, mark_samples_as_billed
from app.services.sheets_sync import sync_to_sheets
import json

bill_generator_bp = Blueprint('bill_generator', __name__, template_folder='templates', static_folder='static')

@bill_generator_bp.route('/')
def index():
    ...
    return render_template('bill_generator/index.html')

@bill_generator_bp.route('/lookup_fbo_issues', methods=['GET'])
def lookup_fbo_issues():
    """Lookup FBO issues by fbo_id to provide pre-fill options for bill generation."""
    ...
    return jsonify(result), 200

@bill_generator_bp.route('/bill/preview', methods=['GET'])
def bill_preview():
    """Preview bill for a date range."""
    ...
    return jsonify(result), 200

@bill_generator_bp.route('/generate_bill', methods=['POST'])
def generate_bill_route():
    ...
    return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")
```

### Path: app/bill_generator/utils.py

```python
"""
Bill Generator Utilities
Shared query helpers for bill generation and preview.
"""
from datetime import datetime
from app.models import Sample, BillSample
from app.extensions import db

def get_billable_samples(start_date, end_date):
    """
    Get billable samples for a date range, split by type.
    Returns dict with enforcement_no, enforcement_price, surveillance_no, surveillance_price, samples.
    """
    ...

def mark_samples_as_billed(sample_ids, bill_id):
    """
    Mark samples as billed and link them to the bill.
    """
    ...
```

### Path: app/billing/__init__.py

```python
"""
Billing module for NSA_webservice.
Provides billing summary and export functionality for Sample data.
"""
from flask import Blueprint

billing_bp = Blueprint('billing', __name__, template_folder='templates', static_folder='static')

from app.billing import routes
```

### Path: app/billing/routes.py

```python
"""
Billing routes module.
Provides billing summary and export functionality for Sample data.
"""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file
from app.extensions import db
from app.models import Sample
from app.utils.fso_data import get_all_fso_names
from app.billing.billing_utils import compute_summary, generate_excel_report, format_price

from app.billing import billing_bp

SAMPLE_TYPES = ['enforcement', 'surveillance']

@billing_bp.route('/')
def index():
    """Billing summary view with filters and export."""
    ...
    return render_template('billing/index.html', ...)

@billing_bp.route('/export', methods=['GET'])
def export_excel():
    """Export current filtered view to Excel."""
    ...
    return send_file(excel_file, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
```

### Path: app/billing/billing_utils.py

```python
"""
billing_utils.py
Utilities for the Billing module, including Excel export.
"""
import io
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from flask import current_app

def format_price(price_str):
    """
    Format a price string to a float, handling various formats.
    Returns float or 0.0 if parsing fails.
    """
    ...

def compute_summary(samples):
    """
    Compute summary statistics from a list of sample records.
    Returns dict with by_type, grand_total, total_count.
    """
    ...

def generate_excel_report(samples, summary, start_date=None, end_date=None):
    """
    Generate an Excel workbook with two sheets: Samples and Summary.
    Returns (Excel file bytes, filename).
    """
    ...
```

### Path: app/case_file_generator/routes.py

```python
import io
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import CaseFile, Sample
from app.utils.lookup import lookup_fssai
from app.utils.filters import format_date_indian
from app.services.sheets_sync import sync_to_sheets
from app.shared.case_keys import (
    DERIVED_APPLICABLE_SECTIONS, DERIVED_SECTIONS_DISPLAY, DERIVED_CASE_TRACK,
    DERIVED_VIOLATIONS, DERIVED_SAME_ENTITY,
    SAMPLE_IS_SUBSTANDARD, SAMPLE_IS_MISBRANDED,
    PARTY_MANUFACTURER_FSSAI, PARTY_RETAILER_FSSAI,
)
from app.shared.context_derivers import (
    derive_applicable_sections_from_case_file,
    derive_sections_display, derive_same_entity,
)

case_file_generator_bp = Blueprint('case_file_generator', __name__, template_folder='templates', static_folder='static')

def get_applicable_sections(form_data: dict) -> list:
    """Determine applicable FSS Act sections based on analysis result."""
    ...

def process_form_data(form_data):
    """Process form data and prepare case_data dictionary for template rendering and model saving."""
    ...

def case_file_to_dict(case_file):
    """Convert a CaseFile model instance to a dictionary for JSON serialization."""
    return {...}

@case_file_generator_bp.route('/')
def index():
    ...
    return render_template('case_file_generator/index.html')

@case_file_generator_bp.route('/cases', methods=['GET'])
def list_cases():
    """List all existing case files."""
    ...
    return jsonify([...])

@case_file_generator_bp.route('/case/<int:case_id>', methods=['GET'])
def get_case(case_id):
    """Retrieve a specific case file by ID."""
    ...
    return jsonify(case_file_to_dict(case_file))

@case_file_generator_bp.route('/case/by_number/<case_number>', methods=['GET'])
def get_case_by_number(case_number):
    """Retrieve a specific case file by case number."""
    ...
    return jsonify(case_file_to_dict(case_file))

@case_file_generator_bp.route('/regenerate/<int:case_id>', methods=['GET'])
def regenerate_case_files(case_id):
    """Regenerate both Petition and Permission Letter from an existing case."""
    ...
    return send_file(zip_buffer, as_attachment=True, download_name=..., mimetype="application/zip")

@case_file_generator_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    ...
    return jsonify({"identity": result})

@case_file_generator_bp.route('/lookup_sample', methods=['GET'])
def lookup_sample():
    """Lookup sample by sample_code for CaseFile prefill."""
    ...
    return jsonify({...})

@case_file_generator_bp.route('/samples', methods=['GET'])
def list_samples_for_datalist():
    """List all samples for datalist dropdown (returns sample codes only)."""
    ...
    return jsonify({'sample_codes': sample_codes})

@case_file_generator_bp.route('/generate_case_file', methods=['POST'])
def generate_case_file_route():
    ...
    return send_file(zip_buffer, as_attachment=True, download_name=..., mimetype="application/zip")
```

### Path: app/fbo_issue/routes.py

```python
from datetime import datetime
import json
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models import FboIssue, FboIssueAudit
from app.utils.lookup import lookup_fssai

fbo_issue_bp = Blueprint('fbo_issue', __name__, template_folder='templates', static_folder='static')

VALID_TRANSITIONS = {
    'open': ['permission_pending', 'dismissed'],
    'permission_pending': ['permission_granted', 'dismissed'],
    'permission_granted': ['closed'],
    'closed': [],
    'dismissed': [],
}

SAMPLE_REQUIRED_FIELDS = {'sampling_date', 'sample_name', 'price', 'sample_code'}
INSPECTION_REQUIRED_FIELDS = {'checklist'}

def validate_detail_json(source_type, detail_json):
    """
    Validate that detail_json contains the required fields for the given source_type.
    Returns (is_valid, error_message)
    """
    ...

@fbo_issue_bp.route('/new', methods=['POST'])
def create_issue():
    """
    Create a new FBO issue.
    Required fields: source_type, fbo_id, fso_name
    """
    ...
    return jsonify({...}), 201

@fbo_issue_bp.route('/<int:issue_id>/transition', methods=['POST'])
def transition_issue(issue_id):
    """
    Transition an FBO issue to a new state.
    Required fields: to_state, asserted_by
    """
    ...
    return jsonify({...}), 200

@fbo_issue_bp.route('/<int:issue_id>', methods=['GET'])
def get_issue(issue_id):
    """
    Get full details of an FBO issue including its audit history.
    """
    ...
    return jsonify({...}), 200

@fbo_issue_bp.route('/', methods=['GET'])
def list_issues():
    """
    List FBO issues with optional filtering by fbo_id or state.
    """
    ...
    return jsonify(result), 200
```

### Path: app/inspection/__init__.py

```python
"""
Inspection module for NSA_webservice.
Provides inspection entry, tracking, and management.
"""
from flask import Blueprint

inspection_bp = Blueprint('inspection', __name__, template_folder='templates', static_folder='static')

from app.inspection import routes
```

### Path: app/inspection/routes.py

```python
"""
Inspection routes module.
Provides endpoints for Inspection CRUD operations and UI.
"""
from datetime import datetime, date
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from app.extensions import db
from app.models import Inspection, FSO, Adjudication, PhotoEvidence
from app.utils.lookup import lookup_fssai, lookup_ce
from app.utils.fso_data import get_all_fso_names
from app.inspection.inspection_utils import generate_inspection_code, calculate_compliance_deadline
from app.services.sheets_sync import sync_to_sheets
from app.inspection.verification_service import verify_photo_location
from app.inspection.image_processing import process_and_stamp_image
from app.inspection.audit import log_audit
import uuid
import os
from werkzeug.utils import secure_filename

from app.inspection import inspection_bp

@inspection_bp.route('/')
def index():
    """Inspection entry form page."""
    ...
    return render_template('inspection/index.html', fso_names=fso_names)

@inspection_bp.route('/list')
def list_inspections():
    """List all inspections with pagination, sorting, and filtering."""
    ...
    return render_template('inspection/list.html', ...)

@inspection_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    """Lookup FSSAI license information."""
    ...
    return jsonify({...})

@inspection_bp.route('/lookup_ce', methods=['POST'])
def lookup_ce_route():
    """Lookup CE (KMC Trade) license information."""
    ...
    return jsonify(result)

@inspection_bp.route('/create', methods=['POST'])
def create_inspection():
    """Create a new inspection record."""
    ...
    return jsonify({...}), 201

@inspection_bp.route('/<int:inspection_id>', methods=['GET'])
def get_inspection(inspection_id):
    """Get a specific inspection by ID."""
    ...
    return jsonify({...})

@inspection_bp.route('/<int:inspection_id>', methods=['PUT'])
def update_inspection(inspection_id):
    """Update an inspection record."""
    ...
    return jsonify({'message': 'Inspection updated successfully'}), 200

@inspection_bp.route('/<int:inspection_id>', methods=['DELETE'])
def delete_inspection(inspection_id):
    """Delete an inspection record."""
    ...
    return jsonify({'message': 'Inspection deleted successfully'}), 200

@inspection_bp.route('/open')
def open_issues():
    """Open Issues view: inspections where compliance_deadline >= today AND is_dismissed = false AND adjudication_id IS NULL."""
    ...
    return render_template('inspection/open_issues.html', ...)

@inspection_bp.route('/pending')
def pending_action():
    """Pending Action view: inspections where compliance_deadline < today AND is_dismissed = false AND adjudication_id IS NULL."""
    ...
    return render_template('inspection/pending_action.html', ...)

@inspection_bp.route('/history')
def history():
    """History view: inspections that are dismissed or have adjudication_id set."""
    ...
    return render_template('inspection/history.html', ...)

@inspection_bp.route('/<int:inspection_id>/dismiss', methods=['POST'])
def dismiss_inspection(inspection_id):
    """Dismiss an inspection (Pending Action only)."""
    ...
    return jsonify({...}), 200

@inspection_bp.route('/<int:inspection_id>/create_adjudication', methods=['GET'])
def create_adjudication_from_inspection(inspection_id):
    """Redirect to Adjudication form with prefill data from inspection."""
    ...
    return redirect(url_for('adjudication.index', **prefill))

@inspection_bp.route('/<int:inspection_id>/link_adjudication/<int:adjudication_id>', methods=['POST'])
def link_adjudication(inspection_id, adjudication_id):
    """Link an inspection to an adjudication after successful save."""
    ...
    return jsonify({...}), 200

@inspection_bp.route('/photo-upload', methods=['POST'])
def upload_photo_evidence():
    """Upload photo evidence for an inspection."""
    ...
    return jsonify({...}), 201
```

### Path: app/inspection/inspection_utils.py

```python
"""
inspection_utils.py
Utilities for the Inspection module, including inspection_code generation.
"""
import threading
from datetime import datetime, timedelta
from app.extensions import db
from app.models import Inspection

_inspection_code_lock = threading.Lock()

def generate_inspection_code() -> str:
    """
    Generate an inspection code in the format INSP-YYYY-#####.
    Uses a thread lock to ensure race-safe sequential writes for the same year.
    """
    ...

def calculate_compliance_deadline(inspection_date_str: str) -> str:
    """
    Calculate compliance deadline as inspection_date + 30 days.
    """
    ...
```

### Path: app/inspection/audit.py

```python
from datetime import datetime
from app.extensions import db
from app.models import AuditLog
import json
import hashlib

def compute_hash(prev_hash: str, entity_id: str, action: str, timestamp: str, details_json: str) -> str:
    """
    Returns sha256 hex digest of (prev_hash or "") + entity_id + action + timestamp + details_json.
    """
    ...

def log_audit(entity_type: str, entity_id: str, action: str, actor: str, details: dict) -> None:
    """
    Inserts a row into AuditLog table with hash chaining.
    Serializes details dict to JSON string for details_json column.
    """
    ...

def verify_audit_chain(entity_id: str) -> bool:
    """
    Fetches all AuditLog rows for entity_id, recomputes hash chain.
    Returns True if chain is intact, False on mismatch.
    """
    ...
```

### Path: app/inspection/verification_service.py

```python
from .geo_verification import reverse_geocode
from .ip_verification import ip_geolocate, region_match
from .distance_verification import haversine_distance, get_or_geocode_fbo_location

def verify_photo_location(raw_lat, raw_lng, accuracy, ip_address, fbo) -> dict:
    """
    Runs all verification checks and returns a combined result.
    Result dict: locality, ip_match, distance_to_fbo_m, verification_status, flag_reasons.
    """
    ...
```

### Path: app/inspection/image_processing.py

```python
from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime

def process_and_stamp_image(image_file, locality: str, captured_at: str, verification_status: str, image_id: str, case_id: str) -> str:
    """
    Takes an uploaded image file, processes it (resize, strip EXIF, stamp banner), saves as WebP.
    Returns the final filepath.
    """
    ...
```

### Path: app/inspection/geo_verification.py

```python
import requests
import time

_last_request_time = 0

def reverse_geocode(lat: float, lng: float) -> dict:
    """
    Calls Nominatim (OpenStreetMap) reverse geocoding API.
    Returns dict with locality, raw_response, error.
    """
    ...
```

### Path: app/inspection/ip_verification.py

```python
import requests
import re

def ip_geolocate(ip_address: str) -> dict:
    """
    Calls ip-api.com (free tier) to geolocate an IP address.
    Returns dict with region, city, error.
    """
    ...

def region_match(ip_city: str, ip_region: str, geocoded_locality: str) -> bool:
    """
    Compares IP-based city/region against the reverse-geocoded locality.
    Returns True if any overlap found, False otherwise.
    """
    ...
```

### Path: app/inspection/distance_verification.py

```python
import requests
import math
import time

_last_request_time = 0

def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Returns distance in meters between two lat/lng points."""
    ...

def geocode_fbo_address(address: str) -> dict:
    """
    Calls Nominatim forward-geocoding for a given address string.
    Returns dict with lat, lng, error.
    """
    ...

def get_or_geocode_fbo_location(fbo) -> tuple:
    """
    Takes an FBO object, returns (reg_lat, reg_lng) or geocodes the address.
    """
    ...
```

### Path: app/sample/__init__.py

```python
"""
Sample module for NSA_webservice.
This module handles sample collection, tracking, and management.
"""
from flask import Blueprint

sample_bp = Blueprint('sample', __name__, template_folder='templates', static_folder='static')

from app.sample import routes
```

### Path: app/sample/routes.py

```python
"""
Sample routes module.
Provides endpoints for Sample CRUD operations and UI.
"""
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from app.extensions import db
from app.models import Sample, FSO
from app.utils.lookup import lookup_fssai
from app.utils.fso_data import get_all_fso_names
from app.sample.sample_utils import generate_sample_code
from app.services.sheets_sync import sync_to_sheets

from app.sample import sample_bp

SAMPLE_TYPES = ['enforcement', 'surveillance']

@sample_bp.route('/')
def index():
    """Sample entry form page."""
    ...
    return render_template('sample/index.html', fso_names=fso_names, sample_types=SAMPLE_TYPES)

@sample_bp.route('/list')
def list_samples():
    """List all samples with pagination, sorting, and filtering."""
    ...
    return render_template('sample/list.html', ...)

@sample_bp.route('/lookup_retailer', methods=['POST'])
def lookup_retailer():
    """Lookup retailer information by FSSAI number."""
    ...
    return jsonify({...})

@sample_bp.route('/create', methods=['POST'])
def create_sample():
    """Create a new sample record."""
    ...
    return jsonify({...}), 201

@sample_bp.route('/<int:sample_id>', methods=['GET'])
def get_sample(sample_id):
    """Get a specific sample by ID."""
    ...
    return jsonify({...})

@sample_bp.route('/<int:sample_id>', methods=['PUT'])
def update_sample(sample_id):
    """Update a sample record."""
    ...
    return jsonify({'message': 'Sample updated successfully'}), 200

@sample_bp.route('/<int:sample_id>', methods=['DELETE'])
def delete_sample(sample_id):
    """Delete a sample record."""
    ...
    return jsonify({'message': 'Sample deleted successfully'}), 200
```

### Path: app/sample/sample_utils.py

```python
"""
sample_utils.py
Utilities for the Sample module, including sample_code generation.
"""
import threading
from datetime import datetime
from app.extensions import db
from app.models import Sample

_sample_code_lock = threading.Lock()

def generate_sample_code() -> str:
    """
    Generate a sample code in the format SKS-YYYY-#####.
    Uses a thread lock to ensure race-safe sequential writes for the same year.
    """
    ...
```

### Path: app/services/__init__.py

```python
# Services package
# This package contains service modules for the NSA Webservice application
```

### Path: app/services/sheets_sync.py

```python
import os
from flask import current_app
import gspread

WORKSHEET_MAP = {
    "non_sample": "NonSample_Adjudication",
    "sample": "Sample_CaseFile",
    "billing": "Billing",
    "sample_repo": "Sample_Repository",
    "inspection_log": "Inspection_Log",
}

SHEET_COLUMNS = {
    "non_sample": [...],
    "sample": [...],
    "billing": [...],
    "sample_repo": [...],
    "inspection_log": [...],
}

_client_cache = None

def get_client():
    """
    Get a cached gspread client using service-account authentication.
    Tries instance/credentials.json, GOOGLE_APPLICATION_CREDENTIALS env var, then default service account.
    """
    ...

_ws_cache = {}

def get_worksheet(module):
    """Get a cached worksheet for the specified module."""
    ...

def sync_to_sheets(module: str, row_dict: dict) -> bool:
    """
    Sync a row of data to the appropriate Google Sheet.
    Returns True if sync succeeded, False otherwise.
    """
    ...
```

### Path: app/settings/__init__.py

```python
"""
Settings module for NSA_webservice.
Provides administrative and settings functionality.
"""
from flask import Blueprint

settings_bp = Blueprint('settings', __name__, template_folder='templates', static_folder='static')

from app.settings import routes
```

### Path: app/settings/routes.py

```python
"""
Settings routes module.
Provides administrative routes including FSO sync.
"""
from flask import Blueprint, jsonify, render_template
from app.utils.fso_data import sync_fso_from_markdown, get_all_fso_names

from app.settings import settings_bp

@settings_bp.route('/')
def index():
    """Settings dashboard."""
    ...
    return render_template('settings/index.html', fso_names=fso_names)

@settings_bp.route('/sync-fso', methods=['POST'])
def sync_fso():
    """Manual FSO sync trigger."""
    ...
    return jsonify({'status': ..., 'result': result})
```

### Path: app/shared/__init__.py

```python
"""
Shared context package for NSA_webservice uniform-keys migration.
Re-exports all canonical key constants, mappings, TypedDicts, and helper functions.
"""
from app.shared.case_keys import (
    SHARED_FSO_NAME, SHARED_FBO_OWNER, SHARED_FBO_NAME, SHARED_FBO_ADDRESS,
    SHARED_FSSAI_LICENSE, SHARED_CASE_NUMBER, SHARED_AUTHORIZATION_DATE,
    SHARED_COMPLIANCE_DEADLINE, SHARED_CONCERNED_FOOD, SHARED_PROBLEM,
    SHARED_COMPLAINT_LODGED, SHARED_NON_LICENSE, SHARED_PRE_AUTHORIZATION, SHARED_FROM_INSPECTION,
    DATE_INSPECTION, DATE_FIRST_INSPECTION, DATE_FOLLOWUP_INSPECTION, DATE_COMPLAINT,
    DATE_SAMPLE_DRAW, DATE_SAMPLE_DRAW_TIME, DATE_SAMPLE_SUBMISSION,
    DATE_DO_RECEIPT, DATE_ANALYST_REPORT, DATE_DIRECTIVE_LETTER,
    DATE_RETAILER_REPORT_RECEIVE, DATE_MANUFACTURER_REPORT_RECEIVE,
    PARTY_MANUFACTURER_PERSON_NAME, PARTY_MANUFACTURER_TRADE_NAME, PARTY_MANUFACTURER_ADDRESS, PARTY_MANUFACTURER_FSSAI,
    PARTY_RETAILER_PERSON_NAME, PARTY_RETAILER_TRADE_NAME, PARTY_RETAILER_ADDRESS, PARTY_RETAILER_FSSAI,
    PARTY_SAME_ENTITY,
    SAMPLE_ID, SAMPLE_CODE, SAMPLE_NAME, SAMPLE_TYPE, SAMPLE_PRODUCT_NAME,
    SAMPLE_BATCH_NO, SAMPLE_QUANTITY, SAMPLE_PACKET_COUNT, SAMPLE_MFG_DATE, SAMPLE_EXPIRY_DATE,
    SAMPLE_TOTAL_COST, SAMPLE_COST_IN_WORDS, SAMPLE_OTHER_FOOD_ARTICLES,
    LAB_REGISTRATION_NO, LAB_ANALYST_REPORT_NO, LAB_DIRECTIVE_LETTER_NO,
    SAMPLE_APPLICABLE_REGULATION, SAMPLE_APPLICABLE_CLAUSE,
    SAMPLE_IS_SUBSTANDARD, SAMPLE_IS_MISBRANDED,
    SECTION_55, SECTION_56, SECTION_58, SECTION_63, SECTION_64, SECTION_KEYS,
    DERIVED_APPLICABLE_SECTIONS, DERIVED_SECTIONS_DISPLAY, DERIVED_CASE_TRACK,
    DERIVED_VIOLATIONS, DERIVED_SAME_ENTITY, DERIVED_DOCUMENT_ROLE,
    INSPECTION_OLD_TO_NEW, INSPECTION_NEW_TO_OLD,
    SAMPLE_OLD_TO_NEW, SAMPLE_NEW_TO_OLD,
    ADJUDICATION_OLD_TO_NEW, ADJUDICATION_NEW_TO_OLD,
    CASE_FILE_OLD_TO_NEW, CASE_FILE_NEW_TO_OLD,
    ViolationDict, ApplicableSectionsShape,
    sections_display, resolve_case_track, get_hygienic_sections,
    get_nonsample_licence_sections, get_sample_sections,
)
from app.shared.context_derivers import (
    CHECKLIST_RULES, SPECIAL_VIOLATION_RULES,
    derive_applicable_sections_from_case_file,
    derive_applicable_sections_from_adjudication,
    derive_applicable_sections_from_form_data,
    derive_sections_display, derive_case_track,
    derive_violations, derive_same_entity,
    derive_case_file_context, derive_adjudication_context,
)
```

### Path: app/shared/case_keys.py

```python
"""
Uniform keys contract for NSA_webservice migration.
Defines canonical key naming for all case-related data across four UIs.
"""
from typing import TypedDict, NotRequired

# Canonical Key Constants
SHARED_FSO_NAME = "food_safety_officer_name"
SHARED_FBO_OWNER = "fbo_owner"
SHARED_FBO_NAME = "fbo_name"
SHARED_FBO_ADDRESS = "fbo_address"
SHARED_FSSAI_LICENSE = "fssai_license"
SHARED_CASE_NUMBER = "case_number"
SHARED_AUTHORIZATION_DATE = "authorization_date"
SHARED_COMPLIANCE_DEADLINE = "compliance_deadline"
SHARED_CONCERNED_FOOD = "concerned_food"
SHARED_PROBLEM = "problem"
SHARED_COMPLAINT_LODGED = "complaint_lodged"
SHARED_NON_LICENSE = "non_license"
SHARED_PRE_AUTHORIZATION = "pre_authorization"
SHARED_FROM_INSPECTION = "from_inspection"
DATE_INSPECTION = "inspection_date"
DATE_FIRST_INSPECTION = "first_inspection_date"
DATE_FOLLOWUP_INSPECTION = "followup_inspection_date"
DATE_COMPLAINT = "complaint_date"
DATE_SAMPLE_DRAW = "sample_draw_date"
DATE_SAMPLE_DRAW_TIME = "sample_draw_time"
DATE_SAMPLE_SUBMISSION = "sample_submission_date"
DATE_DO_RECEIPT = "do_receipt_date"
DATE_ANALYST_REPORT = "analyst_report_date"
DATE_DIRECTIVE_LETTER = "directive_letter_date"
DATE_RETAILER_REPORT_RECEIVE = "retailer_report_receive_date"
DATE_MANUFACTURER_REPORT_RECEIVE = "manufacturer_report_receive_date"
PARTY_MANUFACTURER_PERSON_NAME = "manufacturer_person_name"
PARTY_MANUFACTURER_TRADE_NAME = "manufacturer_trade_name"
PARTY_MANUFACTURER_ADDRESS = "manufacturer_address"
PARTY_MANUFACTURER_FSSAI = "manufacturer_fssai_license"
PARTY_RETAILER_PERSON_NAME = "retailer_person_name"
PARTY_RETAILER_TRADE_NAME = "retailer_trade_name"
PARTY_RETAILER_ADDRESS = "retailer_address"
PARTY_RETAILER_FSSAI = "retailer_fssai_license"
PARTY_SAME_ENTITY = "same_entity"
SAMPLE_ID = "sample_id"
SAMPLE_CODE = "sample_code"
SAMPLE_NAME = "sample_name"
SAMPLE_TYPE = "sample_type"
SAMPLE_PRODUCT_NAME = "product_name"
SAMPLE_BATCH_NO = "batch_no"
SAMPLE_QUANTITY = "sample_quantity"
SAMPLE_PACKET_COUNT = "packet_count"
SAMPLE_MFG_DATE = "mfg_date"
SAMPLE_EXPIRY_DATE = "expiry_date"
SAMPLE_TOTAL_COST = "total_cost"
SAMPLE_COST_IN_WORDS = "cost_in_words"
SAMPLE_OTHER_FOOD_ARTICLES = "other_food_articles"
LAB_REGISTRATION_NO = "lab_registration_no"
LAB_ANALYST_REPORT_NO = "analyst_report_no"
LAB_DIRECTIVE_LETTER_NO = "directive_letter_no"
SAMPLE_APPLICABLE_REGULATION = "applicable_regulation"
SAMPLE_APPLICABLE_CLAUSE = "applicable_clause"
SAMPLE_IS_SUBSTANDARD = "is_substandard"
SAMPLE_IS_MISBRANDED = "is_misbranded"
SECTION_55 = "section_55"
SECTION_56 = "section_56"
SECTION_58 = "section_58"
SECTION_63 = "section_63"
SECTION_64 = "section_64"
SECTION_KEYS = (SECTION_55, SECTION_56, SECTION_58, SECTION_63, SECTION_64)
DERIVED_APPLICABLE_SECTIONS = "applicable_sections"
DERIVED_SECTIONS_DISPLAY = "sections_display"
DERIVED_CASE_TRACK = "case_track"
DERIVED_VIOLATIONS = "violations"
DERIVED_SAME_ENTITY = "same_entity"
DERIVED_DOCUMENT_ROLE = "document_role"

INSPECTION_OLD_TO_NEW = {...}
INSPECTION_NEW_TO_OLD = {...}
SAMPLE_OLD_TO_NEW = {...}
SAMPLE_NEW_TO_OLD = {...}
ADJUDICATION_OLD_TO_NEW = {...}
ADJUDICATION_NEW_TO_OLD = {...}
CASE_FILE_OLD_TO_NEW = {...}
CASE_FILE_NEW_TO_OLD = {...}

class ViolationDict(TypedDict):
    title: str
    observation: str

class ApplicableSectionsShape(TypedDict):
    sections: list[str]
    display: str

def sections_display(sections: list[str]) -> str:
    """Convert a list of section numbers to a human-readable display string."""
    ...

def resolve_case_track(non_license=False, pre_authorization=False, complaint_lodged=False, is_sample=False) -> str:
    """Determine the case track based on case characteristics."""
    ...

def get_hygienic_sections() -> list[str]:
    return ["55", "56", "58"]

def get_nonsample_licence_sections() -> list[str]:
    return ["63"]

def get_sample_sections(is_substandard=True, is_misbranded=True) -> list[str]:
    """Return sample-based sections based on analysis results."""
    ...

__all__ = [...]  # Full export list
```

### Path: app/shared/context_derivers.py

```python
"""
Derived context helpers for document generation (STEP 4 of uniform-keys migration).
All functions are pure (no side effects, same input -> same output).
"""
from typing import TypedDict, Optional
from app.shared.case_keys import (
    SECTION_KEYS, SECTION_55, SECTION_56, SECTION_58, SECTION_63, SECTION_64,
    SAMPLE_IS_SUBSTANDARD, SAMPLE_IS_MISBRANDED,
    SHARED_NON_LICENSE, SHARED_PRE_AUTHORIZATION, SHARED_COMPLAINT_LODGED,
    PARTY_MANUFACTURER_FSSAI, PARTY_RETAILER_FSSAI,
    DERIVED_APPLICABLE_SECTIONS, DERIVED_SECTIONS_DISPLAY, DERIVED_CASE_TRACK,
    DERIVED_VIOLATIONS, DERIVED_SAME_ENTITY,
)

CHECKLIST_RULES: dict[str, tuple[str, str]] = {...}
SPECIAL_VIOLATION_RULES: dict[str, tuple[str, str]] = {...}

def derive_applicable_sections_from_case_file(is_substandard=False, is_misbranded=False) -> list[str]:
    """Derive applicable sections for case file (sample-based) cases."""
    ...

def derive_applicable_sections_from_adjudication(section_55=False, section_56=False, section_58=False, section_63=False, section_64=False) -> list[str]:
    """Derive applicable sections from adjudication form checkboxes."""
    ...

def derive_applicable_sections_from_form_data(form_data: dict) -> list[str]:
    """Derive applicable sections by detecting the case type from form data."""
    ...

def derive_sections_display(applicable_sections: list[str]) -> str:
    """Convert a list of section numbers to a human-readable display string."""
    ...

def derive_case_track(non_license=False, pre_authorization=False, complaint_lodged=False, is_sample=False) -> str:
    """Determine the case track based on case characteristics."""
    ...

def derive_violations(form_data: dict) -> list[dict[str, str]]:
    """Derive violations list for adjudication cases."""
    ...

def derive_same_entity(manufacturer_fssai=None, retailer_fssai=None) -> bool:
    """Determine if manufacturer and retailer are the same entity."""
    ...

def derive_case_file_context(form_data: dict) -> dict:
    """Derive all context fields for case file generator."""
    ...

def derive_adjudication_context(form_data: dict) -> dict:
    """Derive all context fields for adjudication."""
    ...

__all__ = [...]
```

### Path: app/utils/__init__.py

```python
# Empty file
```

### Path: app/utils/filters.py

```python
from datetime import datetime
from num2words import num2words

def to_words(number):
    """
    Jinja filter to convert a number (integer or float) to Indian currency word representation.
    """
    ...

def format_date_indian(date_val):
    """
    Jinja filter to convert a date string or datetime object to Indian DD-MM-YYYY format.
    """
    ...
```

### Path: app/utils/lookup.py

```python
import os
import sqlite3
import ssl
import re
import json
import httpx
import time
from threading import Lock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', '..'))
DB_DIR = os.path.join(WORKSPACE_DIR, "db")
LICENSE_DB_PATH = os.path.join(DB_DIR, "license_data.db")
REGISTRATION_DB_PATH = os.path.join(DB_DIR, "registration_data.db")

_kmc_last_request_time = 0
_kmc_rate_limit_lock = Lock()
_KMC_RATE_LIMIT_SECONDS = 40

def lookup_fssai(license_no: str):
    """
    Look up an FSSAI License/Registration number from local SQLite databases.
    Returns a dict with companyName/fullAddress/expiryDate/source, or None if not found.
    """
    ...

def lookup_ce(license_no: str):
    """
    Fetches Trade License details from KMC portal with rate limiting.
    """
    ...
```

### Path: app/utils/fso_data.py

```python
"""
fso_data.py
Loads the FSO list from a markdown file and exposes FSO names.
Sync is ADDITIVE ONLY.
"""
import os
import re
import logging
from app.extensions import db
from app.models import FSO

logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', '..'))
FSO_MD_PATH = os.path.join(WORKSPACE_DIR, "fso_list.md")

def load_fso_names(path=FSO_MD_PATH) -> list:
    """
    Parses a markdown file with a list of FSO names.
    Returns a list of name strings.
    """
    ...

def sync_fso_from_markdown(path=FSO_MD_PATH) -> dict:
    """
    Reads FSO names from the markdown file and upserts them into the fso table.
    Returns dict with inserted, updated, skipped counts and errors.
    """
    ...

def get_all_fso_names() -> list:
    """Returns a list of all FSO names from the database, sorted alphabetically."""
    ...

def sync_fso_manually():
    """Manual trigger for FSO sync. Called from route."""
    return sync_fso_from_markdown()

try:
    FSO_NAMES = load_fso_names()
except FileNotFoundError:
    FSO_NAMES = []
```

### Path: app/utils/suggester.py

```python
import logging
from .sections_data import SECTIONS, VALID_SECTION_IDS

logger = logging.getLogger(__name__)

_NON_CHECKLIST_FIELDS = {...}
_HYGIENE_CHECKLIST_ITEMS = {...}
_MANUAL_ONLY_SECTIONS = {"58", "64"}
_DIRECTION_COMPLIANCE_ITEMS = {...}

def _is_non_license(form_data: dict) -> bool:
    ...

def _detect_section_56_from_checklist(form_data: dict) -> tuple:
    ...

def _detect_section_55_from_checklist(form_data: dict) -> tuple:
    ...

def suggest_sections(form_data: dict) -> dict:
    """
    Rule-based section suggestion.
    Rules:
      1. Non-licensed FBO -> Section 63 only.
      2. Licensed FBO -> Section 55 if checklist shows direction non-compliance.
      3. Section 56 if hygiene violations detected in checklist.
      4. Sections 58 and 64 are manual-only (officer ticks in UI).
    """
    ...
```

### Path: app/utils/pdf_utils.py

```python
"""
PDF generation utilities with graceful WeasyPrint handling.
"""
import os

PDF_GENERATION_ENABLED = os.environ.get('DISABLE_PDF_GENERATION', 'false').lower() != 'true'

def import_weasyprint():
    """
    Import WeasyPrint with graceful error handling.
    Returns None if WeasyPrint cannot be imported.
    """
    ...

def generate_pdf_from_html(html_content):
    """
    Generate PDF from HTML string using WeasyPrint.
    Returns (pdf_bytes, error_message) tuple.
    """
    ...
```

### Path: app/utils/sections_data.py

```python
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECTION_MD_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "fss_sections.md"))

def load_sections(path=SECTION_MD_PATH) -> dict:
    """
    Parses a markdown file with '# Section NN' headers into dict: {section_no: full_text}.
    """
    ...

SECTIONS = load_sections()
VALID_SECTION_IDS = {"55", "56", "58", "63", "64"}