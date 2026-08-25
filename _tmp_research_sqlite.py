import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
for fname, table, col in [
    ("license_data.db", "license_records", "license_no"),
    ("registration_data.db", "registration_records", "registration_no"),
]:
    path = os.path.join(BASE, "db", fname)
    print(f"=== {fname} ({os.path.getsize(path):,} bytes) ===")
    con = sqlite3.connect(path)
    cur = con.cursor()
    print("tables:", cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    print(f"{table} count:", cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    print("columns:", [(d[1], d[2], d[5]) for d in cur.execute(f"PRAGMA table_info({table})").fetchall()])
    print("indexes:", cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall())
    print("sample rows:")
    for r in cur.execute(f"SELECT * FROM {table} LIMIT 2").fetchall():
        print("  ", r)
    con.close()
