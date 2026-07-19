import os
import sys
os.chdir('C:\\github\\NSA_webservice')
sys.path.insert(0, 'C:\\github\\NSA_webservice')

from app import create_app
from app.extensions import db
from flask_migrate import stamp

app = create_app()

with app.app_context():
    try:
        # Stamp to the latest migration (add_fso_sample_inspection_tables)
        # This tells Alembic the DB is at that state
        stamp(revision='add_fso_sample_inspection_tables')
        print("Database stamped successfully to add_fso_sample_inspection_tables")
        
        # Now run upgrade for remaining migrations
        from flask_migrate import upgrade
        upgrade(revision='add_bill_sample_fields')
        print("Upgrade to add_bill_sample_fields completed successfully")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
