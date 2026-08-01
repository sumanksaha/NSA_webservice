"""Dump full SQLite schema for schema parity analysis."""

import sqlite3

DB = r"C:\github\NSA_webservice\instance\app.db"
conn = sqlite3.connect(DB)
c = conn.cursor()

# Get all tables
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in c.fetchall()]


for t in tables:
    # CREATE TABLE statement
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,))
    row = c.fetchone()
    if row and row[0]:
        pass

    # Show PRAGMA table_info for column details
    c.execute(f"PRAGMA table_info('{t}')")
    cols = c.fetchall()
    for col in cols:
        cid, name, ctype, notnull, dflt, pk = col
        dflt_str = dflt or ""

    # Indexes
    c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (t,))
    idxs = [r[0] for r in c.fetchall() if r[0]]
    if idxs:
        for _idx in idxs:
            pass

    # Foreign keys
    c.execute(f"PRAGMA foreign_key_list('{t}')")
    fks = c.fetchall()
    if fks:
        for fk in fks:
            fid, seq, ftable, ffrom, fto, on_up, on_del, match = fk

conn.close()
