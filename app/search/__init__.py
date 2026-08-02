"""Search module for NSA Webservice.

Provides full-text search via SQLite FTS5 across case files, adjudications,
annexures, and evidence records.

On PostgreSQL the FTS5 virtual table is not created; the search API falls
back to ``LIKE`` queries on the regular tables so the endpoint is always
available regardless of the database engine.
"""

from flask import Blueprint

search_bp = Blueprint(
    "search",
    __name__,
    template_folder="templates",
)

# Import routes after blueprint is defined so the route decorators register.
from app.search import routes  # noqa: F401
