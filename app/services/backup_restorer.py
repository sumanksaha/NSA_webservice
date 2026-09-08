"""BackupRestorer — unified CSV restore adapter (D6 deepening).

Consolidates the triplicated restore pipelines from `app/utils/sync.py`
into one deep module with a single canonical map and one public interface.

Public surface:
    BackupRestorer.restore_from(target)  -> int   (records restored)
    BackupRestorer.restore_if_empty()    -> dict (startup helper)
    BackupRestorer.auto_restore_if_empty() -> dict (full archive + CSV chain)

Seam: R2 CSV download → parse → module dispatch → DB restore.
Adapters: none yet — one target, one code path. Two would justify a split.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import Adjudication, Bill, CaseFile, DoIntimation, Inspection, Sample

logger = logging.getLogger(__name__)

#: One canonical module→worksheet/table mapping. Previously triplicated
#: as _AIRTABLE_TABLE_MAP / _WORKSHEET_MAP / _SHEETS_RESTORE_MAP /
#: _RESTORE_MODULE_MAP (all byte-identical). Adding a synced module
#: is one line here.
BACKUP_MODULE_TO_TABLE: dict[str, str] = {
    "non_sample": "adjudications",
    "sample": "case_files",
    "billing": "bills",
    "sample_repo": "samples",
    "inspection_log": "inspections",
    "food_cell_do_intimations": "do_intimations",
}


class BackupRestorer:
    """Deep module: CSV restore pipeline behind a small interface."""

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    def restore_from(self, target: str) -> int:
        """Restore records from the latest CSV backup for *target* in R2."""
        keys = self._list_r2_csv_backups(target)
        if not keys:
            logger.warning("No %s CSV backups found in R2", target.capitalize())
            return 0

        csv_content = self._download_r2_csv(keys[-1])
        if not csv_content:
            logger.warning("Could not download %s CSV backup", target.capitalize())
            return 0

        records = self._csv_to_records(csv_content)
        data_records = [r for r in records if r.get("module") in BACKUP_MODULE_TO_TABLE]
        count = self._restore_from_records(data_records, target)
        logger.info("Restored %d records from %s CSV backup", count, target.capitalize())
        return count

    def restore_if_empty(self) -> dict[str, Any]:
        """Check if SQLite is empty; restore via CSV chain if so."""
        if self._is_empty_sqlite_db():
            logger.info("Database is empty - attempting restore from backup chain")
            for source, fn in (
                ("airtable", lambda: self.restore_from("airtable")),
                ("excel", lambda: self.restore_from("excel")),
                ("sheets", lambda: self.restore_from("sheets")),
            ):
                count = fn()
                if count > 0:
                    logger.info("Restored %d records from %s", count, source.capitalize())
                    return {"restored": True, "source": source, "count": count}
            logger.warning("All restore sources exhausted - no data to restore")
            return {"restored": False, "source": None, "count": 0}
        logger.info("Database is not empty - no restore needed")
        return {"restored": False, "source": None, "count": 0}

    def auto_restore_if_empty(self) -> dict[str, Any]:
        """Startup helper: full archive first, then CSV chain fallback."""
        if not self._is_empty_sqlite_db():
            logger.info("Auto-restore skipped: database is not empty")
            return {"restored": False, "reason": "not-empty"}

        try:
            from app.services.backup_coordinator import restore_latest_full_archive_from_r2

            result = restore_latest_full_archive_from_r2()
            if result is not None:
                logger.info("Auto-restored empty database from full archive %s", result.get("key"))
                return {"restored": True, "source": "full_archive", "key": result["key"]}
        except Exception as e:
            logger.warning("Full-archive restore unavailable (%s); trying CSV chain", e)

        csv_result = self.restore_if_empty()
        if csv_result.get("restored"):
            return {"restored": True, "source": csv_result["source"], "count": csv_result["count"]}

        logger.warning("Auto-restore found no usable backups")
        return {"restored": False, "reason": "no-backups-found"}

    # ------------------------------------------------------------------ #
    # Internal helpers (not part of the public interface)
    # ------------------------------------------------------------------ #
    def _list_r2_csv_backups(self, prefix: str) -> list[str]:
        from flask import current_app

        keys: list[str] = []
        try:
            from app.utils.storage import _get_bucket, _get_client as _get_r2_client

            r2 = _get_r2_client()
            prefix_path = f"nsa_backups/{prefix}_csv/"
            kwargs = {"Bucket": _get_bucket(), "Prefix": prefix_path}
            paginator = r2.get_paginator("list_objects_v2")
            for page in paginator.paginate(**kwargs):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            keys.sort()
        except Exception:
            local_dir = Path(current_app.instance_path) / "backups" / f"{prefix}_csv"
            if local_dir.exists():
                keys = sorted(str(p) for p in local_dir.glob("*.csv"))
        return keys

    def _download_r2_csv(self, key: str) -> str | None:
        try:
            from app.utils.storage import _get_bucket, _get_client as _get_r2_client

            r2 = _get_r2_client()
            resp = r2.get_object(Bucket=_get_bucket(), Key=key)
            return resp["Body"].read().decode("utf-8")
        except Exception:
            p = Path(key)
            if p.exists():
                return p.read_text(encoding="utf-8")
            return None

    @staticmethod
    def _csv_to_records(csv_content: str) -> list[dict]:
        reader = csv.DictReader(io.StringIO(csv_content))
        return [dict(row) for row in reader]

    @staticmethod
    def _parse_csv_value(value: str | None, field_type: str = "str") -> Any:
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

    def _restore_from_records(self, records: list[dict], source: str) -> int:
        by_module: dict[str, list[dict]] = {}
        for r in records:
            module = r.pop("module", "unknown")
            r.pop("base_id", None)
            r.pop("id", None)
            by_module.setdefault(module, []).append(r)

        total = 0
        for module, rows in by_module.items():
            try:
                count = self._restore_module(module, rows)
                total += count
            except Exception as e:
                logger.warning("Restore failed for module %s (%s): %s", module, source, e)
        return total

    def _restore_module(self, module: str, rows: list[dict]) -> int:
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

        col_map: dict[str, str] = {}
        model_cols = {c.name for c in model.__table__.columns}

        if not rows:
            return 0

        count = 0
        for row in rows:
            kwargs: dict[str, Any] = {}
            for csv_key, csv_val in row.items():
                field_name = col_map.get(csv_key, csv_key)
                if field_name not in model_cols:
                    continue
                col = model.__table__.columns[field_name]
                col_type_str = str(col.type).split("[")[0].split("(")[0]
                kwargs[field_name] = self._parse_csv_value(csv_val, col_type_str)
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

    @staticmethod
    def _is_empty_sqlite_db() -> bool:
        for table_name in db.metadata.tables:
            try:
                count = (
                    db.session.execute(db.text(f"SELECT COUNT(*) FROM {table_name}"))  # noqa: S608
                    .scalar()
                    or 0
                )
                if count > 0:
                    return False
            except Exception:
                continue
        return True


# Module-level singleton — callers use this directly.
_backup_restorer = BackupRestorer()

# Public re-exports for backward compatibility (thin shim layer).
restore_from = _backup_restorer.restore_from
restore_if_empty = _backup_restorer.restore_if_empty
auto_restore_if_empty = _backup_restorer.auto_restore_if_empty
restore_from_airtable_csv = lambda: _backup_restorer.restore_from("airtable")
restore_from_excel_csv = lambda: _backup_restorer.restore_from("excel")
restore_from_sheets_csv = lambda: _backup_restorer.restore_from("sheets")

# Pure-function re-exports kept for test compatibility.
_csv_to_records = BackupRestorer._csv_to_records
_parse_csv_value = BackupRestorer._parse_csv_value
_is_empty_sqlite_db = BackupRestorer._is_empty_sqlite_db


def trigger_backup() -> dict[str, Any]:
    """Trigger a full redundant backup via the backup coordinator."""
    from app.services.backup_coordinator import run_backup

    results = run_backup()
    logger.info(
        "Backup triggered: sheets=%s airtable=%s excel=%s",
        results.get("sheets"),
        results.get("airtable"),
        results.get("excel"),
    )
    return results