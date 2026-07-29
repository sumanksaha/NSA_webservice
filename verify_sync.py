import os

os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"

from app import create_app
from app.extensions import db
from app.models import FSO
from app.utils.fso_data import sync_fso_from_markdown

app = create_app()

with app.app_context():
    before = db.session.query(FSO).count()
    result = sync_fso_from_markdown()
    after = db.session.query(FSO).count()
