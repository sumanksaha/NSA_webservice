"""Work Diary bulk-entry model.

``WorkDiaryEntry`` is one officer's record for one duty slot on one calendar
day, written by the bulk grid in ``app/diary.py``. A calendar day holds up to
two duty slots (``duty_seq`` 1|2) — a **split-duty day** (e.g. field work +
VVIP duty). The table is owner-scoped: ``fso_name`` is part of the primary
key, so one officer can never read or overwrite another officer's diary —
the isolation ``app/workdiary`` already enforces through
``app.shared.rbac.scoped_officer_name``.

``fso_name`` is free text (a mirror of ``user.fso_name``) rather than a
foreign key, matching ``inspection.fso_name`` and keeping rows writable for
officers who are not yet in the ``fso`` table. Admins have no bound officer
and write under the empty-string owner; a non-admin with no bound officer is
scoped to the same empty string (deny-by-default), so that key is only
reachable by accounts without an officer.
"""

from __future__ import annotations

from app.extensions import db


class WorkDiaryEntry(db.Model):
    """One duty slot of activity for one officer on one calendar day."""

    __tablename__ = "work_diary"

    fso_name = db.Column(db.String(100), primary_key=True)
    work_date = db.Column(db.String(10), primary_key=True)  # ISO YYYY-MM-DD
    duty_seq = db.Column(db.Integer, primary_key=True, default=1)  # 1|2; one row per slot
    activity = db.Column(db.String(32), nullable=True)  # key into diary.ACTIVITIES
    premises = db.Column(db.Integer, nullable=True)
    samples = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    place_of_visit = db.Column(db.String(200), nullable=True)
    summary = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<WorkDiaryEntry fso_name={self.fso_name!r} work_date={self.work_date} duty_seq={self.duty_seq}>"
