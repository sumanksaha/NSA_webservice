"""Celery tasks for the Food Cell module.

``send_do_intimation`` — enqueued after an FSO saves sample data; renders
the DO intimation HTML + PDF and forwards to the Food Cell.
"""

from __future__ import annotations

import logging

# Lazy import so the module boots even when Celery isn't installed.
try:
    from celery_app import celery
except ImportError:
    celery = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def send_do_intimation(self, sample_id: int) -> int | None:
    """Generate and forward the DO intimation for *sample_id* (Celery task).

    Safe to call even if the sample no longer exists; logs and returns None.

    Returns
    -------
    int | None
        The ``DoIntimation.id`` on success, None if the sample was not found.
    """
    logger.info("send_do_intimation: starting for sample_id=%s", sample_id)
    from app.food_cell.services import generate_and_forward_do_intimation
    intimation = generate_and_forward_do_intimation(sample_id)
    if intimation is None:
        logger.warning("send_do_intimation: sample %s not found", sample_id)
        return None
    logger.info("send_do_intimation: completed for sample_id=%s (intimation_id=%s)", sample_id, intimation.id)
    return intimation.id


# Register as Celery task if celery is available
if celery is not None:
    send_do_intimation = celery.task(bind=True, name="food_cell.send_do_intimation")(send_do_intimation)  # type: ignore[assignment]
