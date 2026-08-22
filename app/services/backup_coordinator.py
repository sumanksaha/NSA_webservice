"""Backup coordinator for Multi-Target Sheets Redundancy (Priority 7).

This module is referenced by ``TASK_REGISTRY`` as
``backup_redundant_sheets`` and executed by the QStash webhook when the
daily schedule fires (or via the admin route / standalone script).

It calls the three parallel-target export functions:
  - ``sheets_sync.export_sheets_to_r2()``
  - ``airtable_sync.export_airtable_all_bases_to_r2()``
  - ``excel_sync.export_excel_to_r2()``

plus a fourth target: a full backup ZIP — complete database dump (every
mapped table, type-safe JSON) plus instance-folder files — built by
``app.utils.backup.build_backup_archive()`` and uploaded under
``nsa_backups/full_archives/``. This is the artifact that restores a
brand-new database to full fidelity (users, evidence, versions, audit
chain — everything the per-target CSV mirrors do not cover).

Each export is wrapped in try/except so a failure in one target does
not prevent the others from running.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.shared.config import cfg

logger = logging.getLogger(__name__)

#: R2 key prefix for full-archive ZIP snapshots.
ARCHIVE_PREFIX = "nsa_backups/full_archives/"


@dataclass(frozen=True)
class BackupTarget:
    """One redundancy target adapter (Sheets / Airtable / Excel / full archive).

    ``export`` resolves its backing function lazily at call time via
    ``importlib`` so tests can patch the source module attribute directly and
    production pays no import cost up front. Adding a fourth redundancy copy
    means appending one row to :data:`TARGETS` — nothing else edits.
    """

    name: str
    result_key: str
    module_name: str
    func_name: str
    warn_on_none: bool = True

    def export(self) -> str | None:
        import importlib

        module = importlib.import_module(self.module_name)
        return getattr(module, self.func_name)()


#: The target registry — Sheets (primary), Airtable + Excel (redundant CSV
#: mirrors), plus the full-archive ZIP snapshot.
TARGETS: tuple[BackupTarget, ...] = (
    BackupTarget("sheets", "sheets", "app.services.sheets_sync", "export_sheets_to_r2"),
    BackupTarget("airtable", "airtable", "app.services.airtable_sync", "export_airtable_all_bases_to_r2"),
    BackupTarget("excel", "excel", "app.services.excel_sync", "export_excel_to_r2"),
    BackupTarget(
        "full_archive",
        "full_archive",
        "app.services.backup_coordinator",
        "export_full_archive_to_r2",
        warn_on_none=False,
    ),
)


def run_backup() -> dict:
    """Export every registered redundancy target to R2.

    Each target is isolated — a failure in one never prevents the others.

    Returns per-target success flags::

        {"sheets": True, "airtable": False, "excel": True,
         "full_archive": True, "r2_keys": [...]}
    """
    results: dict = {"r2_keys": []}
    for target in TARGETS:
        try:
            key = target.export()
            results[target.result_key] = bool(key)
            if key:
                results["r2_keys"].append(key)
            elif target.warn_on_none:
                logger.warning("%s export returned None (not configured or no data)", target.name.capitalize())
        except Exception as e:
            results[target.result_key] = False
            logger.error("%s backup failed: %s", target.name.capitalize(), e)

    logger.info(
        "Redundant backup complete: %s",
        " ".join(f"{t.name}={results.get(t.result_key)}" for t in TARGETS),
    )
    return results


def export_full_archive_to_r2() -> str | None:
    """Build a full backup ZIP (database dump + instance files) and upload it to R2.

    The archive is the same artifact the admin UI downloads via
    ``GET /settings/backup/download`` — restoring it with
    ``restore_from_archive()`` brings a brand-new database back to full
    fidelity. Gated by ``BACKUP_FULL_ARCHIVE_ENABLED`` (opt-out, default on).

    Returns the uploaded object key, or ``None`` when the feature is
    disabled. Raises on R2/build errors (callers isolate failures).
    """
    if not cfg.full_archive_enabled:
        logger.info("Full-archive snapshot disabled (BACKUP_FULL_ARCHIVE_ENABLED=false)")
        return None

    from app.utils.backup import build_backup_archive
    from app.utils.storage import _get_bucket, _get_client

    r2 = _get_client()
    bucket = _get_bucket()
    archive = build_backup_archive()
    body = archive.getvalue()

    key = _archive_key()
    r2.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/zip")
    logger.info("Full archive uploaded to R2 (%s, %d bytes)", key, len(body))

    _prune_old_archives(r2, bucket)
    return key


def restore_latest_full_archive_from_r2() -> dict | None:
    """Download the newest full-archive ZIP from R2 and restore it.

    The inverse of :func:`export_full_archive_to_r2` — this is the path a
    freshly-provisioned database uses to replenish itself at full fidelity.
    Requires an active Flask app context (``restore_from_archive`` reads the
    instance path) and the R2 environment variables.

    Returns ``{"key": <object key>, **restore_stats}``, or ``None`` when no
    archive exists in the bucket. Raises on R2/restore errors.
    """
    from app.utils.backup import restore_from_archive
    from app.utils.storage import _get_bucket, _get_client

    r2 = _get_client()
    bucket = _get_bucket()

    keys: list[str] = []
    paginator = r2.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=ARCHIVE_PREFIX):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    if not keys:
        logger.info("No full archives found in R2 (%s)", ARCHIVE_PREFIX)
        return None

    key = sorted(keys)[-1]  # timestamped names sort chronologically
    body = r2.get_object(Bucket=bucket, Key=key)["Body"].read()
    stats = restore_from_archive(body)
    logger.info("Restored full archive %s: %s", key, stats)
    return {"key": key, **stats}


def _archive_key() -> str:
    """Timestamped R2 object key — lexicographic sort == chronological order."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{ARCHIVE_PREFIX}nsa_backup_{timestamp}.zip"


def _prune_old_archives(r2, bucket: str) -> int:
    """Delete full archives beyond ``BACKUP_ARCHIVE_RETENTION`` (newest N kept).

    Keys sort chronologically (timestamped names), so the last ``keep`` keys
    are the newest. Per-object delete failures are logged, not raised —
    pruning is best-effort housekeeping.

    Returns the number of objects deleted.
    """
    keep = max(1, int(cfg.archive_retention))

    keys: list[str] = []
    paginator = r2.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=ARCHIVE_PREFIX):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    keys.sort()

    stale = keys[:-keep] if len(keys) > keep else []
    for key in stale:
        try:
            r2.delete_object(Bucket=bucket, Key=key)
        except Exception as e:
            logger.warning("Archive prune failed for %s: %s", key, e)
    if stale:
        logger.info("Pruned %d old full archives (retention=%d)", len(stale), keep)
    return len(stale)
