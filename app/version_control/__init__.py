"""Version Control blueprint (Phase 9).

Exposes the ``version_control_bp`` API blueprint from
``app.version_control.routes`` — snapshot-on-save, compare, restore,
branch, and history endpoints under ``/api/version-control``.
"""

from app.version_control.routes import version_control_bp

__all__ = ["version_control_bp"]
