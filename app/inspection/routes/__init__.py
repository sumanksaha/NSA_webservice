"""Inspection route package.

Routes are split into focused submodules:
- inspection_routes — CRUD and list/index views
- lookup_routes — FSSAI / CE license lookup endpoints
- derived_views — open issues, pending, history, dismissal, adjudication linking
- photo_routes — photo evidence upload, download, delete, listing
"""

from app.inspection import inspection_bp  # noqa: F401  (re-export for callers)
from app.inspection.routes import (  # noqa: F401
    derived_views,
    inspection_routes,
    lookup_routes,
    photo_routes,
)
