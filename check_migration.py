import sys
import os
os.chdir('C:\\github\\NSA_webservice')
sys.path.insert(0, 'C:\\github\\NSA_webservice')

from app import create_app
from flask_migrate import Migrate, upgrade, current, get_version

app = create_app()
migrate = Migrate(app, app.extensions['db'])

with app.app_context():
    try:
        # Check current version
        from alembic.config import Config
        from alembic import command
        
        # Get alembic config
        alembic_cfg = Config('migrations/alembic.ini')
        alembic_cfg.set_main_option('script_location', 'migrations')
        
        # Get current head
        command.current(alembic_cfg, verbose=True)
    except Exception as e:
        print(f"Error: {e}")
