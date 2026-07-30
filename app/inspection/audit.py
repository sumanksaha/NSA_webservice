"""Re-export audit-log functions from the shared services module.

Kept here for backward compatibility — ``inspection/routes.py`` imports
``log_audit`` from this module and continues to work unchanged.
"""

# flake8: noqa: F401
from app.services.audit import compute_hash, log_audit
