"""Feedback dashboard blueprint (Phase D).

Per-field OCR accuracy metrics derived from :class:`OCRCorrection` rows, plus
a manual trigger for the ``refresh_few_shot_examples`` Celery task that feeds
corrected examples back into the Vision-LLM extraction prompts.
"""

from flask import Blueprint

feedback_dashboard_bp = Blueprint(
    "feedback_dashboard",
    __name__,
    template_folder="templates",
)

from app.feedback_dashboard import routes  # noqa: F401
