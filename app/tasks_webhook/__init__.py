from flask import Blueprint

tasks_webhook_bp = Blueprint("tasks_webhook", __name__)

from app.tasks_webhook import routes  # noqa: F401
