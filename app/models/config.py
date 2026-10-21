"""Application configuration models."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.extensions import db


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
