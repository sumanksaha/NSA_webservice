"""add_settings_annexure_evidence_version_models

Adds four new tables for Phase 3 / Step 3 of the roadmap:

- settings       -- app-level configuration key/value store
- annexures      -- uploaded supporting documents with hash, OCR, tags
- evidence       -- general evidence model (photo, video, report, etc.)
- versions       -- version history table for snapshot-on-save

All CREATE TABLE statements use IF NOT EXISTS for idempotency.

Revision ID: add_settings_annexure_evidence_version_models
Revises: add_is_admin_to_user
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

revision = "add_settings_annexure_evidence_version_models"
down_revision = "add_is_admin_to_user"
branch_labels = None
depends_on = None


def upgrade():
    # --- settings table ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR(100) PRIMARY KEY,
            value TEXT,
            value_type VARCHAR(20) NOT NULL DEFAULT 'string',
            description TEXT,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- annexures table ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS annexures (
            id VARCHAR(36) PRIMARY KEY,
            case_id INTEGER REFERENCES case_files(id) ON DELETE SET NULL,
            adjudication_id INTEGER REFERENCES adjudications(id) ON DELETE SET NULL,
            caption VARCHAR(200) NOT NULL,
            date DATETIME,
            file_hash VARCHAR(64) NOT NULL,
            page_count INTEGER,
            ocr_text TEXT,
            tags VARCHAR(500),
            filepath VARCHAR(500) NOT NULL,
            filename VARCHAR(255) NOT NULL,
            file_size INTEGER,
            mime_type VARCHAR(100),
            annexure_letter VARCHAR(1),
            uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_annexures_case_id ON annexures(case_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_annexures_adjudication_id ON annexures(adjudication_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_annexures_file_hash ON annexures(file_hash)")

    # --- evidence table ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS evidence (
            id VARCHAR(36) PRIMARY KEY,
            case_id INTEGER REFERENCES case_files(id) ON DELETE SET NULL,
            adjudication_id INTEGER REFERENCES adjudications(id) ON DELETE SET NULL,
            inspection_id INTEGER REFERENCES inspection(id) ON DELETE SET NULL,
            evidence_type VARCHAR(20) NOT NULL,
            filepath VARCHAR NOT NULL,
            filename VARCHAR(255) NOT NULL,
            file_size INTEGER,
            mime_type VARCHAR(100),
            file_hash VARCHAR(64),
            raw_lat FLOAT,
            raw_lng FLOAT,
            accuracy FLOAT,
            captured_at DATETIME,
            locality VARCHAR,
            ip_region VARCHAR,
            ip_match BOOLEAN,
            distance_to_fbo_m FLOAT,
            verification_status VARCHAR DEFAULT 'PENDING',
            stamped BOOLEAN DEFAULT 0,
            caption VARCHAR(200),
            ocr_text TEXT,
            tags VARCHAR(500),
            uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_case_id ON evidence(case_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence(evidence_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_evidence_adjudication_id ON evidence(adjudication_id)")

    # --- versions table ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER REFERENCES case_files(id) ON DELETE SET NULL,
            adjudication_id INTEGER REFERENCES adjudications(id) ON DELETE SET NULL,
            doc_type VARCHAR(20) NOT NULL,
            version_number INTEGER NOT NULL,
            html_snapshot TEXT NOT NULL,
            delta TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES user(id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_version_case_doc ON versions(case_id, doc_type, version_number)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_version_adjudication_doc ON versions(adjudication_id, doc_type, version_number)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_version_case_id ON versions(case_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_version_adjudication_id ON versions(adjudication_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_version_adjudication_id")
    op.execute("DROP INDEX IF EXISTS idx_version_case_id")
    op.execute("DROP INDEX IF EXISTS uq_version_adjudication_doc")
    op.execute("DROP INDEX IF EXISTS uq_version_case_doc")
    op.execute("DROP TABLE IF EXISTS versions")

    op.execute("DROP INDEX IF EXISTS idx_evidence_adjudication_id")
    op.execute("DROP INDEX IF EXISTS idx_evidence_type")
    op.execute("DROP INDEX IF EXISTS idx_evidence_case_id")
    op.execute("DROP TABLE IF EXISTS evidence")

    op.execute("DROP INDEX IF EXISTS idx_annexures_file_hash")
    op.execute("DROP INDEX IF EXISTS idx_annexures_adjudication_id")
    op.execute("DROP INDEX IF EXISTS idx_annexures_case_id")
    op.execute("DROP TABLE IF EXISTS annexures")

    op.execute("DROP TABLE IF EXISTS settings")
