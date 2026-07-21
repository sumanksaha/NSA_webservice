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
    
    # Link to Sample (optional FK - Step 5 addition)
    sample_id = db.Column(db.Integer, db.ForeignKey('sample.id', ondelete='SET NULL'), nullable=True)
    
    # Manufacturer details
    manufacturer_fssai = db.Column(db.String(50), nullable=False)
    manufacturer_name = db.Column(db.String(200), nullable=False)
    manufacturer_fbo_name = db.Column(db.String(200), nullable=False)
    manufacturer_address = db.Column(db.Text, nullable=False)
    
    # Retailer details
    retailer_fssai = db.Column(db.String(50), nullable=False)
    retailer_name = db.Column(db.String(200), nullable=False)
    retailer_fbo_name = db.Column(db.String(200), nullable=False)
    retailer_address = db.Column(db.Text, nullable=False)
    
    # Product details
    product_name = db.Column(db.String(200), nullable=False)
    batch_no = db.Column(db.String(100), nullable=False)
    sample_quantity = db.Column(db.String(100), nullable=False)
    packet_count = db.Column(db.Integer, nullable=False)
    mfg_date = db.Column(db.String(100), nullable=False)
    expiry_date = db.Column(db.String(100), nullable=False)
    other_food_articles = db.Column(db.String(500))
    total_cost = db.Column(db.String(50))
    cost_in_words = db.Column(db.String(200))
    
    # Sample details
    sample_code = db.Column(db.String(100), nullable=False)
    sample_submission_date = db.Column(db.String(100), nullable=False)
    Lab_Registration_No = db.Column(db.String(100), nullable=False)
    do_receipt_date = db.Column(db.String(100), nullable=False)
    
    # Results
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
    
    # Audit & Sync fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)


class Adjudication(db.Model):
    __tablename__ = 'adjudications'
    
    id = db.Column(db.Integer, primary_key=True)
    case_number = db.Column(db.String(100), nullable=False)
    food_safety_officer = db.Column(db.String(100), nullable=False)
    
    # Flags
    non_license = db.Column(db.String(10), default='no')
    pre_authorization = db.Column(db.String(10), default='no')
    complaint_lodged = db.Column(db.String(10), default='no')
    
    # KMC lookup fields (nullable)
    ce_license_no = db.Column(db.String(100))
    ce_trade_name = db.Column(db.String(200))
    ce_proprietor = db.Column(db.String(200))
    ce_address = db.Column(db.Text)
    ce_status = db.Column(db.String(100))
    
    # FBO metadata
    fbo_owner = db.Column(db.String(200), nullable=False)
    fbo_name = db.Column(db.String(200), nullable=False)
    fbo_address = db.Column(db.Text, nullable=False)
    fssai_license = db.Column(db.String(100), nullable=False)
    concerned_food = db.Column(db.String(200))
    problem = db.Column(db.Text)
    
    # Dates
    First_inspection_date = db.Column(db.String(100), nullable=False)
    compliance_deadline = db.Column(db.String(100), nullable=False)
    Complaint_date = db.Column(db.String(100))
    inspection_date = db.Column(db.String(100), nullable=False)
    authorization_date = db.Column(db.String(100))
    
    # Checklist items (storing as string 'yes'/'no')
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
    
    # Selected sections
    section_55 = db.Column(db.String(10), default='no')
    section_56 = db.Column(db.String(10), default='no')
    section_58 = db.Column(db.String(10), default='no')
    section_63 = db.Column(db.String(10), default='no')
    section_64 = db.Column(db.String(10), default='no')
    
    # Audit & Sync fields
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
    
    # Audit & Sync fields
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    synced_at = db.Column(db.DateTime, nullable=True)
    
    # Relationship to samples
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
    case_id = db.Column(db.String, db.ForeignKey('cases.id'), nullable=False)
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
