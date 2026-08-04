import json
import os
from datetime import UTC, datetime
from pathlib import Path

import gspread
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db


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
