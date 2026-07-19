import sqlite3
import os

db_path = os.path.join('C:\\github\\NSA_webservice\\instance', 'app.db')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check if sample table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sample'")
table_exists = cursor.fetchone()

if not table_exists:
    print("1. FAIL: sample table does not exist")
else:
    print("1. PASS: sample table exists")
    
    # Get table info
    cursor.execute("PRAGMA table_info(sample)")
    columns = cursor.fetchall()
    
    print("\nPRAGMA table_info(sample):")
    for col in columns:
        col_name, col_type, not_null, default_val, pk = col
        flag = "NOT NULL" if not_null else "NULL"
        print(f"  {col_name}: {col_type}, {flag}, pk={pk}, default={default_val}")
    
    # Check for NOT NULL on sample_type
    sample_type_col = [c for c in columns if c[1] == 'sample_type'][0] if any(c[1] == 'sample_type' for c in columns) else None
    if sample_type_col and sample_type_col[2] == 1:
        print("\n2. PASS: sample_type is NOT NULL")
    else:
        print("\n2. FAIL: sample_type is NULLABLE")
    
    # Check for CHECK constraints
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sample'")
    create_sql = cursor.fetchone()
    if create_sql:
        sql = create_sql[0]
        print(f"\nCREATE TABLE sample:")
        print(f"  {sql}")
        if "CHECK" in sql and "sample_type" in sql:
            print("\n3. PASS: sample_type has CHECK constraint")
        else:
            print("\n3. FAIL: sample_type missing CHECK constraint")

conn.close()
