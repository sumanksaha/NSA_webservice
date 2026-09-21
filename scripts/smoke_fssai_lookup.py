"""One-shot READ-ONLY smoke check: does lookup_fssai resolve a real FSSAI number?

Exercises the same code path as POST /case_file_generator/lookup_fssai
(app.utils.lookup.lookup_fssai -> Postgres PK get) against the environment's
configured DATABASE_URL. No writes, no server boot.

Usage: venv/bin/python scripts/smoke_fssai_lookup.py <fssai_no>
"""

import sys

sys.path.insert(0, ".")

from app import create_app  # noqa: E402
from app.utils.lookup import lookup_fssai  # noqa: E402


def main() -> int:
    license_no = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not license_no:
        print("usage: smoke_fssai_lookup.py <fssai_no>")
        return 2

    app = create_app()
    with app.app_context():
        result = lookup_fssai(license_no)

    if result.error:
        print(f"ERROR: {result.error}")
        return 1
    if not result.found:
        print("NOT FOUND (no error)")
        return 1

    print("FOUND:")
    for key, value in result.data.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
