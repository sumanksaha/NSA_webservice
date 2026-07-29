import sqlite3
import time

db_path = "C:\\github\\NSA_webservice\\instance\\app.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Insert a dummy row
cursor.execute("""
INSERT INTO fbo_issue (fbo_id, manufacturer_fbo_id, fbo_name, source_type, state, fso_name, created_at, updated_at, detail_json)
VALUES ('test_fbo_123', NULL, 'Test FBO', 'inspection', 'open', 'Test FSO', datetime('now'), datetime('now'), NULL)
""")
conn.commit()

# Get the id and initial updated_at
cursor.execute("SELECT id, updated_at FROM fbo_issue WHERE fbo_id = 'test_fbo_123'")
row = cursor.fetchone()
issue_id = row[0]
initial_updated_at = row[1]

# Wait a bit to ensure time difference
time.sleep(2)

# Update the row
cursor.execute("UPDATE fbo_issue SET state = 'closed' WHERE id = ?", (issue_id,))
conn.commit()

# Check updated_at changed
cursor.execute("SELECT updated_at FROM fbo_issue WHERE id = ?", (issue_id,))
new_updated_at = cursor.fetchone()[0]

if initial_updated_at != new_updated_at:
    pass
else:
    pass

# Delete the dummy row
cursor.execute("DELETE FROM fbo_issue WHERE id = ?", (issue_id,))
conn.commit()

conn.close()
