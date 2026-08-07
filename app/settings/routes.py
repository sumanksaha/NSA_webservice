"""Settings routes module.

Provides administrative routes including FSO sync, and the Phase 3
local-database backup / restore endpoints.
"""

from datetime import UTC, datetime

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import login_required
from sqlalchemy import text

# Import the blueprint from __init__.py
from app.extensions import db
from app.settings import settings_bp
from app.utils.auth import admin_required
from app.utils.backup import (
    MAX_ARCHIVE_SIZE,
    build_backup_archive,
    db_dialect,
    restore_from_archive,
)
from app.utils.fso_data import get_all_fso_names, sync_fso_from_markdown


@settings_bp.route("/")
def index():
    """Settings dashboard."""
    fso_names = get_all_fso_names()
    return render_template("settings/index.html", fso_names=fso_names)


@settings_bp.route("/sync-fso", methods=["POST"])
def sync_fso():
    """Manual FSO sync trigger."""
    result = sync_fso_from_markdown()

    # Return result as JSON
    return jsonify({"status": "success" if not result.get("errors") else "partial", "result": result})


# ---------------------------------------------------------------------------
# Phase 3 — local database backup & restore (admin only)
# ---------------------------------------------------------------------------


def _table_row_counts() -> dict:
    """Return ``{table_name: row_count}`` for every mapped table."""
    counts = {}
    for table in db.metadata.sorted_tables:
        if table.name == "search_index":
            continue
        try:
            # Table names come from SQLAlchemy metadata, not user input.
            counts[table.name] = (
                db.session.execute(text(f"SELECT COUNT(*) FROM {table.name}")).scalar() or 0  # noqa: S608
            )
        except Exception:
            counts[table.name] = 0
    return counts


@settings_bp.route("/backup", methods=["GET"])
@login_required
@admin_required
def backup():
    """Backup & restore dashboard (admin only)."""
    counts = _table_row_counts()
    return render_template(
        "settings/backup.html",
        dialect=db_dialect(),
        table_counts=counts,
        total_tables=len(counts),
        total_rows=sum(counts.values()),
    )


@settings_bp.route("/backup/download", methods=["GET"])
@login_required
@admin_required
def backup_download():
    """Download a full backup ZIP (database dump + instance files)."""
    archive = build_backup_archive()
    filename = f"nsa_backup_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(
        archive,
        as_attachment=True,
        download_name=filename,
        mimetype="application/zip",
    )


# ---------------------------------------------------------------------------
# Priority 7 — Multi-Target Sheets Redundancy backup (admin only)
# ---------------------------------------------------------------------------


@settings_bp.route("/backup-redundant-to-r2", methods=["POST"])
@login_required
@admin_required
def backup_redundant_to_r2():
    """Trigger a redundant backup of all sync targets to R2 (admin only).

    Calls ``export_sheets_to_r2()``, ``export_airtable_all_bases_to_r2()``, and
    ``export_excel_to_r2()`` via the backup coordinator, then returns a JSON
    summary of which targets succeeded.
    """
    from app.services.backup_coordinator import run_backup

    results = run_backup()
    return jsonify(results)


@settings_bp.route("/backup-redundant-to-r2", methods=["GET"])
@login_required
@admin_required
def backup_redundant_to_r2_status():
    """Return the status/help text for the redundant backup endpoint."""
    return jsonify(
        {
            "description": "POST to trigger backup of Sheets, Airtable, and Excel data to R2.",
            "endpoint": "/settings/backup-redundant-to-r2",
            "method": "POST",
            "auth": "admin",
            "targets": ["sheets", "airtable", "excel"],
    }
    )


@settings_bp.route("/backup/restore", methods=["POST"])
@login_required
@admin_required
def backup_restore():
    """Restore the database + instance files from an uploaded backup ZIP."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Please choose a backup ZIP file to restore.", "error")
        return redirect(url_for("settings.backup"))

    # Bound the upload before it is read into memory.
    upload_data = upload.read(MAX_ARCHIVE_SIZE + 1)
    if len(upload_data) > MAX_ARCHIVE_SIZE:
        flash("Backup archive exceeds the size limit.", "error")
        return redirect(url_for("settings.backup"))

    try:
        stats = restore_from_archive(upload_data)
    except ValueError as exc:
        flash(f"Restore failed: {exc}", "error")
        return redirect(url_for("settings.backup"))

    flash(
        f"Restore complete — {stats['rows']} rows across {stats['tables']} tables, "
        f"{stats['files_restored']} files restored, search reindexed "
        f"({stats['reindexed']} records).",
        "success",
    )
    return redirect(url_for("settings.backup"))
