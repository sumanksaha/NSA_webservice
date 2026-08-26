"""Work Diary engine.

Builds per-FSO diary rows from :class:`~app.models.inspection.Inspection`
records.  The engine is deliberately read-only: the diary *accumulates*
inspections that FSOs already enter through the Inspection tab — no
duplicate data entry, no separate persistence layer.

Row contract (fixed format):
    - ``date``           — ``Inspection.inspection_date``
    - ``place_of_visit`` — ``Inspection.fbo_address`` (falls back to the
      FBO name when no address was recorded)
    - ``purpose``        — always ``"Routine Inspection"`` or ``"Complaint"``;
      derived from whether the inspection records a ``problem``
    - ``activity``       — human-readable activity line built from the
      purpose + FBO/problem context
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.extensions import db
from app.models import FSO, Inspection
from app.utils.filters import parse_date

PURPOSE_ROUTINE = "Routine Inspection"
PURPOSE_COMPLAINT = "Complaint"


class WorkDiaryEngine:
    """Query + shape Inspections into work-diary rows."""

    def build_entries(
        self,
        fso_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        purpose: str | None = None,
        include_dismissed: bool = False,
    ) -> list[dict[str, Any]]:
        """Return diary rows sorted by inspection date (oldest first).

        Args:
            fso_name: Restrict to one FSO (the per-FSO view).
            date_from / date_to: Inclusive ISO-date strings (YYYY-MM-DD).
            purpose: Optional filter — ``"routine"`` or ``"complaint"``;
                anything else means "all".
            include_dismissed: Dismissed inspections are excluded by default.
        """
        query = db.session.query(Inspection).join(FSO, Inspection.fso_name == FSO.fso_name)

        if fso_name:
            query = query.filter(Inspection.fso_name == fso_name)

        parsed_from = parse_date(date_from) if date_from else None
        if parsed_from:
            query = query.filter(Inspection.inspection_date >= parsed_from)

        parsed_to = parse_date(date_to) if date_to else None
        if parsed_to:
            # Make an upper-bound date inclusive of the whole day.
            end_of_day = datetime.combine(parsed_to.date(), parsed_to.time().max)
            query = query.filter(Inspection.inspection_date <= end_of_day)

        if not include_dismissed:
            query = query.filter((Inspection.is_dismissed.is_(False)) | (Inspection.is_dismissed.is_(None)))

        if purpose == "complaint":
            query = query.filter(
                db.or_(
                    Inspection.visit_purpose == "complaint",
                    db.and_(
                        Inspection.visit_purpose.is_(None),
                        Inspection.problem.isnot(None),
                        Inspection.problem != "",
                    ),
                )
            )
        elif purpose == "routine":
            query = query.filter(
                db.or_(
                    Inspection.visit_purpose == "routine",
                    db.and_(
                        Inspection.visit_purpose.is_(None),
                        db.or_(Inspection.problem.is_(None), Inspection.problem == ""),
                    ),
                )
            )

        inspections = query.order_by(Inspection.inspection_date.asc(), Inspection.id.asc()).all()
        return [self._to_entry(insp) for insp in inspections]

    @staticmethod
    def derive_purpose(problem: str | None, visit_purpose: str | None = None) -> str:
        """Map an Inspection to its diary purpose.

        Preference order:
        1. The FSO's explicit ``visit_purpose`` pick at entry time
           (``"routine"`` / ``"complaint"``) — authoritative.
        2. Legacy heuristic fallback for rows entered before the field
           existed: a recorded ``problem`` means the visit originated from
           a complaint; anything else is routine.
        """
        if visit_purpose == "complaint":
            return PURPOSE_COMPLAINT
        if visit_purpose == "routine":
            return PURPOSE_ROUTINE
        if problem and problem.strip():
            return PURPOSE_COMPLAINT
        return PURPOSE_ROUTINE

    def _to_entry(self, insp: Inspection) -> dict[str, Any]:
        purpose = self.derive_purpose(insp.problem, insp.visit_purpose)
        place = (insp.fbo_address or "").strip() or (insp.fbo_name or "").strip() or "\u2014"
        if purpose == PURPOSE_COMPLAINT:
            problem_brief = (insp.problem or "").strip()
            activity = f"Enquiry into complaint ({problem_brief})" if problem_brief else "Enquiry into complaint"
        else:
            subject = (insp.fbo_name or "").strip() or "food premises"
            activity = f"Routine inspection of {subject}"
        return {
            "inspection_id": insp.id,
            "inspection_code": insp.inspection_code,
            "fso_name": insp.fso_name,
            "date": insp.inspection_date,
            "place_of_visit": place,
            "purpose": purpose,
            "activity": activity,
        }
