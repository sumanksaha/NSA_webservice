import os
import sys

os.chdir("C:\\github\\NSA_webservice")
sys.path.insert(0, "C:\\github\\NSA_webservice")

from app import create_app
from app.extensions import db
from app.models import *

app = create_app()

with app.app_context():
    db.create_all()

    # Now check schema
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    columns = inspector.get_columns("sample")
    for _col in columns:
        pass
