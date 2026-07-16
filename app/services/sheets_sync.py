import os
from flask import current_app
import gspread

# Module-level config dicts
WORKSHEET_MAP = {
    "non_sample": "NonSample_Adjudication",
    "sample": "Sample_CaseFile", 
    "billing": "Billing",
}

SHEET_COLUMNS = {
    "non_sample": [
        "case_number", "food_safety_officer", "non_license", "pre_authorization", "complaint_lodged",
        "ce_license_no", "ce_trade_name", "ce_proprietor", "ce_address", "ce_status",
        "fbo_owner", "fbo_name", "fbo_address", "fssai_license", "concerned_food", "problem",
        "First_inspection_date", "compliance_deadline", "Complaint_date", "inspection_date", "authorization_date",
        "clean_premise", "refrigerator_clean", "proper_attire", "proper_covered_utensil", "date_tag",
        "veg_nonveg_separation", "food_segregation", "license_display", "artificial_colour", "Expired_item",
        "Pest_report", "Water_report", "section_55", "section_56", "section_58", "section_63", "section_64",
        "created_at"
    ],
    "sample": [
        "case_number", "food_safety_officer_name", "authorization_date", "inspection_date", "inspection_time",
        "manufacturer_fssai", "manufacturer_name", "manufacturer_fbo_name", "manufacturer_address",
        "retailer_fssai", "retailer_name", "retailer_fbo_name", "retailer_address",
        "product_name", "batch_no", "sample_quantity", "packet_count", "mfg_date", "expiry_date",
        "other_food_articles", "total_cost", "cost_in_words", "sample_code", "sample_submission_date",
        "Lab_Registration_No", "do_receipt_date", "is_misbranded", "is_substandard",
        "analyst_report_no", "analyst_report_date", "directive_letter_no", "directive_letter_date",
        "retailer_report_receive_date", "manufacturer_report_receive_date",
        "applicable_regulation", "applicable_clause", "sample_name", "created_at"
    ],
    "billing": [
        "Name", "EMP_ID", "Designation", "Enf_samp_No", "Surv_samp_No", "Total_bill",
        "No_of_enfbills", "No_of_survbills", "TR_Value", "TR_date", "Submission_date", "created_at"
    ],
}

# Cached gspread client
_client_cache = None

def get_client():
    """
    Get a cached gspread client using service-account authentication.
    Uses credentials from instance/credentials.json or GOOGLE_APPLICATION_CREDENTIALS.
    """
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    
    # Try to get credentials from instance/credentials.json
    creds_path = os.path.join(current_app.instance_path, 'credentials.json')
    if os.path.exists(creds_path):
        try:
            _client_cache = gspread.service_account(filename=creds_path)
            return _client_cache
        except Exception as e:
            current_app.logger.error(f"Failed to load credentials from {creds_path}: {e}")
    
    # Fallback to environment variable
    try:
        import json
        creds_json = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
        if creds_json:
            creds_data = json.loads(creds_json)
            _client_cache = gspread.service_account_from_dict(creds_data)
            return _client_cache
    except Exception as e:
        current_app.logger.error(f"Failed to parse GOOGLE_APPLICATION_CREDENTIALS: {e}")
    
    # Fallback to default service account
    try:
        _client_cache = gspread.service_account()
        return _client_cache
    except Exception as e:
        current_app.logger.error(f"Failed to authenticate with gspread service account: {e}")
        return None

# Cached worksheet getter
_ws_cache = {}

def get_worksheet(module):
    """Get a cached worksheet for the specified module."""
    if module not in _ws_cache:
        spreadsheet_id = current_app.config.get("GSHEETS_SPREADSHEET_ID") or current_app.config.get("SPREADSHEET_ID")
        if not spreadsheet_id:
            raise ValueError("GSHEETS_SPREADSHEET_ID or SPREADSHEET_ID is not configured")
        sh = get_client().open_by_key(spreadsheet_id)
        _ws_cache[module] = sh.worksheet(WORKSHEET_MAP[module])
    return _ws_cache[module]


def sync_to_sheets(module: str, row_dict: dict) -> bool:
    """
    Sync a row of data to the appropriate Google Sheet.
    
    Args:
        module: One of 'non_sample', 'sample', or 'billing'
        row_dict: Dictionary containing field names and values
        
    Returns:
        bool: True if sync succeeded, False otherwise
    """
    try:
        cols = SHEET_COLUMNS[module]
        row = [row_dict.get(c, "") for c in cols]
        ws = get_worksheet(module)
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        current_app.logger.error(f"Sheets sync failed [{module}]: {e}")
        return False