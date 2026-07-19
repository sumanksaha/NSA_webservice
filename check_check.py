import sqlite3
import os

db_path = os.path.join('C:\\github\\NSA_webservice\\instance', 'app.db')

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get the CREATE TABLE statement
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sample'")
create_sql = cursor.fetchone()
if create_sql:
    print("CREATE TABLE sample:")
    print(create_sql[0])
    print()
    if "CHECK" in create_sql[0] and "sample_type" in create_sql[0]:
        print("PASS: sample_type has CHECK constraint")
    else:
        print("FAIL: sample_type missing CHECK constraint")
        
# Also check the constraints
cursor.execute("PRAGMA table_info(sample)")
columns = cursor.fetchall()
print("\nPRAGMA table_info(sample):")
for col in columns:
    print(f"  {col}")

conn.close()
