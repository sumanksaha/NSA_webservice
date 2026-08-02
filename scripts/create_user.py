"""One-off script to create an initial admin user.

Usage:
    python scripts/create_user.py <username> [--admin]

You will be prompted for the password (hidden input). Pass ``--admin`` to
grant the user admin rights (ability to reset other users' passwords).

This script requires a running Flask app context to access the database.
It works with both SQLite and PostgreSQL.
"""

import getpass
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env before anything else
from dotenv import load_dotenv

load_dotenv()

# Skip FSO sync and other startup noise during script execution
os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import User


def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    username = sys.argv[1].strip()
    is_admin = "--admin" in sys.argv
    if not username:
        sys.exit(1)

    password = getpass.getpass("Password: ")
    if not password:
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        sys.exit(1)

    app = create_app()
    with app.app_context():
        # Check if user already exists
        existing = User.query.filter_by(username=username).first()
        if existing:
            sys.exit(1)

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
        )
        db.session.add(user)
        db.session.commit()


if __name__ == "__main__":
    main()
