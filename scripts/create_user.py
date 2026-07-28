"""
One-off script to create an initial admin user.

Usage:
    python scripts/create_user.py <username>

You will be prompted for the password (hidden input).

This script requires a running Flask app context to access the database.
It works with both SQLite and PostgreSQL.
"""

import getpass
import os
import sys

# Ensure the project root is on sys.path so that "from app" imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
        print("Usage: python scripts/create_user.py <username>")
        sys.exit(1)

    username = sys.argv[1].strip()
    if not username:
        print("Error: Username cannot be empty.")
        sys.exit(1)

    password = getpass.getpass("Password: ")
    if not password:
        print("Error: Password cannot be empty.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: Passwords do not match.")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        # Check if user already exists
        existing = User.query.filter_by(username=username).first()
        if existing:
            print(f"Error: User '{username}' already exists.")
            sys.exit(1)

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()

        print(f"User '{username}' created successfully (ID: {user.id}).")


if __name__ == "__main__":
    main()
