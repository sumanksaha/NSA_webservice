#!/usr/bin/env python3
"""One-time migration: update dummy SKS-2026-XXXXX codes to real SL/WB/... codes.

Usage:
    python scripts/migrate_sample_codes.py

Mapping (update these before running):
    SKS-2026-XXXXX → SL/WB/110223/2026/25275
    SKS-2026-XXXXX → SL/WB/110223/2026/25279
    SKS-2026-XXXXX → SL/WB/110223/2026/25280

This script is safe to run multiple times (idempotent).
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mapping: old dummy code → new real code
# Update the OLD codes to match what's actually in your database
CODE_MIGRATION = {
    # "SKS-2026-00001": "SL/WB/110223/2026/25275",
    # "SKS-2026-00002": "SL/WB/110223/2026/25279",
    # "SKS-2026-00003": "SL/WB/110223/2026/25280",
}


def main():
    from app import create_app
    from app.extensions import db
    from app.models import Sample

    if not CODE_MIGRATION:
        print("ERROR: CODE_MIGRATION is empty. Update the mapping in this script first.")
        print("Open scripts/migrate_sample_codes.py and fill in the old→new code pairs.")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        updated = 0
        skipped = 0

        for old_code, new_code in CODE_MIGRATION.items():
            sample = Sample.query.filter_by(sample_code=old_code).first()
            if not sample:
                print(f"  SKIP: No sample found with code '{old_code}'")
                skipped += 1
                continue

            # Check new code doesn't already exist
            conflict = Sample.query.filter_by(sample_code=new_code).first()
            if conflict:
                print(f"  SKIP: Code '{new_code}' already exists (sample id={conflict.id})")
                skipped += 1
                continue

            print(f"  MIGRATE: '{old_code}' → '{new_code}' (sample id={sample.id})")
            sample.sample_code = new_code
            updated += 1

        if updated:
            db.session.commit()
            print(f"\nDone: {updated} sample(s) updated, {skipped} skipped.")
        else:
            print(f"\nNothing to do: {skipped} skipped.")


if __name__ == "__main__":
    main()
