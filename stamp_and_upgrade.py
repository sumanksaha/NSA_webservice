import os
import sys

os.chdir("C:\\github\\NSA_webservice")
sys.path.insert(0, "C:\\github\\NSA_webservice")

from flask_migrate import stamp, upgrade

from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    try:
        # First reset the alembic_version to empty
        from sqlalchemy import text

        db.session.execute(text("DELETE FROM alembic_version"))
        db.session.commit()

        # Stamp to the migration before ours
        stamp(revision="add_fso_sample_inspection_tables")

        # Now run upgrade for our migration
        upgrade(revision="add_bill_sample_fields")

    except Exception:
        import traceback

        traceback.print_exc()
