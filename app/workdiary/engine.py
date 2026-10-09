"""Work Diary engine.

Builds per-FSO diary rows from Monthly Diary rows (``WorkDiaryEntry``,
written in ``/diary/bulk``). A calendar day holds up to two duty slots
(``duty_seq`` 1|2) — a **split-duty day** renders as two rows with the Date
cell merged (``rowspan``) and Place / Purpose / Activity kept separate.

The interactive ``/workdiary/`` index unions Inspection rows (read-only,
accumulated from the Inspection tab) with Monthly rows; the official
PDF/preview report (``include_inspections=False``) builds Monthly-only, as
numeric inspection values are entered through the Monthly Diary itself.

Row contract (fixed format):
    - ``date``           — the Monthly row's ``work_date`` (parsed to
      datetime); Inspections use ``Inspection.inspection_date`` when included
    - ``place_of_visit`` — Inspection: ``fbo_address`` (fallback FBO name);
      Monthly: the per-duty Place of Visit input (fallback ``—``)
    - ``purpose``        — Inspections: ``"Routine Inspection"`` / ``"Complaint"``;
      Monthly: ``"VVIP duty"`` / ``"Meeting"`` / ``"Inspection"``
      (see ``app.diary.derive_diary_purpose``)
    - ``activity``       — human-readable activity line
"""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from typing import Any

from sqlalchemy import exc as sa_exc

from app.diary import (
    DIARY_PURPOSE_INSPECTION,
    DIARY_PURPOSE_MEETING,
    DIARY_PURPOSE_VVIP,
    derive_diary_purpose,
    print_activity,
)
from app.extensions import db
from app.models import FSO, Inspection
from app.utils.filters import parse_date

logger = logging.getLogger(__name__)

PURPOSE_ROUTINE = "Routine Inspection"
PURPOSE_COMPLAINT = "Complaint"
#: Monthly-row purposes, single-sourced from app.diary (the owner of the
#: Monthly Diary vocabulary) so a rename can't silently empty the filters.
PURPOSE_VVIP = DIARY_PURPOSE_VVIP
PURPOSE_MEETING = DIARY_PURPOSE_MEETING
PURPOSE_INSPECTION = DIARY_PURPOSE_INSPECTION


class WorkDiaryEngine:
    """Query + shape Inspections into work-diary rows."""

    def build_entries(
        self,
        fso_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        purpose: str | None = None,
        include_dismissed: bool = False,
        include_inspections: bool = True,
    ) -> list[dict[str, Any]]:
        """Return diary rows sorted by date (oldest first).

        Unions Inspection rows with Monthly Diary (``WorkDiaryEntry``) rows
        for the same officer + date range when ``include_inspections`` is
        true (the interactive index). The official PDF/preview report passes
        ``include_inspections=False`` and builds Monthly-only. Within one
        date, Inspection rows come first, Monthly duties after in
        ``duty_seq`` order; a split-duty day renders as two rows with the
        Date cell merged.

        Args:
            fso_name: Restrict to one FSO (the per-FSO view).
            date_from / date_to: Inclusive ISO-date strings (YYYY-MM-DD).
            purpose: Optional filter — ``"routine"`` / ``"inspection"`` (routine
                inspections + monthly field/office days), ``"complaint"``
                (complaint inspections), ``"vvip"`` / ``"meeting"`` (monthly
                days only); ``"routine"`` is kept as an alias of
                ``"inspection"``. Anything else means "all".
            include_dismissed: Dismissed inspections are excluded by default
                (Monthly rows have no dismissed state and are always kept).
            include_inspections: When false, skip the Inspection query and
                return Monthly rows only (official report buildup).

        """
        norm_purpose = (purpose or "").strip().lower()
        entries: list[dict[str, Any]] = []
        if include_inspections:
            query = db.session.query(Inspection).join(FSO, Inspection.fso_name == FSO.fso_name)

            if fso_name is not None:
                # ``""`` is the deny-by-default sentinel for unbound non-admins
                # (scoped_officer_name): it must filter to nothing, not to all.
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

            if norm_purpose in ("inspection", "routine"):
                query = query.filter(
                    db.or_(
                        Inspection.visit_purpose == "routine",
                        db.and_(
                            Inspection.visit_purpose.is_(None),
                            db.or_(Inspection.problem.is_(None), Inspection.problem == ""),
                        ),
                    ),
                )
            elif norm_purpose == "complaint":
                query = query.filter(
                    db.or_(
                        Inspection.visit_purpose == "complaint",
                        db.and_(
                            Inspection.visit_purpose.is_(None),
                            Inspection.problem.isnot(None),
                            Inspection.problem != "",
                        ),
                    ),
                )
            elif norm_purpose in ("vvip", "meeting"):
                query = query.filter(db.text("1 = 0"))  # no Inspection matches these

            inspections = query.order_by(Inspection.inspection_date.asc(), Inspection.id.asc()).all()
            entries = [self._to_entry(insp) for insp in inspections]
        elif norm_purpose == "complaint":
            # Monthly-only report: no Monthly row carries the Complaint
            # purpose (complaint detail lives in Notes/Activity text).
            self._annotate_date_groups(entries)
            return entries
        entries.extend(
            self._monthly_entries(
                fso_name=fso_name,
                date_from=date_from,
                date_to=date_to,
                purpose=norm_purpose or None,
            ),
        )
        entries.sort(
            key=lambda e: (
                e["date"].date() if e["date"] else datetime.min.date(),
                0 if e.get("inspection_id") else 1,
                e.get("duty_seq") or 0,
            ),
        )
        self._annotate_date_groups(entries)
        return entries

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

    def _monthly_entries(
        self,
        fso_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        purpose: str | None = None,
    ) -> list[dict[str, Any]]:
        """Monthly Diary rows for the same officer + range, as diary entries.

        Skips ``holiday`` / ``leave`` days (no Work Diary purpose) and applies
        the shared purpose filter (``vvip`` / ``meeting`` / ``inspection`` /
        ``routine``-alias / ``complaint``-excludes-monthly).
        """
        from app.models import WorkDiaryEntry

        if purpose == "complaint":
            return []
        query = db.session.query(WorkDiaryEntry)
        if fso_name is not None:
            query = query.filter(WorkDiaryEntry.fso_name == fso_name)
        if date_from:
            query = query.filter(WorkDiaryEntry.work_date >= date_from)
        if date_to:
            query = query.filter(WorkDiaryEntry.work_date <= date_to)
        try:
            rows = query.order_by(WorkDiaryEntry.work_date.asc(), WorkDiaryEntry.duty_seq.asc()).all()
        except (sa_exc.OperationalError, sa_exc.ProgrammingError):
            # Pre-migration database without the work_diary table / place
            # column (the boot schema check fails loud for this; this is
            # belt-and-braces so the report degrades instead of 500ing).
            logger.exception("WorkDiaryEngine: work_diary query failed")
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = self._diary_to_entry(row)
            if entry is None:
                continue
            if purpose in ("vvip", "meeting", "inspection", "routine"):
                want = {
                    "vvip": PURPOSE_VVIP,
                    "meeting": PURPOSE_MEETING,
                    "inspection": PURPOSE_INSPECTION,
                    "routine": PURPOSE_INSPECTION,
                }[purpose]
                if entry["purpose"] != want:
                    # "routine" is the legacy alias of the monthly
                    # "Inspection" purpose; routine inspections are already
                    # included from the Inspection side.
                    continue
            out.append(entry)
        return out

    @staticmethod
    def _diary_to_entry(row: Any) -> dict[str, Any] | None:
        """Shape one ``WorkDiaryEntry`` into a diary row (or ``None`` to skip).

        The Activity text is rebuilt here at print time from the live fields
        (never the frozen stored summary): ``<label>[ at place].`` + the
        count sentence when the activity records counts and at least one of
        premises/samples is non-zero + ``Remarks: <notes>`` when notes exist.
        No date prefix — column (i) already shows the date.
        """
        activity_key = (row.activity or "").strip()
        purpose = derive_diary_purpose(activity_key)
        if purpose is None:
            return None  # holiday / leave / unknown: monthly grid only
        try:
            when = datetime.strptime(row.work_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
        place = (getattr(row, "place_of_visit", None) or "").strip()
        activity = print_activity(
            activity_key,
            row.premises or 0,
            row.samples or 0,
            (row.notes or "").strip(),
            place,
        )
        return {
            "inspection_id": None,
            "inspection_code": "",
            "fso_name": row.fso_name,
            "date": when,
            "duty_seq": getattr(row, "duty_seq", None) or 1,
            "place_of_visit": escape(place) if place else "—",
            "purpose": purpose,
            "activity": escape(activity),
            "sample_collected": False,
            "sample_code": "",
        }

    def _to_entry(self, insp: Inspection) -> dict[str, Any]:
        purpose = self.derive_purpose(insp.problem, insp.visit_purpose)

        # --- Column 2: Place of Visit (FBO name + address + license) ---
        fbo_name = (insp.fbo_name or "").strip()
        fbo_address = (insp.fbo_address or "").strip()
        license_no = (insp.fssai_license or "").strip()

        place_lines: list[str] = []
        if fbo_name and fbo_address:
            place_lines.append(f"{fbo_name}, {fbo_address}")
        elif fbo_address:
            place_lines.append(fbo_address)
        elif fbo_name:
            place_lines.append(fbo_name)
        else:
            place_lines.append("\u2014")
        if license_no:
            place_lines.append(f"License: {license_no}")
        place_of_visit = "<br>".join(place_lines)

        # --- Column 4: Activity (enriched with food item + notice info) ---
        concerned_food = (insp.concerned_food or "").strip()
        notice_date = insp.notice_issued_at.strftime("%d-%m-%Y") if insp.notice_issued_at else None

        if purpose == PURPOSE_COMPLAINT:
            problem_brief = (insp.problem or "").strip()
            activity = f"Enquiry into complaint: {problem_brief}" if problem_brief else "Enquiry into complaint"
            if fbo_name:
                food_clause = f" ({concerned_food})" if concerned_food else ""
                activity += f"<br>Inspected {fbo_name}{food_clause}"
        else:
            subject = fbo_name or "food premises"
            food_clause = f" ({concerned_food})" if concerned_food else ""
            activity = f"Routine inspection of {subject}{food_clause}"

        if notice_date:
            activity += f"<br>Notice issued: {notice_date}."

        # --- Sample collection ---
        sample_collected = bool(insp.sample_collected) if insp.sample_collected is not None else False
        sample_code = insp.sample_code or ""

        return {
            "inspection_id": insp.id,
            "inspection_code": insp.inspection_code,
            "fso_name": insp.fso_name,
            "date": insp.inspection_date,
            "duty_seq": 0,
            "place_of_visit": place_of_visit,
            "purpose": purpose,
            "activity": activity,
            "sample_collected": sample_collected,
            "sample_code": sample_code,
        }

    @staticmethod
    def _annotate_date_groups(entries: list[dict[str, Any]]) -> None:
        """Add ``is_first_in_date`` and ``date_rowspan`` for merged-date rendering.

        Mutates each entry dict in-place so that the template can use
        ``rowspan`` on the first row of a date group and skip the date
        cell on subsequent rows.
        """
        from collections import OrderedDict

        # Group entries by calendar date
        groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for entry in entries:
            d = entry["date"]
            key = d.strftime("%Y-%m-%d") if d else "__none__"
            groups.setdefault(key, []).append(entry)

        for _key, group in groups.items():
            rowspan = len(group)
            for i, entry in enumerate(group):
                entry["is_first_in_date"] = i == 0
                entry["date_rowspan"] = rowspan if i == 0 else 0
