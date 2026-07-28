"""Create missing base tables for Adjudication, PhotoEvidence, AuditLog, Bill, CaseFile

These tables already exist in production (created by db.create_all()) but have
no Alembic migration. This migration creates them so a fresh DB can be built
from migrations alone. Uses CREATE TABLE IF NOT EXISTS to be safe on existing DBs.

Revision ID: add_missing_base_tables
Revises: 7e5a0f6c9561
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa


revision = "add_missing_base_tables"
down_revision = "7e5a0f6c9561"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ── case_files ──────────────────────────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS case_files (
            id              INTEGER NOT NULL PRIMARY KEY,
            case_number     VARCHAR(100) NOT NULL,
            food_safety_officer_name VARCHAR(100) NOT NULL,
            authorization_date      DATETIME NOT NULL,
            inspection_date         DATETIME NOT NULL,
            inspection_time         VARCHAR(100) NOT NULL,
            sample_id       INTEGER,
            manufacturer_fssai      VARCHAR(50) NOT NULL,
            manufacturer_name       VARCHAR(200) NOT NULL,
            manufacturer_fbo_name   VARCHAR(200) NOT NULL,
            manufacturer_address    TEXT NOT NULL,
            retailer_fssai  VARCHAR(50) NOT NULL,
            retailer_name   VARCHAR(200) NOT NULL,
            retailer_fbo_name       VARCHAR(200) NOT NULL,
            retailer_address        TEXT NOT NULL,
            product_name    VARCHAR(200) NOT NULL,
            batch_no        VARCHAR(100) NOT NULL,
            sample_quantity VARCHAR(100) NOT NULL,
            packet_count    INTEGER NOT NULL,
            mfg_date        DATETIME NOT NULL,
            expiry_date     DATETIME NOT NULL,
            other_food_articles     VARCHAR(500),
            total_cost      VARCHAR(50),
            cost_in_words   VARCHAR(200),
            sample_code     VARCHAR(100) NOT NULL,
            sample_submission_date  DATETIME NOT NULL,
            Lab_Registration_No     VARCHAR(100) NOT NULL,
            do_receipt_date         DATETIME NOT NULL,
            is_misbranded   BOOLEAN DEFAULT 0,
            is_substandard  BOOLEAN DEFAULT 0,
            analyst_report_no       VARCHAR(100) NOT NULL,
            analyst_report_date     DATETIME NOT NULL,
            directive_letter_no     VARCHAR(100) NOT NULL,
            directive_letter_date   DATETIME NOT NULL,
            retailer_report_receive_date    DATETIME NOT NULL,
            manufacturer_report_receive_date DATETIME NOT NULL,
            applicable_regulation   VARCHAR(200),
            applicable_clause       VARCHAR(200),
            sample_name     VARCHAR(200),
            applicable_sections     VARCHAR(50),
            created_at      DATETIME,
            synced_at       DATETIME
        )
    """)
    )

    # ── adjudications ───────────────────────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS adjudications (
            id                  INTEGER NOT NULL PRIMARY KEY,
            case_number         VARCHAR(100) NOT NULL,
            food_safety_officer VARCHAR(100) NOT NULL,
            non_license         VARCHAR(10) DEFAULT 'no',
            pre_authorization   VARCHAR(10) DEFAULT 'no',
            complaint_lodged    VARCHAR(10) DEFAULT 'no',
            ce_license_no       VARCHAR(100),
            ce_trade_name       VARCHAR(200),
            ce_proprietor       VARCHAR(200),
            ce_address          TEXT,
            ce_status           VARCHAR(100),
            fbo_owner           VARCHAR(200) NOT NULL,
            fbo_name            VARCHAR(200) NOT NULL,
            fbo_address         TEXT NOT NULL,
            fssai_license       VARCHAR(100) NOT NULL,
            concerned_food      VARCHAR(200),
            problem             TEXT,
            First_inspection_date       DATETIME NOT NULL,
            compliance_deadline         DATETIME NOT NULL,
            Complaint_date              DATETIME,
            inspection_date             DATETIME NOT NULL,
            authorization_date          DATETIME,
            clean_premise       VARCHAR(10) DEFAULT 'yes',
            refrigerator_clean  VARCHAR(10) DEFAULT 'yes',
            proper_attire       VARCHAR(10) DEFAULT 'yes',
            proper_covered_utensil      VARCHAR(10) DEFAULT 'yes',
            date_tag            VARCHAR(10) DEFAULT 'yes',
            veg_nonveg_separation       VARCHAR(10) DEFAULT 'yes',
            food_segregation    VARCHAR(10) DEFAULT 'yes',
            license_display     VARCHAR(10) DEFAULT 'yes',
            artificial_colour   VARCHAR(10) DEFAULT 'no',
            Expired_item        VARCHAR(10) DEFAULT 'no',
            Pest_report         VARCHAR(10) DEFAULT 'yes',
            Water_report        VARCHAR(10) DEFAULT 'yes',
            section_55          VARCHAR(10) DEFAULT 'no',
            section_56          VARCHAR(10) DEFAULT 'no',
            section_58          VARCHAR(10) DEFAULT 'no',
            section_63          VARCHAR(10) DEFAULT 'no',
            section_64          VARCHAR(10) DEFAULT 'no',
            created_at          DATETIME,
            synced_at           DATETIME
        )
    """)
    )

    # ── bills ───────────────────────────────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS bills (
            id                  INTEGER NOT NULL PRIMARY KEY,
            Name                VARCHAR(100) NOT NULL,
            EMP_ID              VARCHAR(50) NOT NULL,
            Designation         VARCHAR(100) NOT NULL DEFAULT 'Food Safety Officer',
            Enf_samp_No         INTEGER NOT NULL DEFAULT 0,
            Surv_samp_No        INTEGER NOT NULL DEFAULT 0,
            enforcement_price   NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            surveillance_price  NUMERIC(10,2) NOT NULL DEFAULT 0.00,
            Total_bill          FLOAT NOT NULL DEFAULT 0.0,
            No_of_enfbills      INTEGER NOT NULL DEFAULT 0,
            No_of_survbills     INTEGER NOT NULL DEFAULT 0,
            TR_Value            VARCHAR(100) NOT NULL,
            TR_date             DATETIME NOT NULL,
            Submission_date     DATETIME NOT NULL,
            start_date          DATETIME,
            end_date            DATETIME,
            created_at          DATETIME,
            synced_at           DATETIME
        )
    """)
    )

    # ── photo_evidence ──────────────────────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS photo_evidence (
            image_id            VARCHAR NOT NULL PRIMARY KEY,
            case_id             INTEGER,
            inspection_id       INTEGER,
            filepath            VARCHAR NOT NULL,
            raw_lat             FLOAT NOT NULL,
            raw_lng             FLOAT NOT NULL,
            accuracy            FLOAT NOT NULL,
            captured_at         DATETIME NOT NULL,
            uploaded_at         DATETIME NOT NULL,
            locality            VARCHAR,
            ip_region           VARCHAR,
            ip_match            BOOLEAN,
            distance_to_fbo_m   FLOAT,
            verification_status VARCHAR DEFAULT 'PENDING',
            stamped             BOOLEAN DEFAULT 0
        )
    """)
    )

    # ── audit_log ───────────────────────────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id              INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            entity_type     VARCHAR NOT NULL,
            entity_id       VARCHAR NOT NULL,
            action          VARCHAR NOT NULL,
            actor           VARCHAR NOT NULL,
            timestamp       DATETIME NOT NULL,
            prev_hash       VARCHAR,
            curr_hash       VARCHAR,
            details_json    TEXT
        )
    """)
    )


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS audit_log"))
    conn.execute(sa.text("DROP TABLE IF EXISTS photo_evidence"))
    conn.execute(sa.text("DROP TABLE IF EXISTS bills"))
    conn.execute(sa.text("DROP TABLE IF EXISTS adjudications"))
    conn.execute(sa.text("DROP TABLE IF EXISTS case_files"))
