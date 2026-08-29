import csv, io, os, sys, time
from pathlib import Path
from sqlalchemy import create_engine, text
import psycopg2

DB_URL = os.getenv("DATABASE_URL")
# Parse for psycopg2
DB_URL_P = DB_URL.replace("postgresql://", "postgresql://") if DB_URL else None
csv_dir = Path("/github/NSA_webservice/db")

sources = [
    {"label": "kmc_license", "csv": csv_dir / "kmc_license_issued.csv", "table": "fssai_licenses", "pk": "license_no", "csv_pk": "license_number", "cols": ["license_number", "company_name", "full_address", "expiry_date"]},
    {"label": "kmc_registration", "csv": csv_dir / "kmc_registration_issued.csv", "table": "fssai_registrations", "pk": "registration_no", "csv_pk": "registration_number", "cols": ["registration_number", "company_name", "full_address", "expiry_date"]},
]

engine = create_engine(DB_URL, connect_args={"sslmode": "require", "connect_timeout": 15})

# Show counts
with engine.connect() as conn:
    for s in sources:
        c = conn.execute(text(f"SELECT COUNT(*) FROM {s['table']}")).scalar()
        print(f"{s['table']}: {c} rows")

for s in sources:
    csv_path = s["csv"]
    if not csv_path.exists():
        print(f"Missing: {csv_path}"); continue
    
    print(f"\nProcessing {s['label']}...")
    t0 = time.perf_counter()
    
    conn = psycopg2.connect(DB_URL_P, sslmode="require", connect_timeout=15)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # Temp table
        cur.execute(f"DROP TABLE IF EXISTS tmp_kmc")
        cur.execute("CREATE TEMP TABLE tmp_kmc (license_number TEXT, company_name TEXT, full_address TEXT, expiry_date TEXT)")
        
        # COPY CSV data into temp table using copy_expert (handles quoted fields)
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            cur.copy_expert("COPY tmp_kmc FROM STDIN WITH CSV HEADER", f)
        conn.commit()
        
        cur.execute("SELECT COUNT(*) FROM tmp_kmc")
        cnt = cur.fetchone()[0]
        print(f"  CSV rows loaded: {cnt}")
        
        # Upsert
        if s["label"] == "kmc_license":
            cur.execute("""
                INSERT INTO fssai_licenses (license_no, company_name, full_address, expiry_date)
                SELECT license_number, company_name, full_address, expiry_date FROM tmp_kmc
                ON CONFLICT (license_no) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    full_address = EXCLUDED.full_address,
                    expiry_date = EXCLUDED.expiry_date
            """)
        else:
            cur.execute("""
                INSERT INTO fssai_registrations (registration_no, company_name, full_address, expiry_date)
                SELECT registration_number, company_name, full_address, expiry_date FROM tmp_kmc
                ON CONFLICT (registration_no) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    full_address = EXCLUDED.full_address,
                    expiry_date = EXCLUDED.expiry_date
            """)
        conn.commit()
        print(f"  Upserted {cur.rowcount} rows")
        
        # Cleanup stale
        cur.execute("SELECT COUNT(*) FROM tmp_kmc")
        csv_pks = set(r[0] for r in cur.fetchall())
        
        # Delete DB rows not in CSV
        pk_col = s["pk"]
        csv_pk_col = s["csv_pk"]
        cur.execute(f"DELETE FROM {s['table']} WHERE {pk_col} NOT IN (SELECT {csv_pk_col} FROM tmp_kmc)")
        deleted = cur.rowcount
        conn.commit()
        print(f"  Removed {deleted} stale records")
        
        # Verify
        cur.execute(f"SELECT COUNT(*) FROM {s['table']}")
        final = cur.fetchone()[0]
        print(f"  Final: {final} rows in {s['table']}")
        
        # Samples
        cur.execute(f"SELECT * FROM {s['table']} LIMIT 2")
        for r in cur.fetchall():
            print(f"    Sample: {r}")
        
        cur.execute("DROP TABLE IF EXISTS tmp_kmc")
        conn.commit()
    finally:
        cur.close(); conn.close()
    
    dt = time.perf_counter() - t0
    print(f"  Done in {dt:.1f}s")

print("\nAll done!")