"""Local-database backup and restore utilities (Phase 3).

Produces a single ZIP archive that captures both the SQL database
(SQLite or PostgreSQL) as a JSON table dump and the instance-folder
files (``annexures/``, ``saved/``, ``editor_images/``), and restores
them back from that archive.

The JSON dump walks ``db.metadata.sorted_tables`` in topological order,
which keeps foreign keys satisfied when re-inserting. The SQLite FTS5
virtual table (``search_index``) is deliberately skipped — the search
index is rebuilt from the restored records via ``index_all()``.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from flask import current_app
from sqlalchemy import text
from sqlalchemy.types import BigInteger, Integer

from app.extensions import db

logger = logging.getLogger(__name__)

BACKUP_VERSION = 1
METADATA_NAME = "metadata.json"
DATABASE_NAME = "database.json"
FILES_PREFIX = "files/"

# Instance subfolders bundled into the archive (relative to instance_path).
_FOLDER_NAMES = ("annexures", "saved", "editor_images")
# Tables excluded from the dump/restore. ``search_index`` is a SQLite FTS5
# virtual table (rebuilt via index_all() after restore) and ``app_secrets``
# holds live deployment credentials (SECRET_KEY, API keys) that are
# auto-provisioned at boot — never overwrite them from a backup.
_SKIP_TABLES = {"search_index", "app_secrets"}
# Keep the restore guard from accepting pathological zip bombs.
MAX_ARCHIVE_SIZE = 200 * 1024 * 1024  # 200 MB


def db_dialect() -> str:
    """Return the current database dialect name (e.g. ``sqlite``)."""
    try:
        return str(db.session.get_bind().dialect.name)
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Serialization (type-safe round-trip across dialects)
# ---------------------------------------------------------------------------


def _serialize(value):
    """Convert a Python value into a JSON-safe tagged representation."""
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": "bytes", "value": base64.b64encode(bytes(value)).decode("ascii")}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Anything else (dicts, lists, UUIDs, enums) — encode as JSON.
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"__type__": "json", "value": json.loads(json.dumps(value, default=str))}


def _deserialize(value):
    """Reverse :func:`_serialize` back to a Python value."""
    if isinstance(value, dict) and "__type__" in value:
        value_type = value["__type__"]
        raw = value["value"]
        if value_type == "datetime":
            return datetime.fromisoformat(raw)
        if value_type == "date":
            return date.fromisoformat(raw)
        if value_type == "decimal":
            return Decimal(raw)
        if value_type == "bytes":
            return base64.b64decode(raw)
        if value_type == "json":
            return raw
    return value


# ---------------------------------------------------------------------------
# Database dump / restore
# ---------------------------------------------------------------------------


def dump_database() -> dict:
    """Return ``{table_name: [row_dict, ...]}`` for every mapped table.

    Tables are emitted in FK-safe topological order so the dump can be
    replayed with ``sorted_tables`` on restore.
    """
    dump: dict = {}
    for table in db.metadata.sorted_tables:
        if table.name in _SKIP_TABLES:
            continue
        columns = list(table.columns.keys())
        rows = db.session.execute(db.select(table)).mappings().all()
        dump[table.name] = [{col: _serialize(row[col]) for col in columns} for row in rows]
    return dump


def _restore_database(data: dict) -> dict:
    """Truncate every mapped table and re-insert the dump (topological order).

    Returns ``{"tables": ..., "rows": ...}``. Raises on failure after a
    rollback so a bad archive can never leave the DB half-restored.
    """
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(db.engine)
    existing = set(inspector.get_table_names())
    # Only touch tables that exist on the target DB (e.g. cross-dialect dumps).
    ordered = [
        table for table in db.metadata.sorted_tables if table.name in existing and table.name not in _SKIP_TABLES
    ]

    # Guard against silently wiping tables that the backup predates.
    dump_tables = set(data.keys())
    current_tables = {table.name for table in ordered}
    missing = sorted(current_tables - dump_tables)
    if missing:
        raise ValueError("Backup predates current tables: " + ", ".join(missing) + " — restore aborted.")

    restored_rows = 0
    try:
        # Delete in reverse topological order so FK constraints hold.
        for table in reversed(ordered):
            db.session.execute(table.delete())

        for table in ordered:
            rows = [{col: _deserialize(row[col]) for col in row} for row in (data.get(table.name) or [])]
            if rows:
                db.session.execute(table.insert(), rows)
            restored_rows += len(rows)

        _fix_sequences(ordered)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {"tables": len(ordered), "rows": restored_rows}


def _fix_sequences(tables) -> None:
    """Best-effort PostgreSQL sequence reset after explicit-PK inserts."""
    if db_dialect() != "postgresql":
        return
    for table in tables:
        for column in table.primary_key.columns.values():
            if not isinstance(column.type, (Integer, BigInteger)) or not column.autoincrement:
                continue
            sequence = f"{table.name}_{column.name}_seq"
            try:
                # Table/column names come from SQLAlchemy metadata, not user input.
                db.session.execute(
                    text(
                        f"SELECT setval('{sequence}', "  # noqa: S608
                        f"GREATEST((SELECT COALESCE(MAX({column.name}), 1) "
                        f"FROM {table.name}), 1))"
                    )
                )
            except Exception:
                logger.warning("Could not reset sequence %s", sequence)


# ---------------------------------------------------------------------------
# Archive build / restore
# ---------------------------------------------------------------------------


def build_backup_archive() -> io.BytesIO:
    """Build an in-memory ZIP of the database dump plus instance files."""
    instance_path = Path(current_app.instance_path)
    db_dump = dump_database()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        metadata = {
            "version": BACKUP_VERSION,
            "dialect": db_dialect(),
            "created_at": datetime.now(UTC).isoformat(),
            "table_counts": {name: len(rows) for name, rows in db_dump.items()},
        }
        zf.writestr(METADATA_NAME, json.dumps(metadata, indent=2))
        zf.writestr(DATABASE_NAME, json.dumps(db_dump, indent=2))

        for folder in _FOLDER_NAMES:
            folder_path = instance_path / folder
            if not folder_path.is_dir():
                continue
            for file_path in sorted(folder_path.rglob("*")):
                if file_path.is_file():
                    arcname = f"{FILES_PREFIX}{file_path.relative_to(instance_path).as_posix()}"
                    zf.write(file_path, arcname=arcname)

    buffer.seek(0)
    return buffer


def restore_from_archive(archive_bytes: bytes) -> dict:
    """Validate and apply a backup archive. Returns a summary stats dict.

    Raises ``ValueError`` for invalid/untrusted archives; database errors
    propagate after a rollback.
    """
    if not archive_bytes:
        raise ValueError("Backup archive is empty.")

    instance_path = Path(current_app.instance_path).resolve()

    try:
        zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid ZIP archive.") from exc

    with zf:
        # Guard against zip bombs / pathological archives.
        if sum(item.file_size for item in zf.infolist()) > MAX_ARCHIVE_SIZE:
            raise ValueError("Backup archive exceeds the size limit.")
        # Guard against path traversal.
        for member in zf.infolist():
            if not (instance_path / member.filename).resolve().is_relative_to(instance_path):
                raise ValueError(f"Backup contains an unsafe path: {member.filename}")

        if METADATA_NAME not in zf.namelist() or DATABASE_NAME not in zf.namelist():
            raise ValueError("Not a valid NSA backup archive (missing metadata/database).")

        metadata = json.loads(zf.read(METADATA_NAME))
        if metadata.get("version") != BACKUP_VERSION:
            raise ValueError(f"Unsupported backup version: {metadata.get('version')}")

        db_dump = json.loads(zf.read(DATABASE_NAME))

        # Restore instance files first — restored rows reference them.
        file_count = 0
        for member in zf.infolist():
            if not member.filename.startswith(FILES_PREFIX):
                continue
            dest = (instance_path / member.filename[len(FILES_PREFIX) :]).resolve()
            if not dest.is_relative_to(instance_path):
                raise ValueError(f"Unsafe path in backup: {member.filename}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, open(dest, "wb") as target:
                target.write(source.read())
            file_count += 1

    stats = _restore_database(db_dump)

    # Rebuild the FTS5 search index from the restored records (SQLite only).
    reindexed = 0
    try:
        from app.search.indexer import index_all

        reindexed = index_all()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Search reindex after restore failed: %s", exc)

    return {
        **stats,
        "files_restored": file_count,
        "reindexed": reindexed,
        "dialect": db_dialect(),
    }


# ---------------------------------------------------------------------------
# Phase 16 — daily scheduled database snapshot
# ---------------------------------------------------------------------------


def create_daily_db_snapshot() -> str:
    """Write a dated full-archive ZIP under ``instance/backups/db_snapshots/``.

    Uses the same archive format as :func:`build_backup_archive` (complete
    DB dump + instance files) so snapshots are directly restorable via
    :func:`restore_from_archive`. Returns the snapshot's path.
    """
    instance_path = Path(current_app.instance_path)
    snapshot_dir = instance_path / "backups" / "db_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"nsa_db_snapshot_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.zip"
    path.write_bytes(build_backup_archive().getvalue())
    logger.info("Daily DB snapshot written: %s", path)
    return str(path)


# Celery beat handler. Registered on the standalone ``celery_app.celery``
# instance (same pattern as the other task modules); ``make_celery`` later
# reconfigures that same instance with the app's broker/backend and the
# ``daily-db-snapshot`` beat entry.
try:
    from celery_app import celery as _celery

    @_celery.task(name="app.utils.backup.create_daily_db_snapshot_task")
    def create_daily_db_snapshot_task() -> str:
        """Celery-beat wrapper around :func:`create_daily_db_snapshot`."""
        return create_daily_db_snapshot()

except ImportError:  # pragma: no cover - Celery not installed (minimal deploys)
    pass
