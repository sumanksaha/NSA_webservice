"""Document viewer and editor package.

Provides shared rendering helpers for previewing and editing legal documents
(Petition and Permission Letter) in the browser via Quill rich-text editor.
"""

from flask import Blueprint

document_viewer_bp = Blueprint(
    "document_viewer",
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Import routes after blueprint is defined so the route decorators register.
# This also makes the GET editor and POST save routes available.
from app.document_viewer import routes  # noqa: F401
