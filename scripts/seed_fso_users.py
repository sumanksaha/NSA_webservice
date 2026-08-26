"""Bulk-seed bound fso accounts for every FSO in fso_list.md.

Usage:
    python scripts/seed_fso_users.py --password 'Str0ng-Pass!1'

Idempotent: FSOs already bound to an account are skipped. The generated
accounts hold the `fso` role and are bound 1:1 to their officer name.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

os.environ.setdefault("SKIP_FSO_STARTUP_SYNC", "1")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed bound fso accounts for all FSOs.")
    parser.add_argument("--password", required=True, help="Initial password for created accounts.")
    args = parser.parse_args()

    from app import create_app
    from app.auth.provisioning import seed_fso_users

    app = create_app()
    with app.app_context():
        result = seed_fso_users(args.password)

    for name in result["created"]:
        print(f"created: {name}")
    for name in result["skipped"]:
        print(f"skipped: {name}")
    print(f"done — {len(result['created'])} created, {len(result['skipped'])} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
