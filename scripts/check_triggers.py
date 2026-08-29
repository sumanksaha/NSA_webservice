import os
from sqlalchemy import create_engine, text

os.environ['DATABASE_URL'] = 'postgresql://postgres.ugvrmjqrumscccrhvcto:fyP4fLbREF8jzpVt@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres'
e = create_engine(os.environ['DATABASE_URL'], connect_args={'sslmode': 'require'})
conn = e.connect()

# Check all triggers on these tables
result = conn.execute(text("""
    SELECT tg.tgname, tg.tgtype, tg.tgenabled, c.relname
    FROM pg_trigger tg
    JOIN pg_class c ON tg.tgrelid = c.oid
    WHERE c.relname IN ('fssai_licenses', 'fssai_registrations')
""")).fetchall()
print("All triggers:")
for r in result:
    print(f"  {r}")

# Check if trigger is for DELETE (bit 1 = DELETE)
# tgtype: bit 1 (1) = ROW trigger, bit 2 (2) = BEFORE, bit 4 (16) = DELETE, bit 8 (8) = INSERT, etc.
# BEFORE DELETE = 1 + 2 + 16 = 19
print("\nExpected: tgtype=19 (BEFORE DELETE ROW trigger)")

conn.close()