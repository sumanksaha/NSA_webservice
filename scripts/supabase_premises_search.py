"""Live query Supabase fssai_licenses / fssai_registrations for a premises name."""

import os
import sys
from sqlalchemy import create_engine, text

# Supabase Postgres connection.
# Set SUPABASE_DATABASE_URL in the environment; no credentials are committed.
DB_URL = os.getenv("SUPABASE_DATABASE_URL")


def search_premises(name: str):
    engine = create_engine(DB_URL)
    results = []
    with engine.connect() as conn:
        for table in ("fssai_licenses", "fssai_registrations"):
            pk_col = "license_no" if table == "fssai_licenses" else "registration_no"
            stmt = text(
                f"SELECT {pk_col}, company_name, full_address, expiry_date FROM {table} WHERE company_name ILIKE :name"
            )
            resp = conn.execute(stmt, {"name": f"%{name}%"})
            for row in resp.mappings():
                d = dict(row)
                d["_source_table"] = table
                results.append(d)
    return results


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "devyani"
    hits = search_premises(name)
    if not hits:
        print(f"No results for '{name}'")
    for h in hits:
        print(h)
