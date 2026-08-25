import json
import logging
import re
import ssl
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

from typing import Any

fcntl: Any
try:
    import fcntl
except ImportError:
    # fcntl is Unix-only; on Windows file locking is skipped (single-process
    # rate limiting still works via the timestamp file).
    fcntl = None

BASE_DIR = Path(__file__).parent.resolve()
# app/utils is nested two levels deep from the workspace root.
# Backs KMC rate-limit lock files (.kmc_lookup_lock / .kmc_last_request_time)
# used by lookup_ce(); must remain writable. The FSSAI reference data itself
# now lives in Postgres (app/models/lookup.py) — see docs/FSSAI_LOOKUP_POSTGRES_RESEARCH.md.
DB_DIR = (Path(__file__).parent.parent.parent).resolve() / "db"

# Rate limiting for KMC CE lookup (govt website - 40 second gap required)
_KMC_RATE_LIMIT_SECONDS = 40  # Minimum gap between KMC portal requests
_KMC_LOCK_PATH = DB_DIR / ".kmc_lookup_lock"
_KMC_LAST_REQUEST_TIME_PATH = DB_DIR / ".kmc_last_request_time"


def lookup_fssai(license_no: str):
    """Look up an FSSAI License/Registration number (Postgres-backed).

    Exact primary-key match on the reference tables ``fssai_licenses`` /
    ``fssai_registrations`` (:mod:`app.models.lookup`), refreshed via
    ``scripts/load_fssai_lookup.py``.

    .. warning:: Naming inversion (historical, intentional): numbers starting
       with ``'1'`` belong to *Registration-category* FBOs even though they
       resolve to the **license** table (``fssai_licenses``), and vice versa
       for ``'2'`` -> ``fssai_registrations``.  The mapping is mechanical,
       not semantic — do not swap the tables.

    Returns ``(dict | None, error | None)``: on success a dict with
    companyName/fullAddress/expiryDate/source, else ``(None, error)``.
    Requires an active Flask application context (all callers are route
    handlers).
    """
    # Local imports avoid import cycles during app factory bootstrap
    # (blueprints import this module before extensions are fully wired).
    from app.extensions import db
    from app.models.lookup import FssaiLicense, FssaiRegistration

    if not license_no:
        return None, "License/Registration number is required."

    prefix = license_no[0]
    if prefix == "1":
        model, source = FssaiLicense, "license_data"
    elif prefix == "2":
        model, source = FssaiRegistration, "registration_data"
    else:
        return None, "Unrecognized License/Registration number prefix (expected to start with 1 or 2)."

    row = db.session.get(model, license_no)  # PK lookup; identity-map cached

    if not row:
        return None, "License/Registration number not found."

    return {
        "companyName": row.company_name,
        "fullAddress": row.full_address,
        "expiryDate": row.expiry_date,
        "source": source,
    }, None


def lookup_ce(license_no: str):
    """Fetches Trade License details from KMC portal.
    Implements cross-process rate limiting: minimum 40 seconds between consecutive requests.
    Uses file locking to coordinate across multiple workers/processes.
    """
    lock_fd = None
    try:
        with open(_KMC_LOCK_PATH, "w") as lock_fd:
            if fcntl:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

            try:
                with open(_KMC_LAST_REQUEST_TIME_PATH) as f:
                    last_time = float(f.read().strip() or "0")
            except (FileNotFoundError, ValueError):
                last_time = 0

            current_time = time.time()
            elapsed = current_time - last_time
            if elapsed < _KMC_RATE_LIMIT_SECONDS:
                sleep_time = _KMC_RATE_LIMIT_SECONDS - elapsed
                time.sleep(sleep_time)
                current_time = time.time()

            with open(_KMC_LAST_REQUEST_TIME_PATH, "w") as f:
                f.write(str(current_time))
    finally:
        if lock_fd:
            try:
                if fcntl:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception as e:
                logger.warning("Failed to release file lock: %s", e)

    # ponytail: TLS verification enabled for security
    # KMC portal certificate is valid (signed by Sectigo)
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")

    with httpx.Client(timeout=15, verify=ctx) as client:
        client.get("https://www.kmcgov.in/KMCPortal/jsp/TradeLicenseInformation.jsp")
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
        fixed_text = re.sub(r"([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', raw_text)
        try:
            data = json.loads(fixed_text)
        except json.JSONDecodeError as e:
            logger.error("KMC JSON repair failed: %s", e)
            return None

    if not data.get("success"):
        return None

    try:
        rows = data["licenseNo"][0]
        identity = rows[0]
    except (KeyError, IndexError):
        return None

    fee_heads = [{"section": r.get("sectionCode"), "amount": r.get("demandAmount")} for r in rows]
    return {"identity": identity, "fee_heads": fee_heads, "is_closed": bool(identity.get("licClosingDate"))}
