"""Guardrail: any destructive DB operation is blocked (logs + raises)."""

import logging

from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)
BLOCKED_PATTERNS = ("TRUNCATE", "DROP TABLE", "DELETE FROM")


def install_guard(eng: Engine) -> None:
    @event.listens_for(eng, "before_execute")
    def guard_destroy(conn, clause, params, execution_options):
        sql_text = str(clause) if clause else ""
        for p in BLOCKED_PATTERNS:
            if p in sql_text.upper():
                msg = f"GUARD BLOCKED destructive SQL: {sql_text[:200]}"
                logger.critical(msg)
                raise RuntimeError(msg)

    logger.info("DB guardrail installed — destructive SQL blocked.")
