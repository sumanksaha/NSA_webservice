import os
import sqlite3
import ssl
import re
import json
import httpx
import time
from threading import Lock

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# app/utils is nested two levels deep from the workspace root
WORKSPACE_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', '..'))
DB_DIR = os.path.join(WORKSPACE_DIR, "db")
LICENSE_DB_PATH = os.path.join(DB_DIR, "license_data.db")
REGISTRATION_DB_PATH = os.path.join(DB_DIR, "registration_data.db")

# Rate limiting for KMC CE lookup (govt website - 40 second gap required)
_kmc_last_request_time = 0
_kmc_rate_limit_lock = Lock()
_KMC_RATE_LIMIT_SECONDS = 40  # Minimum gap between KMC portal requests

def lookup_fssai(license_no: str):
    """
    Look up an FSSAI License/Registration number.
    Numbers starting with '1' are Registration-category FBOs -> license_records in license_data.db.
    Numbers starting with '2' are License-category FBOs -> registration_records in registration_data.db.
    Returns a dict with companyName/fullAddress/expiryDate/source, or None if not found/error.
    """
    if not license_no:
        return None, "License/Registration number is required."

    prefix = license_no[0]
    if prefix == '1':
        db_path, table, col, source = LICENSE_DB_PATH, "license_records", "license_no", "license_data"
    elif prefix == '2':
        db_path, table, col, source = REGISTRATION_DB_PATH, "registration_records", "registration_no", "registration_data"
    else:
        return None, "Unrecognized License/Registration number prefix (expected to start with 1 or 2)."

    if not os.path.exists(db_path):
        return None, f"Lookup database not found: {os.path.basename(db_path)}."

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT company_name, full_address, expiry_date FROM {table} WHERE {col} = ?",
            (license_no,)
        ).fetchone()
    except Exception as e:
        print(f"FSSAI lookup query failed: {e}")
        return None, f"Database error: {str(e)}"
    finally:
        conn.close()

    if not row:
        return None, "License/Registration number not found."

    return {
        "companyName": row["company_name"],
        "fullAddress": row["full_address"],
        "expiryDate": row["expiry_date"],
        "source": source,
    }, None


def lookup_ce(license_no: str):
    """
    Fetches Trade License details from KMC portal.
    Implements rate limiting: minimum 40 seconds between consecutive requests.
    """
    global _kmc_last_request_time
    
    # Apply rate limiting for KMC portal (govt website)
    with _kmc_rate_limit_lock:
        current_time = time.time()
        time_since_last = current_time - _kmc_last_request_time
        if time_since_last < _KMC_RATE_LIMIT_SECONDS:
            sleep_time = _KMC_RATE_LIMIT_SECONDS - time_since_last
            time.sleep(sleep_time)
            # Update last request time after sleeping
            _kmc_last_request_time = time.time()
        else:
            _kmc_last_request_time = current_time
    
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with httpx.Client(timeout=15, verify=ctx) as client:
        client.get(
            "https://www.kmcgov.in/KMCPortal/jsp/TradeLicenseInformation.jsp"
        )
        resp = client.post(
            "https://www.kmcgov.in/KMCPortal/LicenseInformationAction.do?passedParam=searchResult",
            data={"searchLicenseNo": license_no},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.kmcgov.in/KMCPortal/jsp/TradeLicenseInformation.jsp",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )

        raw_text = resp.text
        # KMC's endpoint returns JSON with unquoted keys — fix before parsing
        fixed_text = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', raw_text)
        try:
            data = json.loads(fixed_text)
        except json.JSONDecodeError as e:
            print(f"KMC JSON repair failed: {e}")
            return None

    if not data.get("success"):
        return None

    try:
        rows = data["licenseNo"][0]
        identity = rows[0]
    except (KeyError, IndexError):
        return None

    fee_heads = [{"section": r.get("sectionCode"), "amount": r.get("demandAmount")} for r in rows]
    return {
        "identity": identity,
        "fee_heads": fee_heads,
        "is_closed": bool(identity.get("licClosingDate"))
    }
