"""Autopopulation blueprint (Phase C) — prefill bundles at ``/autopopulation``."""

from flask import Blueprint

autopopulation_bp = Blueprint(
    "autopopulation",
    __name__,
    template_folder="templates",
)

from app.autopopulation import routes  # noqa: F401
