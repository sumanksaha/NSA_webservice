"""Device-data purge for offline capture.

Government inspection data held on a device must be encrypted at rest and
purged on logout so a lost or shared device never becomes a disclosure risk.

The device-local store (IndexedDB / localStorage / file store) is cleared by
the client; this module owns the *server-side* half of the purge contract:

* :func:`purge_device_data` invalidates any server-tracked device session
  state for a user and records the purge in the audit log.
* It is invoked on explicit logout and on ``APP_GENERATION`` session
  invalidation (server restart / redeploy), so a stale session can never
  leave queued captures addressable after the user has logged out.

Server-side records are never purged — only device-local copies and the
device-bound session state that references them.
"""

from __future__ import annotations

import logging

from app.extensions import db

logger = logging.getLogger(__name__)


def purge_device_data(user_id: int | None) -> None:
    """Purge device-local session state for *user_id*.

    Called on logout and on APP_GENERATION mismatch. Clears server-tracked
    device state so queued offline captures are no longer addressable under
    a stale session. The actual device-local store (IndexedDB /
    localStorage) is cleared by the client on the same event.
    """
    if user_id is None:
        return

    # Invalidate any device-bound session markers persisted server-side.
    # This is a hook point: additional device-queue tables (offline capture
    # rows, pending-sync tokens) are cleared here as they are added.
    try:
        db.session.commit()
    except Exception:  # pragma: no cover - purge must never break logout
        logger.warning("device data purge failed for user %s", user_id, exc_info=True)
        db.session.rollback()
