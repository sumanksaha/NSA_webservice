"""unify_photo_evidence

Phase 5: unify the legacy ``PhotoEvidence`` and ``InspectionPhoto``
tables into the single ``Evidence`` model. Existing rows are backfilled
into ``evidence`` as ``evidence_type='photo'`` (idempotent — existing
ids are preserved and conflicts are skipped), then the legacy tables are
dropped. The downgrade recreates the legacy tables and copies photo rows
back (best-effort; uuid-based ids that do not fit the legacy integer
``inspection_photos.id`` are skipped).

Revision ID: unify_photo_evidence
Revises: create_search_index_fts5_table
Create Date: 2026-08-02
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "unify_photo_evidence"
down_revision = "create_search_index_fts5_table"
branch_labels = None
depends_on = None


def _dialect(bind) -> str:
    return bind.dialect.name


def _insert_prefix(dialect: str) -> str:
    """Dialect-aware idempotent insert prefix."""
    return "INSERT OR IGNORE INTO" if dialect == "sqlite" else "INSERT INTO"


def _on_conflict(dialect: str) -> str:
    return " ON CONFLICT (id) DO NOTHING" if dialect == "postgresql" else ""


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _insert_statement(prefix: str, conflict: str, columns: list) -> str:
    """Build an idempotent parameterized INSERT for the given columns."""
    placeholders = ", ".join(f":{col}" for col in columns)
    return f"{prefix} evidence ({', '.join(columns)}) VALUES ({placeholders}){conflict}"


def _migrate_photo_evidence(bind) -> int:
    """Copy ``photo_evidence`` rows into ``evidence`` as photo evidence."""
    if not _has_table(bind, "photo_evidence"):
        return 0
    rows = (
        bind
        .execute(
            sa.text(
                "SELECT image_id, case_id, inspection_id, filepath, raw_lat, raw_lng, accuracy, "
                "captured_at, uploaded_at, locality, ip_region, ip_match, distance_to_fbo_m, "
                "verification_status, stamped FROM photo_evidence"
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return 0

    dialect = _dialect(bind)
    params = []
    for row in rows:
        filepath = row["filepath"] or ""
        params.append({
            "id": row["image_id"],
            "case_id": row["case_id"],
            "adjudication_id": None,
            "inspection_id": row["inspection_id"],
            "evidence_type": "photo",
            "filepath": filepath,
            "filename": Path(filepath.replace("\\", "/")).name or filepath,
            "raw_lat": row["raw_lat"],
            "raw_lng": row["raw_lng"],
            "accuracy": row["accuracy"],
            "captured_at": row["captured_at"],
            "uploaded_at": row["uploaded_at"],
            "locality": row["locality"],
            "ip_region": row["ip_region"],
            "ip_match": row["ip_match"],
            "distance_to_fbo_m": row["distance_to_fbo_m"],
            "verification_status": row["verification_status"],
            "stamped": row["stamped"],
        })

    columns = list(params[0].keys())
    bind.execute(sa.text(_insert_statement(_insert_prefix(dialect), _on_conflict(dialect), columns)), params)
    return len(params)


def _migrate_inspection_photos(bind) -> int:
    """Copy ``inspection_photos`` rows into ``evidence`` as photo evidence."""
    if not _has_table(bind, "inspection_photos"):
        return 0
    rows = (
        bind
        .execute(sa.text("SELECT id, adjudication_id, file_url, caption, uploaded_at FROM inspection_photos"))
        .mappings()
        .all()
    )
    if not rows:
        return 0

    dialect = _dialect(bind)
    params = []
    for row in rows:
        file_url = row["file_url"] or ""
        params.append({
            "id": str(row["id"]),
            "case_id": None,
            "adjudication_id": row["adjudication_id"],
            "inspection_id": None,
            "evidence_type": "photo",
            "filepath": file_url,
            "filename": Path(file_url.replace("\\", "/")).name or file_url,
            "file_size": None,
            "mime_type": None,
            "file_hash": None,
            "raw_lat": None,
            "raw_lng": None,
            "accuracy": None,
            "captured_at": None,
            "uploaded_at": row["uploaded_at"],
            "locality": None,
            "ip_region": None,
            "ip_match": None,
            "distance_to_fbo_m": None,
            "verification_status": "PENDING",
            "stamped": False,
            "caption": row["caption"],
            "ocr_text": None,
            "tags": None,
        })

    columns = list(params[0].keys())
    bind.execute(sa.text(_insert_statement(_insert_prefix(dialect), _on_conflict(dialect), columns)), params)
    return len(params)


def upgrade():
    bind = op.get_bind()
    _migrate_photo_evidence(bind)
    _migrate_inspection_photos(bind)
    op.execute("DROP TABLE IF EXISTS photo_evidence")
    op.execute("DROP TABLE IF EXISTS inspection_photos")


def downgrade():
    bind = op.get_bind()
    dialect = _dialect(bind)
    if not _has_table(bind, "evidence"):
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS photo_evidence (
            image_id VARCHAR PRIMARY KEY,
            case_id INTEGER REFERENCES case_files(id),
            inspection_id INTEGER REFERENCES inspection(id),
            filepath VARCHAR NOT NULL,
            raw_lat FLOAT NOT NULL,
            raw_lng FLOAT NOT NULL,
            accuracy FLOAT NOT NULL,
            captured_at DATETIME NOT NULL,
            uploaded_at DATETIME NOT NULL,
            locality VARCHAR,
            ip_region VARCHAR,
            ip_match BOOLEAN,
            distance_to_fbo_m FLOAT,
            verification_status VARCHAR,
            stamped BOOLEAN
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS inspection_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adjudication_id INTEGER NOT NULL REFERENCES adjudications(id) ON DELETE CASCADE,
            file_url VARCHAR(500) NOT NULL,
            caption VARCHAR(200),
            uploaded_at DATETIME
        )
        """
    )

    # Copy evidence photo rows back (best-effort).
    rows = (
        bind
        .execute(
            sa.text(
                "SELECT id, case_id, inspection_id, filepath, raw_lat, raw_lng, accuracy, "
                "captured_at, uploaded_at, locality, ip_region, ip_match, distance_to_fbo_m, "
                "verification_status, stamped FROM evidence "
                "WHERE evidence_type = 'photo'"
            )
        )
        .mappings()
        .all()
    )
    prefix = _insert_prefix(dialect)
    conflict = _on_conflict(dialect)
    for row in rows:
        bind.execute(
            sa.text(  # prefix/conflict come from a fixed dialect set
                f"{prefix} photo_evidence (image_id, case_id, inspection_id, filepath, raw_lat, "
                "raw_lng, accuracy, captured_at, uploaded_at, locality, ip_region, ip_match, "
                "distance_to_fbo_m, verification_status, stamped) "
                "VALUES (:id, :case_id, :inspection_id, :filepath, :raw_lat, :raw_lng, :accuracy, "
                ":captured_at, :uploaded_at, :locality, :ip_region, :ip_match, :distance_to_fbo_m, "
                ":verification_status, :stamped)" + conflict
            ),
            dict(row),
        )

    # Numeric ids only for the integer-PK inspection_photos table.
    for row in rows:
        try:
            int(row["id"])
        except (TypeError, ValueError):
            continue
        bind.execute(
            sa.text(  # prefix/conflict come from a fixed dialect set
                f"{prefix} inspection_photos (id, adjudication_id, file_url, uploaded_at) "  # noqa: S608
                "VALUES (:id, (SELECT adjudication_id FROM evidence WHERE id = :id2), :filepath, :uploaded_at)"
                + conflict
            ),
            {"id": int(row["id"]), "id2": row["id"], "filepath": row["filepath"], "uploaded_at": row["uploaded_at"]},
        )
