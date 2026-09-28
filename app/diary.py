"""Work Diary module — bulk daily activity entry.

One editable grid per calendar month: every day gets a row (Activity,
Premises, Samples, Notes) and "Save all" writes the whole month in one
POST. Persistence is the ``work_diary`` table (one row per officer per day,
keyed by ``fso_name`` + ``work_date``), owned by
``app.models.WorkDiaryEntry`` and created by its Alembic migration.

Rows are officer-scoped: a non-admin always reads and writes their own bound
officer's diary (``app.shared.rbac.scoped_officer_name``), so one officer
cannot see or overwrite another's entries. Admins pick the officer they are
editing via ``?fso_name=``; they default to their own bound officer.

The module reuses the app-wide SQLAlchemy handle (``app.extensions.db``)
and the global login gate; ``@login_required`` is applied explicitly so
the protection is visible at the route.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import WorkDiaryEntry
from app.shared.rbac import scoped_officer_name

#: Activity types offered in the dropdown. Edit here to change labels or
#: add/remove options — the dropdown, validation, and summary lines all
#: derive from this dict. Premises/samples are only counted for ``field``.
ACTIVITIES = {
    "field": "Field work",
    "vvip": "VVIP duty",
    "meeting": "Meeting at HQ",
    "office": "Office / court work",
    "holiday": "Holiday / weekly off",
    "leave": "Leave",
}

#: The only activity for which premises / samples are recorded.
FIELD_ACTIVITY = "field"

#: Upper bound for premises / samples. The column is a 32-bit INTEGER, so an
#: unclamped form value (e.g. ``99999999999``) would overflow on PostgreSQL
#: and 500 the request; a day's inspection count never approaches this.
MAX_COUNT = 999_999

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

diary_bp = Blueprint("diary", __name__, url_prefix="/diary", template_folder="templates")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _resolve_month() -> _dt.date:
    """First day of the requested month, or the current month when invalid.

    Accepts ``m`` from the query string (GET) or the form (POST). Anything
    that is not ``YYYY-MM`` (or names a non-existent month) falls back to
    the current month.
    """
    raw = (request.values.get("m") or "").strip()
    match = _MONTH_RE.match(raw)
    if match:
        try:
            return _dt.date(int(match.group(1)), int(match.group(2)), 1)
        except ValueError:
            pass
    return _dt.date.today().replace(day=1)


def _resolve_owner() -> str:
    """The officer whose diary this request reads and writes.

    Non-admins are locked to their bound officer (``""`` when unbound, which
    matches nothing else) and any ``fso_name`` they supply is ignored. Admins
    may choose an officer with ``?fso_name=`` — an explicitly empty value
    selects the unassigned officer, while a missing parameter edits their own
    bound officer. Unknown names fall back to their own instead of opening a
    phantom diary (which would also 500 on PostgreSQL past 100 chars).
    """
    scope = scoped_officer_name(current_user)
    if scope is not None:
        return scope
    own = getattr(current_user, "fso_name", None) or ""
    if "fso_name" not in request.values:
        return own
    requested = (request.values.get("fso_name") or "").strip()
    if requested and requested not in [*_officer_choices(), own]:
        return own
    return requested


def _parse_count(raw: str | None) -> int:
    """Parse a premises/samples field: non-numeric and negative become 0."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return min(max(value, 0), MAX_COUNT)


def _shift_month(month: _dt.date, delta: int) -> _dt.date:
    """Return the first day of ``month`` shifted by ``delta`` months."""
    index = (month.year * 12 + (month.month - 1)) + delta
    return _dt.date(index // 12, index % 12 + 1, 1)


def _is_blank_day(premises: int, samples: int, notes: str) -> bool:
    """True when every free-text field of a submitted row is empty."""
    return premises == 0 and samples == 0 and not notes


def _is_untouched_stored(entry: dict | None, premises: int, samples: int, notes: str) -> bool:
    """True when a submitted row only changed the activity dropdown to "—".

    The grid pre-fills premises / samples / notes, so selecting "—" submits
    the stored numbers verbatim; without this check the blank activity would
    coerce the day back to field work and the day could never be cleared.
    """
    if entry is None:
        return False
    return premises == entry["premises"] and samples == entry["samples"] and notes == entry["notes"]


def _is_unchanged_stored(
    entry: dict | None, activity: str, premises: int, samples: int, notes: str, summary: str
) -> bool:
    """True when a submitted row already matches the stored row exactly.

    The grid submits all 31 days on every save, so without this check every
    save would rewrite the whole month (and report all untouched days as
    "Saved"). Comparing the stored summary as well keeps legacy rows in sync
    when a summary format or activity label changes.
    """
    if entry is None:
        return False
    return (
        activity == entry["activity"]
        and premises == entry["premises"]
        and samples == entry["samples"]
        and notes == entry["notes"]
        and summary == entry["summary"]
    )


# ---------------------------------------------------------------------------
# Summary lines
# ---------------------------------------------------------------------------


def _summary_line(work_date: str, activity: str, premises: int, samples: int, notes: str) -> str:
    """Build the daily activity summary line for one diary row."""
    if activity == FIELD_ACTIVITY:
        line = f"On {work_date}: Field work. Inspected {premises} premises, collected {samples} sample(s)."
    else:
        label = ACTIVITIES.get(activity, activity)
        line = f"On {work_date}: {label}."
    remarks = (notes or "").strip()
    if remarks:
        line += f" Remarks: {remarks}"
    return line


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def _load_month(owner: str, month: _dt.date) -> dict[str, dict]:
    """Return ``owner``'s diary rows for ``month`` keyed by ISO date."""
    last_day = calendar.monthrange(month.year, month.month)[1]
    result = db.session.execute(
        db.select(WorkDiaryEntry).where(
            WorkDiaryEntry.fso_name == owner,
            WorkDiaryEntry.work_date >= month.isoformat(),
            WorkDiaryEntry.work_date <= _dt.date(month.year, month.month, last_day).isoformat(),
        ),
    ).scalars()
    rows: dict[str, dict] = {}
    for row in result:
        rows[row.work_date] = {
            "work_date": row.work_date,
            "activity": row.activity or "",
            "premises": row.premises or 0,
            "samples": row.samples or 0,
            "notes": row.notes or "",
            "summary": row.summary or "",
        }
    return rows


def _upsert(owner: str, work_date: str, activity: str, premises: int, samples: int, notes: str, summary: str) -> None:
    """Insert or update one of ``owner``'s diary rows."""
    row = db.session.get(WorkDiaryEntry, {"fso_name": owner, "work_date": work_date})
    if row is None:
        row = WorkDiaryEntry(fso_name=owner, work_date=work_date)
        db.session.add(row)
    row.activity = activity
    row.premises = premises
    row.samples = samples
    row.notes = notes
    row.summary = summary


def _delete(owner: str, work_date: str) -> int:
    """Remove one of ``owner``'s diary rows; return 1 if a row was deleted."""
    return (
        db.session
        .query(WorkDiaryEntry)
        .filter(WorkDiaryEntry.fso_name == owner, WorkDiaryEntry.work_date == work_date)
        .delete()
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@diary_bp.route("/bulk", methods=["GET", "POST"])
@login_required
def bulk():
    """Bulk daily-activity editor for one month (GET) / save it (POST)."""
    month = _resolve_month()
    owner = _resolve_owner()
    days_in_month = calendar.monthrange(month.year, month.month)[1]
    entries = _load_month(owner, month)

    if request.method == "POST":
        saved = 0
        deleted = 0
        for day in range(1, days_in_month + 1):
            work_date = f"{month.year:04d}-{month.month:02d}-{day:02d}"
            activity = (request.form.get(f"activity_{day}") or "").strip()
            premises = _parse_count(request.form.get(f"premises_{day}"))
            samples = _parse_count(request.form.get(f"samples_{day}"))
            notes = (request.form.get(f"notes_{day}") or "").strip()

            if activity and activity not in ACTIVITIES:
                continue  # unknown activity values are ignored
            if not activity:
                if _is_blank_day(premises, samples, notes) or _is_untouched_stored(
                    entries.get(work_date), premises, samples, notes
                ):
                    # A fully blank row clears the day, and so does flipping a
                    # saved day to "—" without retyping its numbers — the
                    # pre-filled inputs are still in the submitted form.
                    deleted += _delete(owner, work_date)
                    continue
                activity = FIELD_ACTIVITY  # numbers (or notes) with no activity -> field work
            if activity != FIELD_ACTIVITY:
                premises = 0
                samples = 0
            summary = _summary_line(work_date, activity, premises, samples, notes)
            if _is_unchanged_stored(entries.get(work_date), activity, premises, samples, notes, summary):
                continue  # untouched rows are not rewritten; a re-save is a no-op
            _upsert(
                owner,
                work_date,
                activity,
                premises,
                samples,
                notes,
                summary,
            )
            saved += 1
        if saved or deleted:
            db.session.commit()
        if saved:
            flash(f"Saved {saved} day(s) for {calendar.month_name[month.month]} {month.year}.", "success")
        if deleted:
            flash(f"Cleared {deleted} blank day(s).", "info")
        return redirect(url_for("diary.bulk", m=month.strftime("%Y-%m"), fso_name=owner))

    rows = []
    for day in range(1, days_in_month + 1):
        work_date = f"{month.year:04d}-{month.month:02d}-{day:02d}"
        entry = entries.get(work_date, {})
        rows.append({
            "day": day,
            "work_date": work_date,
            "weekday": _dt.date(month.year, month.month, day).strftime("%a"),
            "activity": entry.get("activity", ""),
            "premises": entry.get("premises", 0),
            "samples": entry.get("samples", 0),
            "notes": entry.get("notes", ""),
        })

    month_entries = [entries[r["work_date"]] for r in rows if r["work_date"] in entries]
    total_premises = sum(e["premises"] for e in month_entries)
    total_samples = sum(e["samples"] for e in month_entries)
    days_per_activity = {key: 0 for key in ACTIVITIES}
    for e in month_entries:
        if e["activity"] in days_per_activity:
            days_per_activity[e["activity"]] += 1
    daily_activity = [
        e["summary"] or _summary_line(e["work_date"], e["activity"], e["premises"], e["samples"], e["notes"])
        for e in sorted(month_entries, key=lambda e: e["work_date"])
    ]

    return render_template(
        "diary/bulk.html",
        activities=ACTIVITIES,
        month=month,
        month_param=month.strftime("%Y-%m"),
        month_label=f"{calendar.month_name[month.month]} {month.year}",
        prev_month=_shift_month(month, -1).isoformat(),
        next_month=_shift_month(month, 1).isoformat(),
        rows=rows,
        total_premises=total_premises,
        total_samples=total_samples,
        days_per_activity=days_per_activity,
        daily_activity=daily_activity,
        owner=owner,
        officer_choices=_officer_choices(),
    )


def _officer_choices() -> list[str]:
    """Officer names an admin may edit, or ``[]`` for a non-admin."""
    if not getattr(current_user, "is_admin", False):
        return []
    from app.utils.fso_data import get_all_fso_names

    return get_all_fso_names()
