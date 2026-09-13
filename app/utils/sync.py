import logging

from app.services.backup_restorer import (
    BACKUP_MODULE_TO_TABLE,
    BackupRestorer,
    auto_restore_if_empty,
    restore_from,
    restore_from_airtable_csv,
    restore_from_excel_csv,
    restore_from_sheets_csv,
    restore_if_empty,
)

logger = logging.getLogger(__name__)

# Backward-compatible re-exports — thin shim over the deep module.
# Internal helpers kept for test compatibility during migration:
#   _csv_to_records, _parse_csv_value, _is_empty_sqlite_db
# are re-exported from app.services.backup_restorer for now.

__all__ = [
    "BACKUP_MODULE_TO_TABLE",
    "BackupRestorer",
    "_csv_to_records",
    "_is_empty_sqlite_db",
    "_parse_csv_value",
    "auto_restore_if_empty",
    "restore_from",
    "restore_from_airtable_csv",
    "restore_from_excel_csv",
    "restore_from_sheets_csv",
    "restore_if_empty",
    "trigger_backup",
]


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


# Test-compatibility shims — delegate to BackupRestorer internals.
_csv_to_records = BackupRestorer._csv_to_records
_parse_csv_value = BackupRestorer._parse_csv_value
_is_empty_sqlite_db = BackupRestorer._is_empty_sqlite_db
