"""Cross-module case resolution utility.

Extracted to eliminate the three independent, duplicated implementations of
"resolve whether a numeric ID is a CaseFile or an Adjudication" that existed
across the document viewer, version control, and other route modules.

The single disambiguation algorithm lives here so that all callers share one
source of truth and a single database hit per table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from app.models import Adjudication, CaseFile

from app.extensions import db


@dataclass
class ResolvedCase:
    """Result of resolving a numeric ID to a ``CaseFile`` or ``Adjudication``.

    Exactly one of ``case_id`` / ``adjudication_id`` is non-``None`` (or
    ``None`` for both when the ID does not exist in either table).
    """

    case_id: Optional[int]
    adjudication_id: Optional[int]
    case_type: str  # "case_file" | "adjudication"
    case_number: str
    record: Optional[Union["CaseFile", "Adjudication"]]


class CaseResolver:
    """Resolve an integer ID to either a :class:`CaseFile` or :class:`Adjudication`.

    When ``kind`` is omitted, ``CaseFile`` is tried first, then
    ``Adjudication`` — matching the original ``_resolve_case`` contract.

    When ``kind`` is ``"case_file"`` or ``"adjudication"``, only that table
    is consulted, which is critical when the caller already knows which
    blueprint/route the ID originated from (each table has its own
    autoincrement, so IDs can collide across tables).
    """

    @staticmethod
    def resolve(id: int, kind: Optional[str] = None) -> Optional[ResolvedCase]:
        """Resolve ``id`` to a :class:`ResolvedCase`.

        Args:
            id: The primary-key value to look up.
            kind: Optional disambiguation hint — ``"case_file"`` or
                ``"adjudication"``.  When ``None`` (default) ``CaseFile``
                is checked first, then ``Adjudication``.

        Returns:
            A :class:`ResolvedCase` when the record exists in either table,
            or ``None`` when the ID is not found.
        """
        from app.models import Adjudication, CaseFile

        if kind == "adjudication":
            record = db.session.get(Adjudication, id)
            if record is not None:
                return ResolvedCase(
                    case_id=None,
                    adjudication_id=id,
                    case_type="adjudication",
                    case_number=record.case_number or "",
                    record=record,
                )
            return None

        if kind == "case_file":
            record = db.session.get(CaseFile, id)
            if record is not None:
                return ResolvedCase(
                    case_id=id,
                    adjudication_id=None,
                    case_type="case_file",
                    case_number=record.case_number or "",
                    record=record,
                )
            return None

        record = db.session.get(CaseFile, id)
        if record is not None:
            return ResolvedCase(
                case_id=id,
                adjudication_id=None,
                case_type="case_file",
                case_number=record.case_number or "",
                record=record,
            )

        record = db.session.get(Adjudication, id)
        if record is not None:
            return ResolvedCase(
                case_id=None,
                adjudication_id=id,
                case_type="adjudication",
                case_number=record.case_number or "",
                record=record,
            )

        return None
