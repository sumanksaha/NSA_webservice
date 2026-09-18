"""Tasks for the Food Cell module.

``send_do_intimation`` — dispatched via QStash after an FSO saves sample
data (synchronous inline fallback); renders the DO intimation HTML + PDF
and forwards to the Food Cell.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def send_do_intimation(sample_id: int) -> int | None:
    """Generate and forward the DO intimation for *sample_id*.

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
