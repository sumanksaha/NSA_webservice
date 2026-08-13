"""Case query service — lightweight lookups for CaseFile / Adjudication.

Extracted from :class:`~app.shared.document_case_manager.DocumentCaseManager`
so that callers needing *only* lookups (resolve-by-ID, resolve-by-number,
list-all) can avoid the 5-callback constructor of the full
``DocumentCaseManager``.

Typical usage::

    from app.shared.case_query_service import CaseQueryService

    # From a route that just needs to resolve a case:
    svc = CaseQueryService(CaseFile, case_type="case_file")
    case = svc.get_case_by_number("CASE-2026-001")
    cases = svc.list_cases()

The service is also used internally by ``DocumentCaseManager``, which delegates
its public ``get_case`` / ``get_case_by_number`` / ``list_cases`` methods to a
``CaseQueryService`` instance constructed during ``__init__``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.extensions import db

logger = logging.getLogger(__name__)


class CaseQueryService:
    """Stateless read-only query layer for CaseFile and Adjudication models.

    Parameters
    ----------
    model : type
        SQLAlchemy model class — either ``CaseFile`` or ``Adjudication``.
    case_type : str
        ``"case_file"`` or ``"adjudication"`` — drives ``_case_summary``
        field selection.
    """

    def __init__(self, model: type, case_type: str) -> None:
        self.model = model
        self.case_type = case_type

    def get_case(self, case_id: int) -> Any | None:
        """Retrieve a case by primary key."""
        return db.session.get(self.model, case_id)

    def get_case_by_number(self, case_number: str) -> Any | None:
        """Retrieve a case by case number."""
        return self.model.query.filter_by(case_number=case_number).first()

    def list_cases(self) -> list[dict]:
        """Return all cases as summary dicts, newest first."""
        cases = self.model.query.order_by(self.model.created_at.desc()).all()
        return [self.case_summary(c) for c in cases]

    def case_summary(self, case: Any) -> dict:
        """Return a summary dict for *case* — field selection by ``case_type``."""
        if self.case_type == "case_file":
            return {
                "id": case.id,
                "case_number": case.case_number,
                "product_name": case.product_name,
                "manufacturer_name": case.manufacturer_name,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
        return {
            "id": case.id,
            "case_number": case.case_number,
            "fbo_name": case.fbo_name,
            "food_safety_officer": case.food_safety_officer,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        }


__all__ = ["CaseQueryService"]
