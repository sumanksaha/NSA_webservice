"""
Upsert KMC License/Registration CSV data to Supabase Postgres.

Usage:
    python scripts/upsert_kmc_csv_to_supabase.py [--dry-run] [--batch-size 5000]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import create_engine, text
from psycopg2.extras import execute_values


def get_db_url() -> str:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set in environment")
    return db_url


def resolve_db_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    return get_db_url()


SOURCES = [
    {
        "label": "kmc_license",
        "csv": BASE_DIR / "db" / "kmc_license_issued.csv",
        "table": "fssai_licenses",
        "pk": "license_no",
        "csv_cols": ["license_number", "company_name", "full_address", "expiry_date"],
        "upsert_sql": (
            "INSERT INTO fssai_licenses (license_no, company_name, full_address, expiry_date) "
            "VALUES %s ON CONFLICT (license_no) DO UPDATE SET "
            "company_name = EXCLUDED.company_name, "
            "full_address = EXCLUDED.full_address, "
            "expiry_date = EXCLUDED.expiry_date"
        ),
    },
    {
        "label": "kmc_registration",
        "csv": BASE_DIR / "db" / "kmc_registration_issued.csv",
        "table": "fssai_registrations",
        "pk": "registration_no",
        "csv_cols": ["registration_number", "company_name", "full_address", "expiry_date"],
        "upsert_sql": (
            "INSERT INTO fssai_registrations (registration_no, company_name, full_address, expiry_date) "
            "VALUES %s ON CONFLICT (registration_no) DO UPDATE SET "
            "company_name = EXCLUDED.company_name, "
            "full_address = EXCLUDED.full_address, "
            "expiry_date = EXCLUDED.expiry_date"
        ),
    },
]


def escape_val(val: str | None) -> str:
    if val is None:
        return "NULL"
    # Escape single quotes for SQL
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def upsert_csv_to_db(engine, source: dict, batch_size: int = 5000) -> tuple[int, int]:
    csv_path = source["csv"]
    
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        csv_headers = next(reader)
        rows = list(reader)
    
    print(f"  CSV columns: {csv_headers}")
    print(f"  CSV rows: {len(rows)}")
    
    # Find indices of CSV columns that map to DB columns
    col_indices = []
    for col in source["csv_cols"]:
        if col in csv_headers:
            col_indices.append(csv_headers.index(col))
        else:
            col_indices.append(-1)
    
    # Get PK index
    pk_idx = source["csv_cols"].index(source["pk"].replace("_no", "_number"))
    
    total_read = 0
    total_written = 0
    csv_pks = set()
    
    # Get raw psycopg2 connection from SQLAlchemy engine
    raw_conn = engine.raw_connection()
    raw_conn.autocommit = False
    try:
        cursor = raw_conn.cursor()
        
        # Step 1: Upsert all CSV data (respects ON CONFLICT guard)
        for batch_start in range(0, len(rows), batch_size):
            batch_end = min(batch_start + batch_size, len(rows))
            batch = rows[batch_start:batch_end]
            
            all_values = []
            for row in batch:
                vals = []
                for i, col_idx in enumerate(col_indices):
                    if col_idx >= 0 and col_idx < len(row):
                        vals.append(row[col_idx])
                    else:
                        vals.append(None)
                all_values.append(tuple(vals))
                
                # Collect PK for later cleanup
                if pk_idx >= 0 and pk_idx < len(row):
                    csv_pks.add(row[pk_idx])
            
            # Use execute_values for safe bulk upsert
            execute_values(
                cursor,
                source["upsert_sql"],
                all_values,
                template=None,
                page_size=batch_size
            )
            
            raw_conn.commit()
            
            total_read += len(batch)
            total_written += len(batch)
            
            if (batch_start // batch_size) % 5 == 0:
                print(f"  Processed {total_read}/{len(rows)} rows...")
        
        # Step 2: Clean up stale/test data - delete DB rows not in CSV
        # This respects the guard by only removing records that don't exist in the new source
        if csv_pks:
            print(f"  Cleaning up stale records (CSV has {len(csv_pks)} unique PKs)...")
            # Build a temporary table with CSV PKs for efficient DELETE
            cursor.execute(f"CREATE TEMP TABLE temp_csv_pks (pk {source['pk']}) ON COMMIT DROP")
            
            # Insert PKs in batches
            pk_batch = []
            for pk in csv_pks:
                pk_batch.append((pk,))
                if len(pk_batch) >= 10000:
                    execute_values(cursor, "INSERT INTO temp_csv_pks (pk) VALUES %s", pk_batch)
                    pk_batch = []
            if pk_batch:
                execute_values(cursor, "INSERT INTO temp_csv_pks (pk) VALUES %s", pk_batch)
            
            raw_conn.commit()
            
            # Delete from main table where PK not in temp table
            cursor.execute(
                f"DELETE FROM {source['table']} WHERE {source['pk']} NOT IN (SELECT pk FROM temp_csv_pks)"
            )
            deleted = cursor.rowcount
            raw_conn.commit()
            
            if deleted > 0:
                print(f"  Removed {deleted} stale/test records not present in CSV")
            else:
                print(f"  No stale records to remove")
        
        cursor.close()
    finally:
        raw_conn.close()
    
    return total_read, total_written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upsert KMC CSV data to Supabase Postgres")
    parser.add_argument("--dry-run", action="store_true", help="Count/report only; write nothing")
    parser.add_argument("--batch-size", type=int, default=5000, help="Rows per batch (default: 5000)")
    parser.add_argument("--db-url", default=None, help="Target DB URL (default: DATABASE_URL)")
    args = parser.parse_args(argv)
    
    db_url = resolve_db_url(args.db_url)
    print(f"Target database: {db_url[:60]}...")
    
    connect_args = {}
    if db_url.startswith("postgresql"):
        connect_args["sslmode"] = "require"
        connect_args["connect_timeout"] = 15
    
    engine = create_engine(db_url, connect_args=connect_args)
    
    with engine.connect() as conn:
        print("\nExisting row counts:")
        for s in SOURCES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {s['table']}")).scalar()
            print(f"  {s['table']}: {count} rows")
    
    if args.dry_run:
        print("\nDry run complete; no rows written.")
        return 0
    
    grand_total = 0
    for s in SOURCES:
        print(f"\nProcessing {s['label']} from {s['csv']}...")
        
        t0 = time.perf_counter()
        total_read, total_written = upsert_csv_to_db(engine, s, args.batch_size)
        dt = time.perf_counter() - t0
        rate = (total_written / dt) if dt > 0 else float("inf")
        
        print(f"  [{s['label']}] Upserted {total_written}/{total_read} rows in {dt:.1f}s ({rate:,.0f} rows/s)")
        grand_total += total_written
    
    print("\n=== Verification ===")
    with engine.connect() as conn:
        for s in SOURCES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {s['table']}")).scalar()
            print(f"Table '{s['table']}': {count} rows total")
            
            samples = conn.execute(text(f"SELECT * FROM {s['table']} LIMIT 3")).fetchall()
            for sample in samples:
                print(f"  Sample: {sample}")
    
    print(f"\nDone. Upserted {grand_total} rows total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())