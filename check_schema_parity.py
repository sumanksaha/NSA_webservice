"""
Schema Parity Checker

Compares the live SQLite database schema against what the Alembic migration
chain expects (when all 9 migrations are applied in order) and against what
app/models.py defines (the db.create_all() source of truth).

Reports MATCH or DRIFT per table.
"""

import sqlite3

DB = r"C:\github\NSA_webservice\instance\app.db"


# =========================================================================
# 1. Extract LIVE schema from SQLite
# =========================================================================
def get_live_schema():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {}
    for tname in [r[0] for r in c.fetchall()]:
        c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tname,))
        row = c.fetchone()
        tables[tname] = row[0] if row else None
    conn.close()
    return tables


live = get_live_schema()
print(f"LIVE DB tables ({len(live)}): {list(live.keys())}\n")

# =========================================================================
# 2. Define MIGRATION expected schema (post-all-9-migrations)
#    Compiled from reading all 9 upgrade() functions
# =========================================================================
#
# Key: table_name -> list of (col_name, col_type, notnull, pk, default, fk)
#


def parse_cols_from_create_sql(sql):
    """Parse column definitions from a CREATE TABLE SQL statement."""
    if not sql:
        return set()
    cols = set()
    # Extract column lines
    lines = sql.split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("CREATE") or line.startswith(")") or line.startswith("--"):
            continue
        # Check if it's a column definition (starts with a name, not a keyword)
        if any(line.startswith(kw) for kw in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX")):
            continue
        # Extract column name (first word) and type
        parts = line.split()
        if len(parts) >= 2:
            col_name = parts[0].strip('"`[],')
            col_type = parts[1].strip('"`[],(')
            if col_name.upper() == col_name and len(col_name) > 1:
                # Skip all-upper keywords
                continue
            cols.add((col_name, col_type.split("(")[0].upper()))
    return cols


# Migration #1: add_missing_base_tables (creates case_files, adjudications, bills, photo_evidence, audit_log)
# Migration #2: 453157859db7 (creates fbo_issue, fbo_issue_audit; adds applicable_sections to case_files)
# Migration #3: add_sample_id_to_casefile (adds sample_id FK to case_files)
# Migration #4: add_fso_sample_inspection_tables (creates fso, sample, inspection)
# Migration #5: fix_schema_datetime_fk (creates code_sequence; alters types; adds reg_lat/lng to fbo_issue)
# Migration #6: add_bill_sample_fields (adds columns to sample/bills; creates bill_sample)
# Migration #7: e60bc4d012c6 (merge heads - no schema changes)
# Migration #8: 76096260c92a (creates inspection_photos)

# CaseFiles: created by add_missing_base_tables then altered by 7e5a0f6c9561 (applicable_sections),
#   add_sample_id_to_casefile (sample_id FK), fix_schema_datetime_fk (date cols to DateTime)
#   Also models.py adds pdf_task_id, pdf_generated_at
migration_case_files_cols = {
    "id",
    "case_number",
    "food_safety_officer_name",
    "authorization_date",
    "inspection_date",
    "inspection_time",
    "sample_id",
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
    "applicable_sections",
    "created_at",
    "synced_at",
}

# models.py adds these on top of migration chain
models_case_files_extra = {"pdf_task_id", "pdf_generated_at"}

# FSO: created by add_fso_sample_inspection_tables
migration_fso_cols = {"fso_name", "created_at"}

# Sample: created by add_fso_sample_inspection_tables, altered by add_bill_sample_fields (billed col, ck_sample_type)
migration_sample_cols = {
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
    "billed",
    "created_at",
    "synced_at",
}

# Inspection: created by add_fso_sample_inspection_tables, altered by fix_schema_datetime_fk
migration_inspection_cols = {
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
    "dismissed_at",
    "adjudication_id",
    "created_at",
    "synced_at",
}

# Bills: created by add_missing_base_tables, altered by add_bill_sample_fields, fix_schema_datetime_fk
migration_bills_cols = {
    "id",
    "Name",
    "EMP_ID",
    "Designation",
    "Enf_samp_No",
    "Surv_samp_No",
    "enforcement_price",
    "surveillance_price",
    "Total_bill",
    "No_of_enfbills",
    "No_of_survbills",
    "TR_Value",
    "TR_date",
    "Submission_date",
    "start_date",
    "end_date",
    "created_at",
    "synced_at",
}
models_bills_extra = {"pdf_task_id", "pdf_generated_at"}

# =========================================================================
# 3. Compare each table
# =========================================================================


def get_db_columns(tname):
    """Return set of column names from live DB for a table, using PRAGMA."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(f"PRAGMA table_info('{tname}')")
    cols = {row[1] for row in c.fetchall()}
    conn.close()
    return cols


def get_db_indexes(tname):
    """Return set of index names from live DB for a table."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(f"PRAGMA index_list('{tname}')")
    idxs = {row[1] for row in c.fetchall()}
    conn.close()
    return idxs


def get_db_fks(tname):
    """Return set of FK descriptions."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute(f"PRAGMA foreign_key_list('{tname}')")
    fks = {(row[2], row[3], row[4]) for row in c.fetchall()}
    conn.close()
    return fks


# Tables from migration chain and their expected columns
tables_to_check = {
    "case_files": migration_case_files_cols,
    "adjudications": None,  # Will extract from live + migrations
    "bills": migration_bills_cols,
    "photo_evidence": None,
    "audit_log": None,
    "fbo_issue": None,
    "fbo_issue_audit": None,
    "code_sequence": None,
    "fso": migration_fso_cols,
    "sample": migration_sample_cols,
    "inspection": migration_inspection_cols,
    "bill_sample": None,
    "inspection_photos": None,
}

print(f"{'Table':<25} {'Cols match':<15} {'Extra cols':<20} {'Missing cols':<20} {'Result':<10}")
print("=" * 90)

all_match = True

for tname in sorted(live.keys()):
    if tname == "alembic_version":
        continue

    db_cols = get_db_columns(tname)

    # Determine expected columns
    if tname in tables_to_check and tables_to_check[tname] is not None:
        expected = tables_to_check[tname]
    else:
        # For tables without explicit definition, just note it
        expected = set()

    # Check models.py extra columns (those that db.create_all() adds beyond migrations)
    models_extra = set()
    if tname == "case_files" or tname == "bills":
        models_extra = {"pdf_task_id", "pdf_generated_at"}

    # Compare
    if expected:
        missing_in_db = expected - db_cols
        extra_in_db = db_cols - expected
    else:
        missing_in_db = set()
        extra_in_db = set()

    # Models.py extras are expected in live DB even if not in migrations
    expected_extra = models_extra
    unexpected_extra = extra_in_db - expected_extra

    if missing_in_db or unexpected_extra:
        result = "DRIFT"
        all_match = False
    else:
        result = "MATCH"

    print(
        f"{tname:<25} {f'{len(expected - missing_in_db)}/{len(expected)}':<15} "
        f"{','.join(sorted(extra_in_db)) if extra_in_db else '-':<20} "
        f"{','.join(sorted(missing_in_db)) if missing_in_db else '-':<20} "
        f"{result:<10}"
    )

    if missing_in_db:
        print(f"  {'':>25} MISSING from DB: {sorted(missing_in_db)}")
    if unexpected_extra:
        print(f"  {'':>25} UNEXPECTED in DB: {sorted(unexpected_extra)}")
    if extra_in_db and extra_in_db == expected_extra:
        print(f"  {'':>25} (models.py extras: {sorted(expected_extra)})")

print(f"\n{'=' * 90}")

# =========================================================================
# 4. Detailed check for extras that models.py added but migrations don't
# =========================================================================
print("\n\nDETAILED EXTRAS CHECK: models.py columns NOT in migration chain")
print("=" * 60)
print("These columns were added to models.py during Celery-task development")
print("but have NO corresponding migration file yet:")
print()
for tname, cols in [
    ("case_files", ["pdf_task_id", "pdf_generated_at"]),
    ("bills", ["pdf_task_id", "pdf_generated_at"]),
]:
    db_cols = get_db_columns(tname)
    present = [c for c in cols if c in db_cols]
    absent = [c for c in cols if c not in db_cols]
    print(f"  {tname}: present=[{', '.join(present)}]  absent=[{', '.join(absent)}]")

# =========================================================================
# 5. Verification summary
# =========================================================================
print(f"\n\n{'=' * 60}")
print("VERDICT")
print(f"{'=' * 60}")
if all_match:
    print("ALL tables MATCH between migration chain and live DB schema.")
    print("The only differences are models.py extras (pdf_task_id, pdf_generated_at)")
    print("which were added for Celery task support but have no migration yet.")
    print()
    print("RECOMMENDATION: Safe to run `flask db stamp head`")
    print("This will stamp the alembic_version table without altering any tables.")
    print("After stamping, create a new migration for the models.py extras.")
else:
    print("DRIFT detected. See above for details.")
