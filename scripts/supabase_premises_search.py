"""CLI: query the FSSAI registry for premises by free text.

Thin wrapper over ``PremisesResolver`` so the registry is queried by exactly
one code path (see app/services/premises_resolver.py). Reads
``SUPABASE_DB_URL`` from the environment/config seam.

    python scripts/supabase_premises_search.py "balaram mullick in NSC Bose Road"
"""

from __future__ import annotations

import sys

from app.services.premises_resolver import resolve


def search_premises(text: str) -> list:
    """Return candidates for ``text`` (never raises; check ``error`` on failure)."""
    return list(resolve(text).candidates)


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "devyani"
    result = resolve(query)
    if result.error:
        print(f"Lookup failed for {query!r}: {result.error}", file=sys.stderr)
        return 1
    if not result.candidates:
        print(f"No results for {query!r}")
        return 0
    print(f"{len(result.candidates)} result(s) for {query!r}:")
    for cand in result.candidates:
        print(f"  [{cand.source_table}] {cand.fssai_number}  {cand.company_name}")
        print(f"      {cand.full_address or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
