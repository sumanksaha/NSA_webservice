"""Application configuration models."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.extensions import db


class AppSecret(db.Model):
    """Key/value store for bootstrap secrets persisted in the database.

    Currently used to auto-provision a stable ``SECRET_KEY`` in production
    when the env var is missing (Render only mints ``generateValue`` secrets
    when the env var is first created). Stored via raw SQL in
    ``app/__init__.py::_load_or_create_production_secret_key`` before the
    Flask-SQLAlchemy models are wired, so this model exists mainly so schema
    tooling (Alembic migrations, parity/verify scripts, ``db.create_all()``)
    knows the table. Prefer an env var / managed secret over this store.
    """

    __tablename__ = "app_secrets"

    name = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)


class Settings(db.Model):
    """Key/value store for application-level configuration.

    Replaces ad-hoc env-var lookups for non-secret runtime config
    (e.g. items-per-page, default PDF orientation).  Secrets such as
    SECRET_KEY continue to live in the ``app_secrets`` table / env vars.
    """

    __tablename__ = "settings"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    value_type = db.Column(
        db.String(20),
        nullable=False,
        default="string",
        comment="string|int|float|bool|json",
    )
    description = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self):
        return f"<Settings {self.key}={self.value}>"

    @classmethod
    def get(cls, key, default=None):
        """Retrieve a setting value, cast to the declared type."""
        obj = db.session.get(cls, key)
        if obj is None:
            return default
        if obj.value_type == "int":
            return int(obj.value) if obj.value else default
        if obj.value_type == "float":
            return float(obj.value) if obj.value else default
        if obj.value_type == "bool":
            return obj.value.lower() in ("1", "true", "yes") if obj.value else False
        if obj.value_type == "json":
            return json.loads(obj.value) if obj.value else default
        return obj.value
