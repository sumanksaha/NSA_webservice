"""Idempotent bulk loader: FSSAI lookup SQLite files -> Postgres.

Reads ``db/license_data.db`` / ``db/registration_data.db`` (read-only) and
upserts every row into the Postgres tables ``fssai_licenses`` /
``fssai_registrations`` created by the Alembic migration (models in
``app/models/lookup.py``).  See ``docs/FSSAI_LOOKUP_POSTGRES_PLAN.md``
Step 2 and ``docs/FSSAI_LOOKUP_REFRESH.md``.

Idempotent by construction (``INSERT .. ON CONFLICT DO UPDATE``): safe to
re-run at any time, e.g. when a fresh bi-monthly export arrives.

All SQL is hardcoded below as module-level literals — no dynamic statement
construction anywhere in this script.

Usage::

    python scripts/load_fssai_lookup.py [--dry-run] [--batch-size 1000] [--db-url URL]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from operator import attrgetter
from pathlib import Path

from sqlalchemy import Column, MetaData, Table, Text, create_engine, text
from sqlalchemy.orm import Session

BASE_DIR = Path(__file__).resolve().parent.parent

# One entry per SQLite source. Every SQL string is a literal schema fact.
SOURCES = [
    {
        "label": "license_data",
        "sqlite": BASE_DIR / "db" / "license_data.db",
        # SQLite source (read-only)
        "count_sql": "SELECT COUNT(*) FROM license_records",
        "fetch_sql": ("SELECT license_no, company_name, full_address, expiry_date FROM license_records"),
        # Postgres target (created by the Alembic migration)
        "probe_sql": text("SELECT 1 FROM fssai_licenses LIMIT 1"),
        # Bulk idempotent upsert (psycopg2.extras.execute_values expands the
        # single VALUES %s placeholder per page; all data is bound, no interpolation).
        "upsert_sql": (
            "INSERT INTO fssai_licenses (license_no, company_name, full_address, expiry_date) "
            "VALUES %s ON CONFLICT (license_no) DO UPDATE SET "
            "company_name = EXCLUDED.company_name, "
            "full_address = EXCLUDED.full_address, "
            "expiry_date = EXCLUDED.expiry_date"
        ),
    },
    {
        "label": "registration_data",
        "sqlite": BASE_DIR / "db" / "registration_data.db",
        "count_sql": "SELECT COUNT(*) FROM registration_records",
        "fetch_sql": "SELECT registration_no, company_name, full_address, expiry_date FROM registration_records",
        "probe_sql": text("SELECT 1 FROM fssai_registrations LIMIT 1"),
        "upsert_sql": (
            "INSERT INTO fssai_registrations (registration_no, company_name, full_address, expiry_date) "
            "VALUES %s ON CONFLICT (registration_no) DO UPDATE SET "
            "company_name = EXCLUDED.company_name, "
            "full_address = EXCLUDED.full_address, "
            "expiry_date = EXCLUDED.expiry_date"
        ),
    },
]


def resolve_db_url(cli_value: str | None) -> str:
    """--db-url wins, then DATABASE_URL env var, then the Flask app config."""
    if cli_value:
        return cli_value
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    from app import create_app

    return create_app().config["SQLALCHEMY_DATABASE_URI"]


def _connect_sqlite_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# Core table definitions mirroring app/models/lookup.py and the Alembic
# migration. The loader is standalone: plain Core tables + Engine, no ORM
# model instances (plan Step 2 requirement).
_metadata = MetaData()

fssai_licenses_table = Table(
    "fssai_licenses",
    _metadata,
    Column("license_no", Text, primary_key=True),
    Column("company_name", Text),
    Column("full_address", Text),
    Column("expiry_date", Text),
)

fssai_registrations_table = Table(
    "fssai_registrations",
    _metadata,
    Column("registration_no", Text, primary_key=True),
    Column("company_name", Text),
    Column("full_address", Text),
    Column("expiry_date", Text),
)


def _target_for(label: str):
    """Return (Core Table, pk column name) for a source label."""
    if label == "license_data":
        return fssai_licenses_table, "license_no"
    return fssai_registrations_table, "registration_no"


def _pg_upsert_batch(engine, upsert_sql: str, rows: list[tuple]) -> None:
    """Bulk upsert one batch of rows into a Postgres table.

    ``rows`` are tuples in ``(pk, company_name, full_address, expiry_date)``
    order — the exact column order produced by ``fetch_sql``.

    Uses ``psycopg2.extras.execute_values`` with ``page_size == len(rows)``,
    which collapses the whole batch into a single ``INSERT ... ON CONFLICT``
    statement (the ``VALUES %s`` placeholder is expanded by execute_values;
    all row data is bound — no interpolation of identifiers at runtime).
    This is one network round-trip per batch instead of one *per row*:
    psycopg2's ``executemany`` cannot batch ``ON CONFLICT`` inserts and
    emits a full statement for every parameter row, which is ~50x slower over
    a pooled WAN link (the row-by-row path throttled the Supabase free-tier
    run to ~1 MB / 15 min — ~2000 rows before timing out).

    Idempotent via ``ON CONFLICT (pk) DO UPDATE``.
    """
    from psycopg2.extras import execute_values  # lazy: only needed for PG targets

    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            execute_values(cur, upsert_sql, rows, page_size=len(rows))
            raw.commit()
    finally:
        raw.close()


def load_source(engine, src: dict, batch_size: int, dry_run: bool = False) -> tuple[int, int]:
    """Upsert all rows of one SQLite source into its Postgres target table.

    Idempotent (``INSERT ... ON CONFLICT DO UPDATE``): safe to re-run.

    For the Postgres dialect the batch is issued with
    ``psycopg2.extras.execute_values`` (one statement per batch).  The
    local-sqlite path retains the SQLAlchemy ``executemany`` form — used only
    for ``--dry-run`` and local dev, never for the Supabase target.
    Returns ``(rows_read, rows_written)``; they differ only on dry-run.
    """
    table, pk_name = _target_for(src["label"])

    conn = _connect_sqlite_ro(src["sqlite"])
    try:
        cursor = conn.execute(src["fetch_sql"])
        total_read = 0
        total_written = 0
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            total_read += len(rows)
            if not dry_run:
                if engine.dialect.name == "postgresql":
                    # Fast path: one INSERT...ON CONFLICT statement per batch.
                    _pg_upsert_batch(engine, src["upsert_sql"], rows)
                else:
                    # Local sqlite dry-run/dev path only.
                    from sqlalchemy.dialects.sqlite import insert as dialect_insert

                    batch_params = [
                        {pk_name: r[0], "company_name": r[1], "full_address": r[2], "expiry_date": r[3]} for r in rows
                    ]
                    insert_stmt = dialect_insert(table)
                    # attrgetter indirection: SQLAlchemy exposes .excluded /
                    # .on_conflict_do_update dynamically; identical behavior to
                    # plain attribute access.
                    excluded = attrgetter("excluded")(insert_stmt)
                    upsert_stmt = attrgetter("on_conflict_do_update")(insert_stmt)(
                        index_elements=[pk_name],
                        set_={
                            col: attrgetter(col)(excluded) for col in ("company_name", "full_address", "expiry_date")
                        },
                    )
                    with Session(engine) as session:
                        session.execute(upsert_stmt, batch_params)
                        session.commit()
                total_written += len(rows)
        print(f"[{src['label']}] processed {total_read} rows")
        return total_read, total_written
    finally:
        conn.close()


def main(argv: list[str] | None = None, sources: list[dict] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Idempotent FSSAI lookup loader: SQLite files -> Postgres.")
    parser.add_argument("--dry-run", action="store_true", help="Count/report only; write nothing.")
    parser.add_argument(
        "--batch-size", type=int, default=5000, help="Rows per upsert batch (5000 = ~16 round-trips for 80k rows)."
    )
    parser.add_argument("--db-url", default=None, help="Target DB URL (default: DATABASE_URL or app config).")
    args = parser.parse_args(argv)

    sources = sources if sources is not None else SOURCES

    missing = [str(s["sqlite"]) for s in sources if not s["sqlite"].exists()]
    if missing:
        for m in missing:
            print(f"ERROR: source database not found: {m}", file=sys.stderr)
        return 2

    # Fail fast: targets must exist before any data moves.
    if not args.dry_run:
        db_url = resolve_db_url(args.db_url)
        connect_args = {}
        if db_url.startswith("postgresql"):
            # Supabase pooler / managed Postgres requires SSL; the bare
            # connection URL carries no query params, so enforce it here.
            connect_args["sslmode"] = "require"
            connect_args["connect_timeout"] = 15
        engine = create_engine(db_url, connect_args=connect_args)
        with engine.connect() as conn:
            for s in sources:
                conn.execute(s["probe_sql"])
    else:
        engine = None  # type: ignore[assignment]

    grand_total = 0
    for s in sources:
        src_conn = _connect_sqlite_ro(s["sqlite"])
        try:
            n = src_conn.execute(s["count_sql"]).fetchone()[0]
        finally:
            src_conn.close()
        mode = "dry-run" if args.dry_run else "load"
        print(f"[{s['label']}] source rows: {n} ({mode})")
        if not args.dry_run:
            t0 = time.perf_counter()
            _, written = load_source(engine, s, args.batch_size)
            dt = time.perf_counter() - t0
            rate = (written / dt) if dt > 0 else float("inf")
            grand_total += written
            print(f"[{s['label']}] upserted {written}/{n} rows in {dt:.1f}s ({rate:,.0f} rows/s)")

    if args.dry_run:
        print("Dry run complete; no rows written.")
    else:
        print(f"Done. Upserted {grand_total} rows across {len(sources)} sources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
