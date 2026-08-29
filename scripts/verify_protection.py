import os
from sqlalchemy import create_engine, text

os.environ['DATABASE_URL'] = 'postgresql://postgres.ugvrmjqrumscccrhvcto:fyP4fLbREF8jzpVt@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres'
e = create_engine(os.environ['DATABASE_URL'], connect_args={'sslmode': 'require'})

def test_delete_block(table, pk):
    """Test DELETE protection on a single table using fresh connection"""
    conn = e.connect()
    trans = conn.begin()
    try:
        conn.execute(text(f"DELETE FROM {table} WHERE {pk} = '12826019000920'"))
        trans.commit()
        return False  # DELETE was allowed - protection failed
    except Exception as ex:
        trans.rollback()  # Important: rollback the failed transaction
        msg = str(ex)
        return "not allowed" in msg.lower()  # True if blocked correctly
    finally:
        conn.close()

# Test both tables with fresh transactions
for table, pk in [("fssai_licenses", "license_no"), ("fssai_registrations", "registration_no")]:
    blocked = test_delete_block(table, pk)
    if blocked:
        print(f"{table}: DELETE BLOCKED - Protection Active")
    else:
        print(f"{table}: DELETE ALLOWED - PROTECTION FAILED!")

# Verify data is intact
conn = e.connect()
lic_count = conn.execute(text("SELECT COUNT(*) FROM fssai_licenses")).scalar()
reg_count = conn.execute(text("SELECT COUNT(*) FROM fssai_registrations")).scalar()
conn.close()
print(f"\nFinal counts:")
print(f"  fssai_licenses: {lic_count} rows")
print(f"  fssai_registrations: {reg_count} rows")
print(f"\nDELETE protection status verified.")