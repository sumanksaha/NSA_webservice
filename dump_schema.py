"""Dump full SQLite schema for schema parity analysis."""
import sqlite3

DB = r"C:\github\NSA_webservice\instance\app.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

# Get all tables
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in c.fetchall()]

print("=" * 60)
print(f"Database: {DB}")
print(f"Tables ({len(tables)}): {tables}")
print("=" * 60)

for t in tables:
    print(f"\n{'='*60}")
    print(f"  TABLE: {t}")
    print(f"{'='*60}")

    # CREATE TABLE statement
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,))
    row = c.fetchone()
    if row and row[0]:
        print(f"  CREATE: {row[0]}")

    # Show PRAGMA table_info for column details
    c.execute(f"PRAGMA table_info('{t}')")
    cols = c.fetchall()
    print(f"\n  Columns ({len(cols)}):")
    print(f"  {'cid':<4} {'name':<28} {'type':<14} {'notnull':<8} {'dflt':<14} {'pk':<4}")
    print(f"  {'-'*4} {'-'*28} {'-'*14} {'-'*8} {'-'*14} {'-'*4}")
    for col in cols:
        cid, name, ctype, notnull, dflt, pk = col
        dflt_str = dflt if dflt else ""
        print(f"  {cid:<4} {name:<28} {ctype:<14} {notnull:<8} {dflt_str:<14} {pk:<4}")

    # Indexes
    c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (t,))
    idxs = [r[0] for r in c.fetchall() if r[0]]
    if idxs:
        print(f"\n  Indexes ({len(idxs)}):")
        for idx in idxs:
            print(f"    {idx}")

    # Foreign keys
    c.execute(f"PRAGMA foreign_key_list('{t}')")
    fks = c.fetchall()
    if fks:
        print(f"\n  Foreign Keys ({len(fks)}):")
        print(f"  {'id':<4} {'seq':<4} {'table':<20} {'from':<20} {'to':<20} {'on_update':<12} {'on_delete':<12} {'match':<10}")
        print(f"  {'-'*4} {'-'*4} {'-'*20} {'-'*20} {'-'*20} {'-'*12} {'-'*12} {'-'*10}")
        for fk in fks:
            fid, seq, ftable, ffrom, fto, on_up, on_del, match = fk
            print(f"  {fid:<4} {seq:<4} {ftable:<20} {ffrom:<20} {fto:<20} {on_up:<12} {on_del:<12} {match:<10}")

conn.close()
