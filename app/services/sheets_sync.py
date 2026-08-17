import csv
import io
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import gspread
from flask import current_app

# Module-level config dicts
WORKSHEET_MAP = {
    "non_sample": "NonSample_Adjudication",
    "sample": "Sample_CaseFile",
    "billing": "Billing",
    "sample_repo": "Sample_Repository",  # Step 5: Sample table sync
    "inspection_log": "Inspection_Log",  # Step 5: Inspection table sync
    "food_cell_do_intimations": "FoodCellDOIntimations",  # Phase 21: Food Cell DO Intimation sync
}

SHEET_COLUMNS = {
    "non_sample": [
        "case_number",
        "food_safety_officer",
        "non_license",
        "pre_authorization",
        "complaint_lodged",
        "ce_license_no",
        "ce_trade_name",
        "ce_proprietor",
        "ce_address",
        "ce_status",
        "fbo_owner",
        "fbo_name",
        "fbo_address",
        "fssai_license",
        "concerned_food",
        "problem",
        "First_inspection_date",
        "compliance_deadline",
        "Complaint_date",
        "inspection_date",
        "authorization_date",
        "clean_premise",
        "refrigerator_clean",
        "proper_attire",
        "proper_covered_utensil",
        "date_tag",
        "veg_nonveg_separation",
        "food_segregation",
        "license_display",
        "artificial_colour",
        "Expired_item",
        "Pest_report",
        "Water_report",
        "section_55",
        "section_56",
        "section_58",
        "section_63",
        "section_64",
        "created_at",
    ],
    "sample": [
        "case_number",
        "food_safety_officer_name",
        "authorization_date",
        "inspection_date",
        "inspection_time",
        "manufacturer_fssai",
        "manufacturer_name",
        "manufacturer_fbo_name",
        "manufacturer_address",
        "retailer_fssai",
        "retailer_name",
        "retailer_fbo_name",
        "retailer_address",
        "product_name",
        "batch_no",
        "sample_quantity",
        "packet_count",
        "mfg_date",
        "expiry_date",
        "other_food_articles",
        "total_cost",
        "cost_in_words",
        "sample_code",
        "sample_submission_date",
        "Lab_Registration_No",
        "do_receipt_date",
        "is_misbranded",
        "is_substandard",
        "analyst_report_no",
        "analyst_report_date",
        "directive_letter_no",
        "directive_letter_date",
        "retailer_report_receive_date",
        "manufacturer_report_receive_date",
        "applicable_regulation",
        "applicable_clause",
        "sample_name",
        "sample_id",
        "created_at",
    ],
    "billing": [
        "Name",
        "EMP_ID",
        "Designation",
        "Enf_samp_No",
        "Surv_samp_No",
        "Total_bill",
        "No_of_enfbills",
        "No_of_survbills",
        "TR_Value",
        "TR_date",
        "Submission_date",
        "created_at",
    ],
    # Step 5: Sample Repository worksheet columns
    "sample_repo": [
        "id",
        "sample_code",
        "sample_name",
        "sample_type",
        "fso_name",
        "collection_date",
        "submission_date",
        "retailer_fssai",
        "retailer_name",
        "price",
        "created_at",
        "synced_at",
    ],
    # Step 5: Inspection Log worksheet columns
    "inspection_log": [
        "id",
        "inspection_code",
        "fso_name",
        "fssai_license",
        "ce_license_no",
        "fbo_name",
        "fbo_address",
        "concerned_food",
        "problem",
        "inspection_date",
        "compliance_deadline",
        "is_dismissed",
        "dismissed_by",
                "adjudication_id",
        "created_at",
        "synced_at",
    ],
    # Phase 21: Food Cell DO Intimation worksheet columns
    "food_cell_do_intimations": [
        "sample_id",
        "sample_code",
        "sample_name",
        "fso_name",
        "retailer_name",
        "collection_date",
        "do_reference_no",
        "food_cell_forwarded",
        "status",
        "pdf_url",
    ],
}

# Thread-local storage for gspread client/worksheet instances
_thread_local = threading.local()


def _get_client():
    """Get a gspread client using service-account authentication.

    Priority order:
    1. GOOGLE_CREDENTIALS_JSON environment variable (raw JSON string)
    2. instance/credentials.json file (local dev convenience)
    3. GOOGLE_APPLICATION_CREDENTIALS environment variable (legacy / platform)
    4. Default gspread service-account discovery (ADC)
    """
    cached = getattr(_thread_local, "client", None)
    if cached is not None:
        return cached

    # 1. Environment variable: GOOGLE_CREDENTIALS_JSON (raw JSON string)
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_json:
        try:
            import json

            creds_data = json.loads(creds_json)
            client = gspread.service_account_from_dict(creds_data)
            _thread_local.client = client
            return client
        except json.JSONDecodeError as e:
            current_app.logger.error(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {e}")
        except Exception as e:
            current_app.logger.error(f"Failed to authenticate with GOOGLE_CREDENTIALS_JSON: {e}")

    # 2. Local file: instance/credentials.json (development convenience)
    creds_path = Path(current_app.instance_path) / "credentials.json"
    if creds_path.exists():
        try:
            client = gspread.service_account(filename=creds_path)
            _thread_local.client = client
            return client
        except Exception as e:
            current_app.logger.error(f"Failed to load credentials from {creds_path}: {e}")

    # 3. Default service account discovery (ADC / well-known paths)
    try:
        client = gspread.service_account()
        _thread_local.client = client
        return client
    except Exception as e:
        current_app.logger.error(f"Failed to authenticate with gspread service account: {e}")
        return None


def _get_worksheet(module):
    """Get a worksheet for the specified module, cached in thread-local storage."""
    cache = getattr(_thread_local, "worksheets", {})
    if module in cache:
        return cache[module]

    spreadsheet_id = current_app.config.get("GSHEETS_SPREADSHEET_ID") or current_app.config.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("GSHEETS_SPREADSHEET_ID or SPREADSHEET_ID is not configured")
    client = _get_client()
    if client is None:
        raise RuntimeError("gspread client unavailable")
    sh = client.open_by_key(spreadsheet_id)
    ws = sh.worksheet(WORKSHEET_MAP[module])
    cache[module] = ws
    _thread_local.worksheets = cache
    return ws


def _escape_formula(value):
    """Prefix dangerous leading characters to prevent Google Sheets formula injection."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def sync_to_sheets(module: str, row_dict: dict) -> bool:
    """Sync a row of data to the appropriate Google Sheet.

    Args:
        module: One of 'non_sample', 'sample', or 'billing'
        row_dict: Dictionary containing field names and values

    Returns:
        bool: True if sync succeeded, False otherwise

    """
    try:
        cols = SHEET_COLUMNS[module]
        row = [_escape_formula(row_dict.get(c, "")) for c in cols]
        ws = _get_worksheet(module)
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        current_app.logger.error(f"Sheets sync failed [{module}]: {e}")
        return False


# ---------------------------------------------------------------------------
# CSV export for R2 backup (restore chain)
# ---------------------------------------------------------------------------
def export_sheets_to_r2() -> str | None:
    """Export all Google Sheets worksheet data to a combined CSV in R2.

    Downloads all records from every worksheet (via ``WORKSHEET_MAP``) and
    uploads to ``nsa_backups/sheets_csv/`` in R2.

    Returns the R2 key on success, or a local filepath on fallback.
    Returns ``None`` if Sheets is not configured.
    """
    client = _get_client()
    if client is None:
        return None

    spreadsheet_id = (
        current_app.config.get("GSHEETS_SPREADSHEET_ID")
        or current_app.config.get("SPREADSHEET_ID")
    )
    if not spreadsheet_id:
        return None

    rows: list[dict] = []

    try:
        sh = client.open_by_key(spreadsheet_id)
    except Exception as e:
        current_app.logger.error(f"Sheets export: failed to open spreadsheet: {e}")
        return None

    for module, ws_name in WORKSHEET_MAP.items():
        try:
            ws = sh.worksheet(ws_name)
            records = ws.get_all_records()
            for record in records:
                row = {"module": module}
                row.update(record)
                rows.append(row)
        except Exception as e:
            current_app.logger.warning(f"Sheets export failed for {module}: {e}")

    if not rows:
        return None

    # Build CSV in memory
    all_keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    csv_content = buf.getvalue()

    # Upload to R2
    try:
        from app.utils.storage import _get_bucket
        from app.utils.storage import _get_client as _get_r2_client

        r2 = _get_r2_client()
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        key = f"nsa_backups/sheets_csv/sheets_export_{ts}.csv"
        r2.put_object(Bucket=_get_bucket(), Key=key, Body=csv_content)
        current_app.logger.info("Exported Sheets data to R2: %s", key)
        return key
    except Exception as e:
        current_app.logger.error(f"Failed to upload Sheets CSV to R2: {e}")

    # Fallback: save locally
    local_dir = Path(current_app.instance_path) / "backups" / "sheets_csv"
    local_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    local_file = local_dir / f"sheets_export_{ts}.csv"
    local_file.write_text(csv_content, encoding="utf-8")
    current_app.logger.info("Saved Sheets CSV backup locally: %s", local_file)
    return str(local_file)
