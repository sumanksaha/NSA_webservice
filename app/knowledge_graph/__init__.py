"""Knowledge Graph module (plan.md Phase 14).

Extracts entity-relationship graphs from case documents and renders them
as an interactive Cytoscape.js node-link diagram.

Blueprint prefix: ``/knowledge-graph``
"""

from flask import Blueprint

kg_bp = Blueprint(
    "knowledge_graph",
    __name__,
    url_prefix="/knowledge-graph",
    template_folder="templates",
)

# Import routes after blueprint is defined so the route decorators register.
from app.knowledge_graph import routes  # noqa: F401
