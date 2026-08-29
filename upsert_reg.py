import os, time
from pathlib import Path
import psycopg2
from sqlalchemy import create_engine, text

DB_URL = "postgresql://postgres.ugvrmjqrumscccrhvcto:fyP4fLbREF8jzpVt@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres"
os.environ["DATABASE_URL"] = DB_URL

csv_path = Path("/github/NSA_webservice/db/kmc_registration_issued.csv")
table = "fssai_registrations"
pk = "registration_no"
csv_pk = "registration_number"

print("Processing registrations...")
t0 = time.perf_counter()

engine = create_engine(DB_URL, connect_args={"sslmode": "require", "connect_timeout": 15})
with engine.connect() as conn:
    before = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    print(f"Before: {before} rows")

conn = psycopg2.connect(DB_URL, sslmode="require", connect_timeout=15)
conn.autocommit = False
cur = conn.cursor()
try:
    # Create temp table
    cur.execute("DROP TABLE IF EXISTS tmp_reg")
    cur.execute("CREATE TEMP TABLE tmp_reg (registration_number TEXT, company_name TEXT, full_address TEXT, expiry_date TEXT)")
    print("Temp table created")
    
    # Copy CSV
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        cur.copy_expert("COPY tmp_reg FROM STDIN WITH CSV HEADER", f)
    conn.commit()
    
    cur.execute("SELECT COUNT(*) FROM tmp_reg")
    cnt = cur.fetchone()[0]
    print(f"CSV loaded: {cnt} rows in {time.perf_counter()-t0:.1f}s")
    
    # Upsert
    cur.execute("""
        INSERT INTO fssai_registrations (registration_no, company_name, full_address, expiry_date)
        SELECT registration_number, company_name, full_address, expiry_date FROM tmp_reg
        ON CONFLICT (registration_no) DO UPDATE SET
            company_name = EXCLUDED.company_name,
            full_address = EXCLUDED.full_address,
            expiry_date = EXCLUDED.expiry_date
    """)
    conn.commit()
    print(f"Upserted: {cur.rowcount} rows in {time.perf_counter()-t0:.1f}s")
    
    # Delete stale
    cur.execute(f"DELETE FROM {table} WHERE {pk} NOT IN (SELECT {csv_pk} FROM tmp_reg)")
    deleted = cur.rowcount
    conn.commit()
    print(f"Deleted {deleted} stale records in {time.perf_counter()-t0:.1f}s")
    
    # Final count
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    final = cur.fetchone()[0]
    print(f"Final: {final} rows in {table} (elapsed: {time.perf_counter()-t0:.1f}s)")
    
    cur.execute("DROP TABLE IF EXISTS tmp_reg")
    conn.commit()
finally:
    cur.close(); conn.close()

print("Done!")