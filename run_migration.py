import os
import sys
os.chdir('C:\\github\\NSA_webservice')
sys.path.insert(0, 'C:\\github\\NSA_webservice')

from app import create_app
from app.extensions import db
from flask_migrate import upgrade

app = create_app()

with app.app_context():
    try:
        # Run upgrade
        upgrade(directory='migrations')
        print("Migration upgrade completed successfully")
    except Exception as e:
        print(f"Migration upgrade failed: {e}")
        import traceback
        traceback.print_exc()
