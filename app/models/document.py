"""Document, case file, and evidence models."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy.orm import validates

from app.extensions import db


class CaseFile(db.Model):
    __tablename__ = "case_files"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {
        "version_id_col": version_id,
    }
    case_number = db.Column(db.String(100), nullable=False)
    food_safety_officer_name = db.Column(db.String(100), nullable=False)
    authorization_date = db.Column(db.DateTime, nullable=False)
    inspection_date = db.Column(db.DateTime, nullable=False)
    inspection_time = db.Column(db.String(100), nullable=False)

    # Link to Sample (optional FK - Step 5 addition)
    sample_id = db.Column(db.Integer, db.ForeignKey("sample.id", ondelete="SET NULL"), nullable=True)

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
    mfg_date = db.Column(db.DateTime, nullable=False)
    expiry_date = db.Column(db.DateTime, nullable=False)
    other_food_articles = db.Column(db.String(500))
    total_cost = db.Column(db.String(50))
    cost_in_words = db.Column(db.String(200))

    # Sample details
    sample_code = db.Column(db.String(100), nullable=False)
    sample_submission_date = db.Column(db.DateTime, nullable=False)
    Lab_Registration_No = db.Column(db.String(100), nullable=False)
    do_receipt_date = db.Column(db.DateTime, nullable=False)

    # Results
    is_misbranded = db.Column(db.Boolean, default=False)
    is_substandard = db.Column(db.Boolean, default=False)
    analyst_report_no = db.Column(db.String(100), nullable=False)
    analyst_report_date = db.Column(db.DateTime, nullable=False)
    directive_letter_no = db.Column(db.String(100), nullable=False)
    directive_letter_date = db.Column(db.DateTime, nullable=False)
    retailer_report_receive_date = db.Column(db.DateTime, nullable=False)
    manufacturer_report_receive_date = db.Column(db.DateTime, nullable=False)

    applicable_regulation = db.Column(db.String(200))
    applicable_clause = db.Column(db.String(200))
    sample_name = db.Column(db.String(200))
    applicable_sections = db.Column(db.String(50))

    # Audit & Sync fields
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    synced_at = db.Column(db.DateTime, nullable=True)

    # PDF generation tracking (populated by Celery task)
    pdf_task_id = db.Column(db.String(100), nullable=True)
    pdf_generated_at = db.Column(db.DateTime, nullable=True)

    @validates("sample_id")
    def sync_sample_code(self, key, sample_id):
        if sample_id is not None:
            from app.models import Sample

            sample = db.session.get(Sample, sample_id)
            if sample:
                self.sample_code = sample.sample_code
        return sample_id


class Adjudication(db.Model):
    __tablename__ = "adjudications"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {
        "version_id_col": version_id,
    }
    case_number = db.Column(db.String(100), nullable=False)
    food_safety_officer = db.Column(db.String(100), nullable=False)

    # Flags
    non_license = db.Column(db.String(10), default="no")
    pre_authorization = db.Column(db.String(10), default="no")
    complaint_lodged = db.Column(db.String(10), default="no")

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
    First_inspection_date = db.Column(db.DateTime, nullable=False)
    compliance_deadline = db.Column(db.DateTime, nullable=False)
    Complaint_date = db.Column(db.DateTime)
    inspection_date = db.Column(db.DateTime, nullable=False)
    authorization_date = db.Column(db.DateTime)

    # Checklist items (storing as string 'yes'/'no')
    clean_premise = db.Column(db.String(10), default="yes")
    refrigerator_clean = db.Column(db.String(10), default="yes")
    proper_attire = db.Column(db.String(10), default="yes")
    proper_covered_utensil = db.Column(db.String(10), default="yes")
    date_tag = db.Column(db.String(10), default="yes")
    veg_nonveg_separation = db.Column(db.String(10), default="yes")
    food_segregation = db.Column(db.String(10), default="yes")
    license_display = db.Column(db.String(10), default="yes")
    artificial_colour = db.Column(db.String(10), default="no")
    Expired_item = db.Column(db.String(10), default="no")
    Pest_report = db.Column(db.String(10), default="yes")
    Water_report = db.Column(db.String(10), default="yes")

    # Selected sections
    section_55 = db.Column(db.String(10), default="no")
    section_56 = db.Column(db.String(10), default="no")
    section_58 = db.Column(db.String(10), default="no")
    section_63 = db.Column(db.String(10), default="no")
    section_64 = db.Column(db.String(10), default="no")

    # Audit & Sync fields
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    synced_at = db.Column(db.DateTime, nullable=True)

    # Photo evidence — unified Evidence model (Phase 5); photos are
    # queried via ``Evidence.query.filter_by(adjudication_id=...,
    # evidence_type="photo")`` rather than an ORM relationship.


class Annexure(db.Model):
    """Uploaded supporting documents (PDF, JPG, PNG, DOCX).

    Each annexure is attached to either a ``case_files`` or ``adjudications``
    record and carries metadata extracted at upload time: SHA-256 hash,
    page count, OCR text, and free-form tags.
    """

    __tablename__ = "annexures"

    id = db.Column(db.String(36), primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case_files.id", ondelete="SET NULL"), nullable=True)
    adjudication_id = db.Column(db.Integer, db.ForeignKey("adjudications.id", ondelete="SET NULL"), nullable=True)

    caption = db.Column(db.String(200), nullable=False)
    date = db.Column(db.DateTime, nullable=True)
    file_hash = db.Column(db.String(64), nullable=False)
    page_count = db.Column(db.Integer, nullable=True)
    ocr_text = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(500), nullable=True)

    filepath = db.Column(db.String(500), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    annexure_letter = db.Column(db.String(1), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_annexures_case_id", "case_id"),
        db.Index("idx_annexures_adjudication_id", "adjudication_id"),
        db.Index("idx_annexures_file_hash", "file_hash"),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.id:
            self.id = str(uuid.uuid4())

    def __repr__(self):
        return f"<Annexure {self.id[:8]} '{self.caption}'>"


class Evidence(db.Model):
    """Unified evidence model supporting all evidence types.

    Single home for every evidence record: photos (migrated from the
    former ``PhotoEvidence`` / ``InspectionPhoto`` tables in Phase 5),
    videos, reports, licences, bills, and lab reports. Photo-specific
    geolocation fields are nullable so non-photo evidence types reuse
    the same table.
    """

    __tablename__ = "evidence"

    EVIDENCE_TYPES = (
        "photo",
        "video",
        "report",
        "licence",
        "bill",
        "lab_report",
    )

    id = db.Column(db.String(36), primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case_files.id", ondelete="SET NULL"), nullable=True)
    adjudication_id = db.Column(db.Integer, db.ForeignKey("adjudications.id", ondelete="SET NULL"), nullable=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey("inspection.id", ondelete="SET NULL"), nullable=True)

    evidence_type = db.Column(db.String(20), nullable=False)

    filepath = db.Column(db.String, nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)
    file_hash = db.Column(db.String(64), nullable=True)

    raw_lat = db.Column(db.Float, nullable=True)
    raw_lng = db.Column(db.Float, nullable=True)
    accuracy = db.Column(db.Float, nullable=True)
    captured_at = db.Column(db.DateTime, nullable=True)
    locality = db.Column(db.String, nullable=True)
    ip_region = db.Column(db.String, nullable=True)
    ip_match = db.Column(db.Boolean, nullable=True)
    distance_to_fbo_m = db.Column(db.Float, nullable=True)
    verification_status = db.Column(db.String, default="PENDING")
    stamped = db.Column(db.Boolean, default=False)

    caption = db.Column(db.String(200), nullable=True)
    ocr_text = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(500), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_evidence_case_id", "case_id"),
        db.Index("idx_evidence_type", "evidence_type"),
        db.Index("idx_evidence_adjudication_id", "adjudication_id"),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.id:
            self.id = str(uuid.uuid4())

    def __repr__(self):
        return f"<Evidence {self.id[:8]} ({self.evidence_type})>"


class Version(db.Model):
    """Version history table: snapshot-on-save of edited documents.

    Stores a snapshot of the HTML (+ optional Delta) every time a document
    is saved or auto-saved, enabling compare/restore workflows.
    """

    __tablename__ = "versions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case_files.id", ondelete="SET NULL"), nullable=True)
    adjudication_id = db.Column(db.Integer, db.ForeignKey("adjudications.id", ondelete="SET NULL"), nullable=True)
    doc_type = db.Column(db.String(20), nullable=False)  # petition | permission
    version_number = db.Column(db.Integer, nullable=False)
    content_hash = db.Column(db.String(64), nullable=False)  # SHA256 of content
    html_snapshot = db.Column(db.Text, nullable=False)
    delta = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    change_summary = db.Column(db.Text, nullable=True)

    # Branch/draft support (Phase 9): ``branch_name`` is NULL on the mainline
    # and a free-form label on branch versions; ``branch_of`` points at the
    # source version the branch was forked from.
    branch_name = db.Column(db.String(100), nullable=True)
    branch_of = db.Column(db.Integer, db.ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        db.Index(
            "uq_version_case_doc",
            "case_id",
            "doc_type",
            "version_number",
            unique=True,
            sqlite_where=db.text("branch_name IS NULL"),
            postgresql_where=db.text("branch_name IS NULL"),
        ),
        db.Index(
            "uq_version_case_doc_branch",
            "case_id",
            "doc_type",
            "version_number",
            "branch_name",
            unique=True,
            sqlite_where=db.text("branch_name IS NOT NULL"),
            postgresql_where=db.text("branch_name IS NOT NULL"),
        ),
        db.Index(
            "uq_version_adjudication_doc",
            "adjudication_id",
            "doc_type",
            "version_number",
            unique=True,
            sqlite_where=db.text("branch_name IS NULL"),
            postgresql_where=db.text("branch_name IS NULL"),
        ),
        db.Index(
            "uq_version_adjudication_doc_branch",
            "adjudication_id",
            "doc_type",
            "version_number",
            "branch_name",
            unique=True,
            sqlite_where=db.text("branch_name IS NOT NULL"),
            postgresql_where=db.text("branch_name IS NOT NULL"),
        ),
        db.Index("idx_version_case_id", "case_id"),
        db.Index("idx_version_adjudication_id", "adjudication_id"),
        db.Index("idx_version_content_hash", "content_hash"),
        db.Index("idx_version_created_at", "created_at"),
        db.Index("idx_version_user_id", "user_id"),
        db.Index("idx_version_branch_of", "branch_of"),
    )

    def __repr__(self):
        return "<Version " + str(self.doc_type) + "#" + str(self.version_number) + ">"


class TimelineEvent(db.Model):
    """Auto-generated milestone events for a case (Phase 13 timeline engine).

    Populated by ``app/timeline/engine.py::TimelineEngine.extract_events_from_case``
    from date fields across ``CaseFile``, ``Inspection``, ``Sample``, and
    ``Adjudication`` records.  Rendered by the Gantt/timeline UI.
    """

    __tablename__ = "timeline_event"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    case_id = db.Column(db.Integer, db.ForeignKey("case_files.id", ondelete="CASCADE"), nullable=False)
    case_type = db.Column(db.String(32), nullable=False, default="case_file")

    event_type = db.Column(db.String(64), nullable=False)  # inspection | sampling | lab_report | notice | ...
    timestamp = db.Column(db.DateTime, nullable=False)
    document_ref = db.Column(db.String(256), nullable=True)  # Annexure id or document link
    description = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_timeline_event_case_id", "case_id"),
        db.Index("idx_timeline_event_timestamp", "timestamp"),
        db.Index("idx_timeline_case_ts", "case_id", "timestamp"),
        db.Index("idx_timeline_event_type", "case_type", "event_type"),
    )

    def __repr__(self) -> str:
        return f"<TimelineEvent {self.case_type}:{self.event_type}@{self.timestamp}>"


class Entity(db.Model):
    """Knowledge-graph node (Phase 14).

    One row per graph entity (case, FBO, inspector, sample, lab, legal section,
    evidence).  ``source_table``/``source_id`` link back to the originating
    record so edges can be reconstructed.  ``metadata_json`` stores free-form
    attributes surfaced via the Cytoscape.js view.
    """

    __tablename__ = "entity"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entity_type = db.Column(db.String(64), nullable=False)  # case|fbo|inspector|sample|lab|section|evidence
    name = db.Column(db.String(255), nullable=False)

    # Polymorphic back-reference to the source record
    source_table = db.Column(db.String(64), nullable=True)  # e.g. case_files, adjudications
    source_id = db.Column(db.Integer, nullable=True)

    metadata_json = db.Column(db.Text, nullable=True)  # JSON blob of extra attributes

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_entity_type", "entity_type"),
        db.Index("idx_entity_type_source", "entity_type", "source_table", "source_id"),
    )

    def __repr__(self) -> str:
        return f"<Entity {self.entity_type}:{self.name}>"


class Relationship(db.Model):
    """Knowledge-graph edge between two ``Entity`` nodes (Phase 14).

    ``relationship_type`` is one of the directed labels: INSPECTED_BY,
    SAMPLED_FROM, TESTED_AT, VIOLATED_SECTION, SUPPORTED_BY.  ``weight`` is
    the confidence of the inferred edge.
    """

    __tablename__ = "relationship"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    source_id = db.Column(db.Integer, db.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey("entity.id", ondelete="CASCADE"), nullable=False)
    relationship_type = db.Column(db.String(64), nullable=False)
    weight = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.UniqueConstraint("source_id", "target_id", "relationship_type", name="uq_relationship_edge"),
        db.Index("idx_relationship_source_id", "source_id"),
        db.Index("idx_relationship_target_id", "target_id"),
        db.Index("idx_relationship_type", "relationship_type"),
    )

    def __repr__(self) -> str:
        return f"<Relationship {self.source_id}->{self.target_id} ({self.relationship_type})>"
