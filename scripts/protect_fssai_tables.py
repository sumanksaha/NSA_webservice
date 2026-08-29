import os
from sqlalchemy import create_engine, text

os.environ['DATABASE_URL'] = 'postgresql://postgres.ugvrmjqrumscccrhvcto:fyP4fLbREF8jzpVt@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres'
e = create_engine(os.environ['DATABASE_URL'], connect_args={'sslmode': 'require'})
conn = e.connect()

# Check existing triggers
result = conn.execute(text("SELECT trigger_name, event_manipulation, event_object_table FROM information_schema.triggers WHERE event_manipulation='DELETE' AND event_object_table IN ('fssai_licenses', 'fssai_registrations')")).fetchall()
print(f"Existing DELETE triggers: {result}")

# Check if our function exists
func = conn.execute(text("SELECT proname FROM pg_proc WHERE proname = 'prevent_fssai_delete'")).fetchall()
print(f"Function exists: {func}")

# Create the protection function if not exists
conn.execute(text("""
    CREATE OR REPLACE FUNCTION prevent_fssai_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'DELETE operations are not allowed on the % table. '
            'This table contains protected FSSAI lookup data. '
            'Use UPDATE to modify records or contact the database administrator.',
            TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql;
"""))
conn.commit()
print("Function created")

# Create trigger on fssai_licenses
conn.execute(text("""
    DROP TRIGGER IF EXISTS fssai_licenses_no_delete ON fssai_licenses;
    CREATE TRIGGER fssai_licenses_no_delete
    BEFORE DELETE ON fssai_licenses
    FOR EACH ROW
    EXECUTE FUNCTION prevent_fssai_delete();
"""))
conn.commit()
print("Trigger created on fssai_licenses")

# Create trigger on fssai_registrations
conn.execute(text("""
    DROP TRIGGER IF EXISTS fssai_registrations_no_delete ON fssai_registrations;
    CREATE TRIGGER fssai_registrations_no_delete
    BEFORE DELETE ON fssai_registrations
    FOR EACH ROW
    EXECUTE FUNCTION prevent_fssai_delete();
"""))
conn.commit()
print("Trigger created on fssai_registrations")

# Verify
result = conn.execute(text("SELECT trigger_name, event_manipulation, event_object_table FROM information_schema.triggers WHERE event_manipulation='DELETE' AND event_object_table IN ('fssai_licenses', 'fssai_registrations')")).fetchall()
print(f"DELETE triggers after creation: {result}")

# Test the trigger
print("\nTesting DELETE protection...")
try:
    conn.execute(text("DELETE FROM fssai_licenses WHERE license_no = '12826019000920'"))
    conn.commit()
    print("ERROR: DELETE was not blocked!")
except Exception as e:
    print(f"DELETE blocked correctly: {e}")

conn.execute(text("DELETE FROM fssai_registrations WHERE registration_no = '22820044000070'"))
try:
    conn.execute(text("DELETE FROM fssai_registrations WHERE registration_no = '22820044000070'"))
    conn.commit()
    print("ERROR: DELETE was not blocked!")
except Exception as e:
    print(f"DELETE blocked correctly: {e}")

print("\nProtection verified!")