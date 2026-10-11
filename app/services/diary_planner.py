"""Work-diary slot persistence — the diary write seam.

The one deep module behind "write one duty slot of an FSO's Monthly Diary".
The rules live here rather than in the bulk-grid route so that any caller —
the HTML grid, the schedule agent, a QStash task, or a test — gets the same
invariants. Constants and the summary-line builder are imported from
``app.diary`` rather than re-declared, so there is exactly one vocabulary.

Invariants guaranteed after :func:`record` returns (spec §6):

* **INV-1** a day never holds a bare ``duty_seq=2`` row — a lone second slot
  is promoted to slot 1;
* **INV-2** ``holiday`` / ``leave`` never share a day with another duty — a
  second duty beside an exclusive activity is cleared, and an exclusive
  activity in slot 2 loses to a working slot 1;
* **INV-3** an activity outside ``COUNT_ACTIVITIES`` always has zeroed
  premises/samples;
* **INV-5** ``place_of_visit`` is truncated to the column width and stored as
  ``NULL`` when empty;
* **INV-6** counts are clamped into range.

The caller supplies ``owner``. **SEC-1** is the caller's obligation: it must
come from ``app.shared.rbac.scoped_officer_name(current_user)``, never from
request data.
"""

from __future__ import annotations

import re

from app.diary import (
    ACTIVITIES,
    COUNT_ACTIVITIES,
    EXCLUSIVE_ACTIVITIES,
    FIELD_ACTIVITY,
    PLACE_MAX_LEN,
    parse_count,
    summary_line,
)
from app.extensions import db
from app.models import WorkDiaryEntry

#: Duty slots a day may hold.
VALID_DUTY_SEQS = (1, 2)

#: INV-7 — work_date is a 10-char ISO string; anything else is silently
#: dropped from the Work Diary report, so it is refused at the write.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _row(owner: str, work_date: str, duty_seq: int) -> WorkDiaryEntry | None:
    return db.session.get(
        WorkDiaryEntry,
        {"fso_name": owner, "work_date": work_date, "duty_seq": duty_seq},
    )


def _has_content(activity: str, premises: int, samples: int, notes: str, place: str) -> bool:
    return bool(activity.strip()) or bool(premises) or bool(samples) or bool(notes.strip()) or bool(place.strip())


def _upsert(
    owner: str,
    work_date: str,
    duty_seq: int,
    activity: str,
    premises: int,
    samples: int,
    notes: str,
    place: str,
) -> None:
    row = _row(owner, work_date, duty_seq)
    if row is None:
        row = WorkDiaryEntry(fso_name=owner, work_date=work_date, duty_seq=duty_seq)
        db.session.add(row)
    row.activity = activity
    row.premises = premises
    row.samples = samples
    row.notes = notes
    row.place_of_visit = place[:PLACE_MAX_LEN] or None
    row.summary = summary_line(work_date, activity, premises, samples, notes, place)


def _delete(owner: str, work_date: str, duty_seq: int) -> int:
    """Remove one duty slot; return 1 if deleted.

    Uses ``session.delete(row)`` rather than a bulk ``query().delete()``: a bulk
    delete emits a statement straight to the database and never puts the object
    in ``session.deleted``, so the audit hook in ``app/audit_hooks.py`` would
    never see it. One row per call makes the per-object path cheap.
    """
    row = _row(owner, work_date, duty_seq)
    if row is None:
        return 0
    db.session.delete(row)
    return 1


def record(
    owner: str,
    work_date: str,
    duty_seq: int = 1,
    activity: str = FIELD_ACTIVITY,
    premises: int = 0,
    samples: int = 0,
    notes: str = "",
    place: str = "",
) -> str:
    """Persist one duty slot. Returns ``"saved"`` / ``"noop"``.

    ``"noop"`` is returned when the activity is not in :data:`ACTIVITIES`, or
    when the stored row already equals the incoming values (re-saves do not
    rewrite).

    The slot is normalised before persisting: counts are clamped (INV-6),
    non-counting activities are zeroed (INV-3), and the place is truncated
    (INV-5). On return the caller may ``db.session.commit()``.
    """
    activity = (activity or "").strip()
    notes = (notes or "").strip()
    place = (place or "").strip()
    premises = parse_count(str(premises))
    samples = parse_count(str(samples))

    if activity and activity not in ACTIVITIES:
        return "noop"  # unknown activity values are ignored

    # INV-7: a non-ISO date is silently dropped from every Work Diary report
    # (app/workdiary/engine.py), so refuse it here rather than write a row that
    # can never be seen again.
    if not _ISO_DATE_RE.match(work_date or ""):
        return "noop"

    if duty_seq not in VALID_DUTY_SEQS:
        duty_seq = 1

    if not activity:
        # A blank slot clears its stored row; numbers/notes with no activity
        # imply field work (mirrors app/diary.py:_save_slot).
        if not (premises or samples or notes or place):
            return "deleted" if _delete(owner, work_date, duty_seq) else "noop"
        activity = FIELD_ACTIVITY

    if activity not in COUNT_ACTIVITIES:
        premises = 0
        samples = 0

    summary = summary_line(work_date, activity, premises, samples, notes, place)
    if _is_unchanged(owner, work_date, duty_seq, activity, premises, samples, notes, summary, place):
        return "noop"  # untouched rows are not rewritten

    _upsert(owner, work_date, duty_seq, activity, premises, samples, notes, place)

    # INV-1 / INV-2 must hold the moment this function returns, not only when
    # the caller happened to use record_many().
    _enforce_invariants(owner, work_date)
    return "saved"


def _is_unchanged(
    owner: str,
    work_date: str,
    duty_seq: int,
    activity: str,
    premises: int,
    samples: int,
    notes: str,
    summary: str,
    place: str,
) -> bool:
    """True when the stored row already equals the incoming values."""
    row = _row(owner, work_date, duty_seq)
    if row is None:
        return False
    return (
        (row.activity or "") == activity
        and (row.premises or 0) == premises
        and (row.samples or 0) == samples
        and (row.notes or "") == notes
        and (row.summary or "") == summary
        and (row.place_of_visit or "") == place[:PLACE_MAX_LEN]
    )


def _enforce_invariants(owner: str, work_date: str) -> None:
    """Apply INV-1 (no lone slot 2) and INV-2 (holiday/leave never share)."""
    _promote_lonely_second(owner, work_date)
    _enforce_exclusivity(owner, work_date)


def record_many(
    owner: str,
    work_date: str,
    entries: list[dict],
) -> dict[str, int]:
    """Write several slots for one day.

    ``entries`` is a list of ``{"duty_seq", "activity", "premises",
    "samples", "notes", "place"}`` dicts (any key may be omitted). This is the
    path the schedule feature uses when an FSO picks several premises for the
    same day.

    Each entry goes through :func:`record`, which already enforces INV-1/INV-2
    per write; the invariants are re-applied once at the end because a day can
    reach a valid final state only after its last slot lands (e.g. holiday in
    slot 1 clearing a slot 2 written moments earlier).

    Returns a small ``{saved, deleted, moved}`` tally. The caller commits.
    """
    tally = {"saved": 0, "deleted": 0, "moved": 0}
    for entry in entries:
        outcome = record(
            owner,
            work_date,
            entry.get("duty_seq", 1),
            entry.get("activity", FIELD_ACTIVITY),
            entry.get("premises", 0),
            entry.get("samples", 0),
            entry.get("notes", ""),
            entry.get("place", ""),
        )
        if outcome == "saved":
            tally["saved"] += 1
        elif outcome == "deleted":
            tally["deleted"] += 1

    # Re-apply once at the end: a day's final state depends on the last slot,
    # not on the state at the moment any single one was written.
    tally["moved"] += _promote_lonely_second(owner, work_date)
    tally["deleted"] += _enforce_exclusivity(owner, work_date)
    return tally


def _promote_lonely_second(owner: str, work_date: str) -> int:
    """INV-1: move a lone ``duty_seq=2`` row to slot 1. Return 1 if moved."""
    first = _row(owner, work_date, 1)
    second = _row(owner, work_date, 2)
    if first is None and second is not None:
        db.session.add(
            WorkDiaryEntry(
                fso_name=owner,
                work_date=work_date,
                duty_seq=1,
                activity=second.activity,
                premises=second.premises,
                samples=second.samples,
                notes=second.notes,
                place_of_visit=second.place_of_visit,
                summary=second.summary,
            ),
        )
        db.session.delete(second)
        return 1
    return 0


def _enforce_exclusivity(owner: str, work_date: str) -> int:
    """INV-2: holiday/leave never share a day. Slot 1 wins.

    Returns the number of rows deleted.
    """
    first = _row(owner, work_date, 1)
    second = _row(owner, work_date, 2)
    deleted = 0
    if first is not None and (first.activity or "") in EXCLUSIVE_ACTIVITIES:
        if second is not None:
            deleted += _delete(owner, work_date, 2)
    elif second is not None and (second.activity or "") in EXCLUSIVE_ACTIVITIES:
        # Exclusive activity in slot 2 loses to a working slot 1.
        deleted += _delete(owner, work_date, 2)
    return deleted


def slots_for(owner: str, work_date: str) -> dict[int, dict]:
    """Return the day's stored slots keyed by ``duty_seq`` (for rendering)."""
    out: dict[int, dict] = {}
    for seq in VALID_DUTY_SEQS:
        row = _row(owner, work_date, seq)
        if row is not None:
            out[seq] = {
                "activity": row.activity or "",
                "premises": row.premises or 0,
                "samples": row.samples or 0,
                "notes": row.notes or "",
                "place_of_visit": row.place_of_visit or "",
            }
    return out
