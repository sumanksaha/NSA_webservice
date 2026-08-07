"""Microsoft Excel Online sync service for NSA Webservice.

Provides best-effort parallel sync to Excel Online (via Microsoft Graph API)
alongside Google Sheets and Airtable, as part of the Multi-Target Sheets
Redundancy (Priority 7) architecture.

Key design:
- Lazy ``msal`` import (graceful degradation if not installed or
  credentials missing — syncs are silently skipped, never blocking core flow).
- Thread-local token caching (access tokens cached for ~50 min).
- Formula-injection prevention (matches ``sheets_sync.py``'s
  ``_escape_formula``).
- R2 CSV export for backup/restore chain.

Authentication uses the OAuth 2.0 client credentials flow with
``msal.ConfidentialClientApplication``, requiring Azure AD app registration
with ``Files.ReadWrite.All`` (or ``Files.ReadWrite`` on a specific workbook)
application permissions.
"""

import csv
import io
import logging
import os
import threading
import time
from datetime import UTC, datetime

from flask import current_app

logger = logging.getLogger(__name__)

# Thread-local storage for MSAL token cache
_thread_local = threading.local()

# Token cache lifetime (seconds): cache for 50 min to avoid excessive refresh
_TOKEN_CACHE_TTL = 50 * 60


def _env(key: str) -> str | None:
    """Read an environment variable."""
    return os.environ.get(key)


def _get_token() -> str | None:
    """Return a cached or freshly-requested Microsoft Graph access token.

    Uses ``msal.ConfidentialClientApplication`` with the client credentials
    flow.  Returns ``None`` if any required env var is missing, ``msal`` is
    not installed, or the token request fails.
    """
    cached = getattr(_thread_local, "token", None)
    cached_time = getattr(_thread_local, "token_time", 0)
    if cached and (time.time() - cached_time) < _TOKEN_CACHE_TTL:
        return cached

    tenant_id = current_app.config.get("MS_TENANT_ID") or _env("MS_TENANT_ID")
    client_id = current_app.config.get("MS_CLIENT_ID") or _env("MS_CLIENT_ID")
    client_secret = (
        current_app.config.get("MS_CLIENT_SECRET") or _env("MS_CLIENT_SECRET")
    )
    spreadsheet_id = (
        current_app.config.get("MS_SPREADSHEET_ID") or _env("MS_SPREADSHEET_ID")
    )

    if not all([tenant_id, client_id, client_secret, spreadsheet_id]):
        logger.debug(
            "Excel sync disabled: missing MS_TENANT_ID/MS_CLIENT_ID/"
            "MS_CLIENT_SECRET/MS_SPREADSHEET_ID"
        )
        return None

    try:
        from msal import ConfidentialClientApplication  # type: ignore[import-untyped]

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=authority,
        )
        result = app.acquire_token_silent(
            ["https://graph.microsoft.com/.default"], accounts=[]
        )
        if not result:
            result = app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        if "access_token" not in result:
            logger.error(
                "Excel sync: token request failed: %s",
                result.get("error_description", "unknown"),
            )
            return None

        token = result["access_token"]
        _thread_local.token = token
        _thread_local.token_time = time.time()
        return token
    except ImportError:
        logger.warning("msal not installed - Excel sync disabled")
        return None
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to acquire Excel access token: %s", e)
        return None


def _get_graph_session():
    """Return an authenticated ``requests.Session`` for Microsoft Graph.

    Returns ``None`` if no token is available.
    """
    token = _get_token()
    if token is None:
        return None

    import requests

    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    return session


def _escape_formula(value):
    """Prefix dangerous leading characters to prevent Excel formula injection."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _worksheet_name(module: str) -> str | None:
    """Map a module key to its Excel worksheet name via ``WORKSHEET_MAP``."""
    try:
        from app.services.sheets_sync import WORKSHEET_MAP

        return WORKSHEET_MAP.get(module, module)
    except Exception:  # noqa: BLE001
        return module


def is_configured() -> bool:
    """Return True if all required Excel Online env vars are set."""
    return all(
        [
            _env("MS_TENANT_ID"),
            _env("MS_CLIENT_ID"),
            _env("MS_CLIENT_SECRET"),
            _env("MS_SPREADSHEET_ID"),
        ]
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def sync_to_excel(
    module: str, row_dict: dict, db_record_id: int | None = None
) -> bool:
    """Append a row to the Excel worksheet corresponding to *module*.

    Args:
        module: Key in ``WORKSHEET_MAP`` (e.g. 'food_cell_do_intimations').
        row_dict: Dictionary containing field names and values.
        db_record_id: Unused (kept for signature parity with ``sync_to_airtable``).

    Returns:
        bool: True if sync succeeded, False otherwise.
    """
    # Gate on feature flag — dormant when ENABLE_EXCEL_SYNC is false
    if not current_app.config.get("ENABLE_EXCEL_SYNC", False):
        return False

    spreadsheet_id = (
        current_app.config.get("MS_SPREADSHEET_ID") or _env("MS_SPREADSHEET_ID")
    )
    if not spreadsheet_id:
        return False

    session = _get_graph_session()
    if session is None:
        return False

    ws_name = _worksheet_name(module)
    if not ws_name:
        logger.error("Unknown Excel module: %s", module)
        return False

    # Build the column-order from the matching SHEET_COLUMNS entry if
    # available; otherwise fall back to the dict's natural insertion order.
    try:
        from app.services.sheets_sync import SHEET_COLUMNS

        cols = SHEET_COLUMNS.get(module)
    except Exception:  # noqa: BLE001
        cols = None

    if cols:
        values = [_escape_formula(str(row_dict.get(c, "") or "")) for c in cols]
    else:
        values = [
            _escape_formula(str(v) if v is not None else "")
            for v in row_dict.values()
        ]

    # Graph API: append a row to a worksheet
    # POST /drive/items/{item-id}/workbook/worksheets('{sheet-name}')/rows
    url = (
        f"https://graph.microsoft.com/v1.0/me/drive/items/{spreadsheet_id}"
        f"/workbook/worksheets('{ws_name}')/rows"
    )
    payload = {"values": [values]}

    try:
        resp = session.post(url, json=payload, timeout=30)
        if resp.status_code in (200, 201):
            logger.info("Synced record to Excel [%s] (worksheet=%s)", module, ws_name)
            return True
        logger.error(
            "Excel sync failed [%s] (status %d): %s",
            module,
            resp.status_code,
            resp.text[:200],
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Excel sync failed [%s]: %s", module, e)
        return False


# ---------------------------------------------------------------------------
# CSV export for R2 backup (restore chain)
# ---------------------------------------------------------------------------
def export_excel_to_r2() -> str | None:
    """Export Excel worksheet data to a combined CSV in R2.

    Reads each worksheet via the Graph API ``usedRange`` endpoint, builds a
    combined CSV, and uploads to ``nsa_backups/excel_csv/`` in R2.

    Returns the R2 key on success, or a local filepath on fallback.
    Returns ``None`` if Excel sync is not configured.
    """
    # Gate on feature flag — dormant when ENABLE_EXCEL_SYNC is false
    if not current_app.config.get("ENABLE_EXCEL_SYNC", False):
        return None

    session = _get_graph_session()
    if session is None:
        return None

    try:
        from app.services.sheets_sync import WORKSHEET_MAP

        modules = list(WORKSHEET_MAP.keys())
    except Exception:  # noqa: BLE001
        modules = ["food_cell_do_intimations"]

    rows: list[dict] = []

    for module in modules:
        ws_name = _worksheet_name(module)
        spreadsheet_id = (
            current_app.config.get("MS_SPREADSHEET_ID") or _env("MS_SPREADSHEET_ID")
        )
        # GET .../workbook/worksheets('{name}')/usedRange/$value
        url = (
            f"https://graph.microsoft.com/v1.0/me/drive/items/"
            f"{spreadsheet_id}"
            f"/workbook/worksheets('{ws_name}')/usedRange/$value"
        )
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    "Excel export failed for %s (status %d)",
                    module,
                    resp.status_code,
                )
                continue
            # The $value endpoint returns tab-delimited text
            reader = csv.DictReader(io.StringIO(resp.text), delimiter="\t")
            for record in reader:
                row = {"module": module}
                row.update(record)
                rows.append(row)
        except Exception as e:  # noqa: BLE001
            logger.warning("Excel export failed for %s: %s", module, e)

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
        from app.utils.storage import _get_client as _get_r2_client, _get_bucket

        r2 = _get_r2_client()
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        key = f"nsa_backups/excel_csv/excel_export_{ts}.csv"
        r2.put_object(Bucket=_get_bucket(), Key=key, Body=csv_content)
        logger.info("Exported Excel data to R2: %s", key)
        return key
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to upload Excel CSV to R2: %s", e)

    # Fallback: save locally
    from pathlib import Path

    local_dir = Path(current_app.instance_path) / "backups" / "excel_csv"
    local_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    local_file = local_dir / f"excel_export_{ts}.csv"
    local_file.write_text(csv_content, encoding="utf-8")
    logger.info("Saved Excel CSV backup locally: %s", local_file)
    return str(local_file)