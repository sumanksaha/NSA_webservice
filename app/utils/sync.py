import csv
import io
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import gspread
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db

logger = logging.getLogger(__name__)


def get_gspread_client():
    """Authenticate and get a gspread client.

    Priority order:
    1. GOOGLE_CREDENTIALS_JSON environment variable (raw JSON string)
    2. GOOGLE_SHEETS_CREDENTIALS_JSON (legacy alias)
    3. instance/credentials.json or credentials.json (local dev convenience)
    4. Default gspread service-account discovery (ADC)
    """
    # 1. Environment Variable (primary name)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON")
    if creds_json:
        try:
            creds_data = json.loads(creds_json)
            return gspread.service_account_from_dict(creds_data)
        except json.JSONDecodeError:
            pass
        except Exception:
            pass

    # 2. Local Files (development convenience)
    for path in ["instance/credentials.json", "credentials.json"]:
        if Path(path).exists():
            try:
                return gspread.service_account(filename=path)
            except Exception:
                pass

    # 3. Default System Credentials (ADC)
    try:
        return gspread.service_account()
    except Exception:
        return None


def sync_to_sheets():
    """Synchronizes newly created unsynced database records to Google Sheets.
    Finds records where synced_at is null, appends them to the spreadsheet tabs,
    and updates synced_at to the current timestamp.
    """
    client = get_gspread_client()
    if not client:
        return False

    from flask import current_app

    from app.models import Adjudication, Bill, CaseFile

    # Get Spreadsheet ID
    spreadsheet_id = current_app.config.get("SPREADSHEET_ID") or os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        return False

    try:
        sh = client.open_by_key(spreadsheet_id)
    except Exception:
        return False

    sync_configs = [(CaseFile, "case_files"), (Adjudication, "adjudications"), (Bill, "bills")]

    success = True
    for model, tab_name in sync_configs:
        try:
            # Query all records where synced_at is None
            unsynced_records = model.query.filter(model.synced_at.is_(None)).all()
            if not unsynced_records:
                continue

            # Open or create the worksheet
            try:
                worksheet = sh.worksheet(tab_name)
            except gspread.exceptions.WorksheetNotFound:
                # Get columns for header (exclude synced_at)
                headers = [col.name for col in model.__table__.columns if col.name != "synced_at"]
                worksheet = sh.add_worksheet(title=tab_name, rows="100", cols=str(len(headers)))
                worksheet.append_row(headers)

            # Align rows with database columns dynamically
            headers = [col.name for col in model.__table__.columns if col.name != "synced_at"]

            now = datetime.now(UTC)
            for record in unsynced_records:
                row_data = []
                for col in headers:
                    val = getattr(record, col)
                    if isinstance(val, datetime):
                        val = val.isoformat()
                    elif val is None:
                        val = ""
                    else:
                        val = str(val)
                    row_data.append(val)

                worksheet.append_row(row_data)
                record.synced_at = now

            try:
                db.session.commit()
            except StaleDataError:
                db.session.rollback()
                success = False
                continue

        except Exception:
            db.session.rollback()
            success = False

    return success


# ---------------------------------------------------------------------------
# Priority 7 — Restore chain: redundant-target CSV -> database
# ---------------------------------------------------------------------------
def _list_r2_csv_backups(prefix: str) -> list[str]:
    """List R2 object keys under ``nsa_backups/<prefix>_csv/``, newest last.

    Falls back to scanning ``instance/backups/<prefix>_csv/`` locally if R2
    is unavailable.
    """
    from flask import current_app

    keys: list[str] = []
    try:
        from app.utils.storage import _get_bucket
        from app.utils.storage import _get_client as _get_r2_client

        r2 = _get_r2_client()
        prefix_path = f"nsa_backups/{prefix}_csv/"
        kwargs = {"Bucket": _get_bucket(), "Prefix": prefix_path}
        paginator = r2.get_paginator("list_objects_v2")
        for page in paginator.paginate(**kwargs):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        keys.sort()
    except Exception:
        from pathlib import Path

        local_dir = Path(current_app.instance_path) / "backups" / f"{prefix}_csv"
        if local_dir.exists():
            keys = sorted(str(p) for p in local_dir.glob("*.csv"))
    return keys


def _download_r2_csv(key: str) -> str | None:
    """Download a CSV file from R2 (or local fallback) and return its content."""

    try:
        from app.utils.storage import _get_bucket
        from app.utils.storage import _get_client as _get_r2_client

        r2 = _get_r2_client()
        resp = r2.get_object(Bucket=_get_bucket(), Key=key)
        return resp["Body"].read().decode("utf-8")
    except Exception:
        from pathlib import Path

        p = Path(key)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None


def _csv_to_records(csv_content: str) -> list[dict]:
    """Parse CSV text into a list of dict rows (string values, no type coercion)."""
    reader = csv.DictReader(io.StringIO(csv_content))
    return [dict(row) for row in reader]


def _parse_csv_value(value: str, field_type: str = "str"):
    """Parse a CSV string value based on the target column's SQLAlchemy type.

    Handles empty strings (returns ``None``), integers, floats, booleans,
    and dates/datetimes (returns naive ``datetime``).
    """
    if value is None or value == "" or value == "None":
        return None
    if field_type in ("Integer", "BigInteger"):
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    if field_type in ("Float", "Numeric", "DECIMAL"):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    if field_type == "Boolean":
        return value.lower() in ("true", "1", "yes")
    if field_type in ("Date", "DateTime", "TIMESTAMP"):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except (ValueError, TypeError):
            return None
    return value


def _is_empty_sqlite_db() -> bool:
    """Return True if all mapped tables in the SQLite DB have zero rows."""

    from app.extensions import db

    for table_name in db.metadata.tables:
        try:
            # table_name comes from db.metadata.tables — the fixed set of
            # SQLAlchemy-registered tables, never user input.
            count = (
                db.session.execute(
                    db.text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
                ).scalar()
                or 0
            )
            if count > 0:
                return False
        except Exception:
            continue
    return True


def _restore_from_records(records, source):
    """Dispatch records to model-specific restore handlers.

    Returns the count of records processed.
    """
    by_module = {}
    for r in records:
        module = r.pop("module", "unknown")
        r.pop("base_id", None)
        r.pop("id", None)
        by_module.setdefault(module, []).append(r)

    total = 0
    for module, rows in by_module.items():
        try:
            count = _restore_module(module, rows)
            total += count
        except Exception as e:
            logger.warning("Restore failed for module %s (%s): %s", module, source, e)
    return total


def _restore_module(module, rows):
    """Restore rows for a single module to its corresponding DB model."""
    from app.extensions import db
    from app.models import (
        Adjudication,
        Bill,
        CaseFile,
        DoIntimation,
        Inspection,
        Sample,
    )

    model_map = {
        "non_sample": Adjudication,
        "sample": CaseFile,
        "billing": Bill,
        "sample_repo": Sample,
        "inspection_log": Inspection,
        "food_cell_do_intimations": DoIntimation,
    }

    model = model_map.get(module)
    if model is None:
        return 0

    col_map = _build_column_map(model, module)
    model_cols = {c.name for c in model.__table__.columns}

    if not rows:
        return 0

    count = 0
    for row in rows:
        kwargs = {}
        for csv_key, csv_val in row.items():
            field_name = col_map.get(csv_key, csv_key)
            if field_name not in model_cols:
                continue
            col = model.__table__.columns[field_name]
            col_type_str = str(col.type).split("[")[0].split("(")[0]
            kwargs[field_name] = _parse_csv_value(csv_val, col_type_str)
        if not kwargs:
            continue
        try:
            instance = model(**kwargs)
            db.session.add(instance)
            count += 1
        except Exception:
            continue

    db.session.commit()
    return count


def _build_column_map(model, module):
    """Map CSV column names to model field names (identity by default)."""
    return {}


# Canonical module key -> worksheet/table name. ONE copy — the Sheets /
# Airtable / Excel CSV mirrors cover the same six synced modules, so every
# restore target reads this single table. Adding a synced module is a
# one-line edit here, not three.
_RESTORE_MODULE_MAP = {
    "non_sample": "adjudications",
    "sample": "case_files",
    "billing": "bills",
    "sample_repo": "samples",
    "inspection_log": "inspections",
    "food_cell_do_intimations": "do_intimations",
}

# Backward-compatible aliases — the old triplicated maps were byte-identical.
_AIRTABLE_TABLE_MAP = _WORKSHEET_MAP = _SHEETS_RESTORE_MAP = _RESTORE_MODULE_MAP


def restore_from_target_csv(target: str) -> int:
    """Restore records from the latest CSV backup for *target* in R2.

    *target* is the R2 prefix / dispatch name: ``"airtable"``, ``"excel"``,
    or ``"sheets"``. This is the single implementation behind the three
    historical per-target wrappers (which differed only by prefix and an
    identical dispatch map).

    Returns the number of records imported.
    """
    keys = _list_r2_csv_backups(target)
    if not keys:
        logger.warning("No %s CSV backups found in R2", target.capitalize())
        return 0

    csv_content = _download_r2_csv(keys[-1])
    if not csv_content:
        logger.warning("Could not download %s CSV backup: %s", target.capitalize(), keys[-1])
        return 0

    records = _csv_to_records(csv_content)
    data_records = [r for r in records if r.get("module") in _RESTORE_MODULE_MAP]

    count = _restore_from_records(data_records, target)
    logger.info("Restored %d records from %s CSV backup", count, target.capitalize())
    return count


def restore_from_airtable_csv():
    """Restore records from the latest Airtable CSV backup in R2."""
    return restore_from_target_csv("airtable")


def restore_from_excel_csv():
    """Restore records from the latest Excel CSV backup in R2."""
    return restore_from_target_csv("excel")


def restore_from_sheets_csv():
    """Restore records from the latest Google Sheets CSV backup in R2."""
    return restore_from_target_csv("sheets")


def restore_if_empty() -> dict:
    """Check if the SQLite DB is empty and restore from backup if so.

    Tries the restore chain in order: Airtable -> Excel -> Sheets.
    Stops immediately if the DB is not empty.

    Returns a dict with per-target restore results::
        {"restored": True/False, "source": "airtable"/"excel"/"sheets"/None, "count": int}
    """
    if not _is_empty_sqlite_db():
        logger.info("Database is not empty - no restore needed")
        return {"restored": False, "source": None, "count": 0}

    logger.info("Database is empty - attempting restore from backup chain")

    # Priority chain: Airtable -> Excel -> Sheets (declared once, in order).
    for source, restore in (
        ("airtable", restore_from_airtable_csv),
        ("excel", restore_from_excel_csv),
        ("sheets", restore_from_sheets_csv),
    ):
        count = restore()
        if count > 0:
            logger.info("Restored %d records from %s", count, source.capitalize())
            return {"restored": True, "source": source, "count": count}

    logger.warning("All restore sources exhausted - no data to restore")
    return {"restored": False, "source": None, "count": 0}


def auto_restore_if_empty() -> dict:
    """Startup helper: replenish an empty database from R2 backups.

    Restore order:
      1. The newest full-archive ZIP in R2 (complete fidelity — every table
         plus instance files), via
         ``backup_coordinator.restore_latest_full_archive_from_r2()``.
      2. The Airtable → Excel → Sheets CSV chain (:func:`restore_if_empty`),
         which covers the six synced business modules only.

    Returns a summary dict, e.g.::

        {"restored": True, "source": "full_archive", "key": "..."}
        {"restored": True, "source": "airtable", "count": 123}
        {"restored": False, "reason": "not-empty"}
    """
    if not _is_empty_sqlite_db():
        logger.info("Auto-restore skipped: database is not empty")
        return {"restored": False, "reason": "not-empty"}

    # 1) Full archive — complete fidelity.
    try:
        from app.services.backup_coordinator import restore_latest_full_archive_from_r2

        result = restore_latest_full_archive_from_r2()
        if result is not None:
            logger.info("Auto-restored empty database from full archive %s", result.get("key"))
            return {"restored": True, "source": "full_archive", "key": result["key"]}
    except Exception as e:
        logger.warning("Full-archive restore unavailable (%s); trying CSV chain", e)

    # 2) CSV chain fallback (six synced modules only).
    csv_result = restore_if_empty()
    if csv_result.get("restored"):
        return {"restored": True, "source": csv_result["source"], "count": csv_result["count"]}

    logger.warning("Auto-restore found no usable backups")
    return {"restored": False, "reason": "no-backups-found"}


def trigger_backup() -> dict:
    """Trigger a full redundant backup via the backup coordinator.

    Delegates to ``app.services.backup_coordinator.run_backup()`` so the
    QStash webhook, admin route, and standalone script all share one entry
    point.
    """
    from app.services.backup_coordinator import run_backup

    results = run_backup()
    logger.info(
        "Backup triggered: sheets=%s airtable=%s excel=%s",
        results.get("sheets"),
        results.get("airtable"),
        results.get("excel"),
    )
    return results
