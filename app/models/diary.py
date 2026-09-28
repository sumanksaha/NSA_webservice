"""Work Diary bulk-entry model.

``WorkDiaryEntry`` is one officer's record for one calendar day, written by
the bulk grid in ``app/diary.py``. The table is owner-scoped: ``fso_name`` is
half of the primary key, so one officer can never read or overwrite another
officer's diary — the isolation ``app/workdiary`` already enforces through
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
    """One calendar day of activity for one officer."""

    __tablename__ = "work_diary"

    fso_name = db.Column(db.String(100), primary_key=True)
    work_date = db.Column(db.String(10), primary_key=True)  # ISO YYYY-MM-DD
    activity = db.Column(db.String(32), nullable=True)  # key into diary.ACTIVITIES
    premises = db.Column(db.Integer, nullable=True)
    samples = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    summary = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<WorkDiaryEntry fso_name={self.fso_name!r} work_date={self.work_date}>"
