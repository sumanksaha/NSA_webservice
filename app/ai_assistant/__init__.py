"""AI Assistant blueprint for NSA_webservice.

Provides an AI-powered assistant integrated into the document editor,
offering summarization, legal-language refinement, contradiction detection,
missing-annexure suggestions, and prayer drafting.

Routes are prefixed at ``/ai-assistant``.  The LLM API key and provider are
read from Flask config (set in ``create_app`` from environment variables), so
the module is dormant by default — no API key means 503 responses, not
crashes.
"""

from flask import Blueprint

ai_bp = Blueprint(
    "ai_assistant",
    __name__,
    url_prefix="/ai-assistant",
)

# Import routes after the blueprint is defined so route decorators register
# (same pattern as app/validation/__init__.py and app/food_cell/__init__.py).
from app.ai_assistant import routes  # noqa: F401
