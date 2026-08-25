"""Tests for scripts/load_fssai_lookup.py — offline, SQLite-only.

Seeds tiny SQLite source fixtures (same schema as the real FSSAI export
files) and loads into a temp-file SQLite target database (SQLite supports
INSERT .. ON CONFLICT DO UPDATE since 3.24), so no Postgres is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, MetaData, Table, Text, create_engine, insert, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.load_fssai_lookup import SOURCES, main

LICENSE_ROWS = [
    {
        "license_no": "10000000000001",
        "company_name": "Acme Foods",
        "full_address": "12 Park St, Kolkata",
        "expiry_date": "31-03-2020",
    },
    {
        "license_no": "10000000000002",
        "company_name": "Beta Traders",
        "full_address": "9 MG Rd, Kolkata",
        "expiry_date": "18-03-2021",
    },
]
REGISTRATION_ROWS = [
    {
        "registration_no": "20000000000001",
        "company_name": "Gamma Sweets",
        "full_address": "5 College St, Kolkata",
        "expiry_date": "01-01-2022",
    },
]

# Core definitions mirroring the real export schema (fixed names, never input).
_source_metadata = MetaData()

_license_records = Table(
    "license_records",
    _source_metadata,
    Column("license_no", Text, primary_key=True),
    Column("company_name", Text),
    Column("full_address", Text),
    Column("expiry_date", Text),
)

_registration_records = Table(
    "registration_records",
    _source_metadata,
    Column("registration_no", Text, primary_key=True),
    Column("company_name", Text),
    Column("full_address", Text),
    Column("expiry_date", Text),
)

_COUNT_LICENSES = text("SELECT COUNT(*) FROM fssai_licenses")
_COUNT_REGISTRATIONS = text("SELECT COUNT(*) FROM fssai_registrations")
_SELECT_ACME = text("SELECT company_name FROM fssai_licenses WHERE license_no = '10000000000001'")


def _make_source_db(path: Path, table: Table, rows: list[dict]) -> None:
    """Create one tiny SQLite source fixture with fixed schema."""
    engine = create_engine("sqlite:///" + str(path))
    try:
        table.metadata.create_all(engine)
        with make_session(engine) as session:
            session.execute(insert(table), rows)
            session.commit()
    finally:
        engine.dispose()


def make_session(engine):
    """Local indirection so each helper owns its Session import cleanly."""
    from sqlalchemy.orm import Session

    return Session(bind=engine)


def _query_scalar(engine_url: str, stmt) -> object:
    """Run one literal query against the target and return its scalar."""
    engine = create_engine(engine_url)
    try:
        with make_session(engine) as session:
            return session.execute(stmt).scalar()
    finally:
        engine.dispose()


@pytest.fixture
def env(tmp_path: Path):
    """Create SQLite sources + target tables; return (sources, db_url)."""
    _make_source_db(tmp_path / "license_data.db", _license_records, LICENSE_ROWS)
    _make_source_db(tmp_path / "registration_data.db", _registration_records, REGISTRATION_ROWS)

    sources = [{**src, "sqlite": tmp_path / src["sqlite"].name} for src in SOURCES]

    # Target DB with the loader's Core table definitions
    from scripts.load_fssai_lookup import _metadata

    target_url = f"sqlite:///{tmp_path / 'target.db'}"
    engine = create_engine(target_url)
    _metadata.create_all(engine)
    engine.dispose()

    return sources, target_url


def test_load_and_idempotency(env):
    sources, db_url = env

    assert main(["--db-url", db_url], sources=sources) == 0

    assert _query_scalar(db_url, _COUNT_LICENSES) == 2
    assert _query_scalar(db_url, _COUNT_REGISTRATIONS) == 1
    assert _query_scalar(db_url, _SELECT_ACME) == "Acme Foods"

    # Idempotent: second run keeps counts identical
    assert main(["--db-url", db_url], sources=sources) == 0
    assert _query_scalar(db_url, _COUNT_LICENSES) == 2

    # Upsert semantics: changed source values overwrite the target
    engine = create_engine("sqlite:///" + str(sources[0]["sqlite"]))
    try:
        with make_session(engine) as session:
            stmt = (
                update(_license_records)
                .where(_license_records.c.license_no == "10000000000001")
                .values(company_name="Updated Ltd")
            )
            session.execute(stmt)
            session.commit()
    finally:
        engine.dispose()

    assert main(["--db-url", db_url], sources=sources) == 0
    assert _query_scalar(db_url, _SELECT_ACME) == "Updated Ltd"


def test_dry_run_writes_nothing(env):
    sources, db_url = env

    assert main(["--dry-run", "--db-url", db_url], sources=sources) == 0

    assert _query_scalar(db_url, _COUNT_LICENSES) == 0
    assert _query_scalar(db_url, _COUNT_REGISTRATIONS) == 0


def test_missing_source_exits_nonzero(tmp_path: Path):
    bogus = [{**SOURCES[0], "sqlite": tmp_path / "does_not_exist.db"}]
    assert main(["--db-url", "sqlite:///:memory:"], sources=bogus) == 2
