"""Verify inspection_photos table in SQLite."""
import os
os.environ['FLASK_APP'] = 'app:create_app'

from app import create_app
from app.extensions import db
from sqlalchemy import inspect, text

app = create_app()
with app.app_context():
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print('RESULT: inspection_photos exists:', 'inspection_photos' in tables)

    cols = inspector.get_columns('inspection_photos')
    print('RESULT: Columns:')
    for c in cols:
        print(f'  {c["name"]}: {c["type"]} nullable={c["nullable"]}')

    fks = inspector.get_foreign_keys('inspection_photos')
    print('RESULT: Foreign keys:')
    for fk in fks:
        print(f'  {fk["constrained_columns"]} -> {fk["referred_table"]}.{fk["referred_columns"]} ondelete={fk.get("ondelete", "N/A")}')

    idxs = inspector.get_indexes('inspection_photos')
    print('RESULT: Indexes:')
    for idx in idxs:
        print(f'  {idx["name"]}: {idx["column_names"]} unique={idx["unique"]}')

    with db.engine.begin() as conn:
        result = conn.execute(text('SELECT version_num FROM alembic_version'))
        print('RESULT: Alembic version:', result.scalar())
