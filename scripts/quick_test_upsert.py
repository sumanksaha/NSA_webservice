"""Quick test upsert with limited rows."""
import csv
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from psycopg2.extras import execute_values

# Set DB URL
os.environ['DATABASE_URL'] = 'postgresql://postgres.ugvrmjqrumscccrhvcto:fyP4fLbREF8jzpVt@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres'

BASE_DIR = Path('/github/NSA_webservice')

# Quick test with just first 10 rows from each CSV
test_sources = [
    {
        "label": "kmc_license_test",
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
]

engine = create_engine(
    os.environ['DATABASE_URL'],
    connect_args={'sslmode': 'require', 'connect_timeout': 15}
)

# Test reading CSV
with open(test_sources[0]["csv"], "r", encoding="utf-8", errors="replace") as f:
    reader = csv.reader(f)
    csv_headers = next(reader)
    rows = list(reader)

print(f"Read {len(rows)} rows from CSV")

# Use only first 10 rows for testing
rows = rows[:10]

raw_conn = engine.raw_connection()
raw_conn.autocommit = False
try:
    cursor = raw_conn.cursor()
    
    # Prepare test data
    all_values = []
    for row in rows:
        vals = [row[0], row[1], row[2], row[3]]
        all_values.append(tuple(vals))
    
    # Insert test data
    execute_values(
        cursor,
        test_sources[0]["upsert_sql"],
        all_values,
        template=None,
        page_size=10
    )
    
    raw_conn.commit()
    print(f"Successfully inserted {len(all_values)} test rows")
    
    # Verify
    with engine.connect() as conn:
        pks = ", ".join(f"'{row[0]}'" for row in all_values)
        count = conn.execute(text(f"SELECT COUNT(*) FROM fssai_licenses WHERE license_no IN ({pks})")).scalar()
        print(f"Verified {count} rows in database")
    
    cursor.close()
finally:
    raw_conn.close()

print("Quick test completed successfully!")