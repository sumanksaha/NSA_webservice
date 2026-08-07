"""Airtable sync service for NSA Webservice.

Provides best-effort parallel sync to Airtable alongside Google Sheets,
as part of the Multi-Target Sheets Redundancy (Priority 7) architecture.

Key design:
- Lazy ``pyairtable`` import (graceful degradation if not installed or
  credentials missing — syncs are silently skipped, never blocking core flow).
- Thread-local client caching (matches ``sheets_sync.py`` pattern).
- Base rotation for Airtable's 1,200-record free-tier limit: when the
  active base nears capacity, a new base is created programmatically via
  the Airtable REST API and subsequent records are routed there.
- Formula-injection prevention (matches ``sheets_sync.py``'s
  ``_escape_formula``).
- R2 CSV export for backup/restore chain.
"""
import csv
import io
import logging
import threading
from datetime import UTC, datetime

from flask import current_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level config dicts
# ---------------------------------------------------------------------------
# Mirrors WORKSHEET_MAP in app/services/sheets_sync.py
AIRTABLE_TABLE_MAP = {
    "non_sample": "NonSample_Adjudication",
    "sample": "Sample_CaseFile",
    "billing": "Billing",
    "sample_repo": "Sample_Repository",
    "inspection_log": "Inspection_Log",
    "food_cell_do_intimations": "FoodCellDOIntimations",
}

# Mirrors SHEET_COLUMNS in app/services/sheets_sync.py
AIRTABLE_FIELD_MAP = {
    "non_sample": [
        "case_number", "food_safety_officer", "non_license", "pre_authorization",
        "complaint_lodged", "ce_license_no", "ce_trade_name", "ce_proprietor",
        "ce_address", "ce_status", "fbo_owner", "fbo_name", "fbo_address",
        "fssai_license", "concerned_food", "problem", "First_inspection_date",
        "compliance_deadline", "Complaint_date", "inspection_date",
        "authorization_date", "clean_premise", "refrigerator_clean",
        "proper_attire", "proper_covered_utensil", "date_tag",
        "veg_nonveg_separation", "food_segregation", "license_display",
        "artificial_colour", "Expired_item", "Pest_report", "Water_report",
        "section_55", "section_56", "section_58", "section_63", "section_64",
        "created_at",
    ],
    "sample": [
        "case_number", "food_safety_officer_name", "authorization_date",
        "inspection_date", "inspection_time", "manufacturer_fssai",
        "manufacturer_name", "manufacturer_fbo_name", "manufacturer_address",
        "retailer_fssai", "retailer_name", "retailer_fbo_name", "retailer_address",
        "product_name", "batch_no", "sample_quantity", "packet_count",
        "mfg_date", "expiry_date", "other_food_articles", "total_cost",
        "cost_in_words", "sample_code", "sample_submission_date",
        "Lab_Registration_No", "do_receipt_date", "is_misbranded",
        "is_substandard", "analyst_report_no", "analyst_report_date",
        "directive_letter_no", "directive_letter_date",
        "retailer_report_receive_date", "manufacturer_report_receive_date",
        "applicable_regulation", "applicable_clause", "sample_name",
        "sample_id", "created_at",
    ],
    "billing": [
        "Name", "EMP_ID", "Designation", "Enf_samp_No", "Surv_samp_No",
        "Total_bill", "No_of_enfbills", "No_of_survbills", "TR_Value",
        "TR_date", "Submission_date", "created_at",
    ],
    "sample_repo": [
        "id", "sample_code", "sample_name", "sample_type", "fso_name",
        "collection_date", "submission_date", "retailer_fssai",
        "retailer_name", "price", "created_at", "synced_at",
    ],
    "inspection_log": [
        "id", "inspection_code", "fso_name", "fssai_license", "ce_license_no",
        "fbo_name", "fbo_address", "concerned_food", "problem",
        "inspection_date", "compliance_deadline", "is_dismissed",
        "dismissed_by", "adjudication_id", "created_at", "synced_at",
    ],
    "food_cell_do_intimations": [
        "sample_id", "sample_code", "sample_name", "fso_name",
        "retailer_name", "collection_date", "do_reference_no",
        "food_cell_forwarded", "status", "pdf_url",
    ],
}

# Threshold for base rotation: Airtable free tier = 1,200 records/base.
BASE_ROTATION_THRESHOLD = 1100

# Thread-local storage for pyairtable client
_thread_local = threading.local()


def _env(key: str) -> str | None:
    """Read an environment variable."""
    import os

    return os.environ.get(key)


def _get_client():
    """Return a lazily-created ``pyairtable.Api`` instance (thread-local).

    Falls back to ``None`` if ``pyairtable`` is not installed or the
    ``AIRTABLE_API_KEY`` env var is missing, so the app boots regardless.
    """
    cached = getattr(_thread_local, "client", None)
    if cached is not None:
        return cached

    api_key = current_app.config.get("AIRTABLE_API_KEY") or _env("AIRTABLE_API_KEY")
    if not api_key:
        logger.debug("AIRTABLE_API_KEY not configured - Airtable sync disabled")
        return None

    try:
        from pyairtable import Api

        client = Api(api_key)
        _thread_local.client = client
        return client
    except ImportError:
        logger.warning("pyairtable not installed - Airtable sync disabled")
        return None
    except Exception as e:
        logger.error("Failed to create Airtable client: %s", e)
        return None


def _escape_formula(value):
    """Prefix dangerous leading characters to prevent Airtable formula injection."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


# ---------------------------------------------------------------------------
# Base management (with rotation)
# ---------------------------------------------------------------------------
def _get_base_id(module: str) -> str | None:
    """Return the Airtable base ID for *module*.

    Uses ``AIRTABLE_BASE_ID`` from config as the primary base.  If the
    base is near capacity and ``schema.bases:write`` scope is available,
    a new base is created and its ID is returned.
    """
    primary_base = (
        current_app.config.get("AIRTABLE_BASE_ID")
        or _env("AIRTABLE_BASE_ID")
    )
    if not primary_base:
        logger.debug("AIRTABLE_BASE_ID not configured - Airtable sync disabled")
        return None

    client = _get_client()
    if client is None:
        return None

    if _base_near_capacity(client, primary_base, module):
        rotated = _rotate_base(module)
        if rotated is not None:
            return rotated
        logger.warning(
            "Base %s near capacity, rotation unavailable - continuing with primary",
            primary_base,
        )

    return primary_base


def _base_near_capacity(client, base_id: str, module: str) -> bool:
    """Check whether *base_id* has >= BASE_ROTATION_THRESHOLD records."""
    table_name = AIRTABLE_TABLE_MAP.get(module)
    if not table_name:
        return False
    try:
        count = 0
        table = client.table(base_id, table_name)
        for _ in table.iter_all(page_size=100):
            count += 1
            if count >= BASE_ROTATION_THRESHOLD:
                return True
        return False
    except Exception as e:
        logger.debug("Capacity check failed for base %s: %s", base_id, e)
        return False


def _build_table_fields(module: str) -> list[dict]:
    """Build Airtable field spec list for table creation (base rotation)."""
    cols = AIRTABLE_FIELD_MAP.get(module, [])
    type_hints = {"sample_id": "number"}
    fields = []
    for col in cols:
        ftype = type_hints.get(col, "text")
        fields.append({"name": col, "type": ftype, "options": {}})
    return fields


def _rotate_base(module: str) -> str | None:
    """Create a new Airtable base for *module* and return its ID."""
    import httpx

    api_key = current_app.config.get("AIRTABLE_API_KEY") or _env("AIRTABLE_API_KEY")
    if not api_key:
        return None

    table_name = AIRTABLE_TABLE_MAP.get(module)
    if not table_name:
        return None

    fields_spec = _build_table_fields(module)
    primary_field = AIRTABLE_FIELD_MAP.get(module, ["id"])[0]

    try:
        resp = httpx.post(
            "https://api.airtable.com/v0/meta/bases",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "name": f"NSA_{table_name}",
                "tables": [
                    {
                        "name": table_name,
                        "fields": fields_spec,
                        "primaryFieldName": primary_field,
                        "primaryFieldType": {"type": "autonumber", "options": {}},
                    }
                ],
            },
            timeout=30,
        )
        if resp.status_code == 200:
            new_base_id = resp.json().get("id")
            if new_base_id:
                logger.info("Created new Airtable base %s for module %s", new_base_id, module)
                return new_base_id
        logger.warning("Airtable base creation failed (status %d)", resp.status_code)
    except Exception as e:
        logger.warning("Airtable base creation failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Record tracking (AirtableBaseMap model)
# ---------------------------------------------------------------------------
def _track_airtable_sync(
    db_record_id: int, module: str, airtable_record_id: str, base_id: str
) -> None:
    """Persist the mapping between a local DB record and its Airtable row."""
    try:
        from app.extensions import db
        from app.models import AirtableBaseMap

        mapping = AirtableBaseMap(
            record_id=db_record_id,
            module=module,
            airtable_record_id=airtable_record_id,
            airtable_base_id=base_id,
            airtable_table_name=AIRTABLE_TABLE_MAP.get(module, module),
        )
        db.session.add(mapping)
        db.session.commit()
    except Exception as e:
        logger.warning("Failed to track Airtable sync mapping: %s", e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def sync_to_airtable(
    module: str, row_dict: dict, db_record_id: int | None = None
) -> bool:
    """Sync a row of data to the appropriate Airtable base/table.

    Args:
        module: Key in ``AIRTABLE_TABLE_MAP`` (e.g. 'food_cell_do_intimations').
        row_dict: Dictionary containing field names and values.
        db_record_id: Local DB record ID for tracking (enables restore mapping).

    Returns:
        bool: True if sync succeeded, False otherwise.
    """
    # Gate on feature flag — dormant when ENABLE_AIRTABLE_SYNC is false
    if not current_app.config.get("ENABLE_AIRTABLE_SYNC", False):
        return False

    client = _get_client()
    if client is None:
        return False

    base_id = _get_base_id(module)
    if not base_id:
        return False

    table_name = AIRTABLE_TABLE_MAP.get(module)
    if not table_name:
        logger.error("Unknown Airtable module: %s", module)
        return False

    cols = AIRTABLE_FIELD_MAP.get(module, [])
    fields = {}
    for c in cols:
        v = row_dict.get(c, "")
        v = _escape_formula(v)
        if v != "" and v is not None:
            fields[c] = v

    try:
        table = client.table(base_id, table_name)
        result = table.insert(fields)
        airtable_record_id = (
            result.get("id") if isinstance(result, dict) else None
        )

        if db_record_id is not None and airtable_record_id:
            _track_airtable_sync(db_record_id, module, airtable_record_id, base_id)

        logger.info("Synced record to Airtable [%s] (base=%s)", module, base_id)
        return True
    except Exception as e:
        logger.error("Airtable sync failed [%s]: %s", module, e)
        return False


# ---------------------------------------------------------------------------
# CSV export for R2 backup (restore chain)
# ---------------------------------------------------------------------------
def export_airtable_all_bases_to_r2() -> str | None:
    """Export all Airtable base records to a combined CSV in R2.

    Downloads all records from every configured table and uploads to
    ``nsa_backups/airtable_csv/`` in R2.

    Returns the R2 key on success, or a local filepath on fallback.
    """
    # Gate on feature flag — dormant when ENABLE_AIRTABLE_SYNC is false
    if not current_app.config.get("ENABLE_AIRTABLE_SYNC", False):
        return None

    client = _get_client()
    if client is None:
        return None

    base_id = (
        current_app.config.get("AIRTABLE_BASE_ID")
        or _env("AIRTABLE_BASE_ID")
    )
    if not base_id:
        return None

    rows: list[dict] = []

    for module, table_name in AIRTABLE_TABLE_MAP.items():
        try:
            table = client.table(base_id, table_name)
            for record in table.iter_all(page_size=100):
                row = {"module": module, "base_id": base_id}
                for k, v in record.get("fields", {}).items():
                    row[k] = v if v is not None else ""
                rows.append(row)
        except Exception as e:
            logger.warning("Airtable export failed for %s: %s", module, e)

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
        key = f"nsa_backups/airtable_csv/airtable_export_{ts}.csv"
        r2.put_object(Bucket=_get_bucket(), Key=key, Body=csv_content)
        logger.info("Exported Airtable data to R2: %s", key)
        return key
    except Exception as e:
        logger.error("Failed to upload Airtable CSV to R2: %s", e)

    # Fallback: save locally
    from pathlib import Path

    local_dir = Path(current_app.instance_path) / "backups" / "airtable_csv"
    local_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    local_file = local_dir / f"airtable_export_{ts}.csv"
    local_file.write_text(csv_content, encoding="utf-8")
    logger.info("Saved Airtable CSV backup locally: %s", local_file)
    return str(local_file)


def is_configured() -> bool:
    """Return True if Airtable API key and base ID are configured."""
    return bool(
        (current_app.config.get("AIRTABLE_API_KEY") or _env("AIRTABLE_API_KEY"))
        and (current_app.config.get("AIRTABLE_BASE_ID") or _env("AIRTABLE_BASE_ID"))
    )
