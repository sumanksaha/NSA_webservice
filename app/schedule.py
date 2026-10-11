"""Schedule planning — search premises, select, record the duty.

The flow an FSO actually wants: type *"restaurant in NSC Bose Road"* → get
as many matching registered premises as the registry holds → pick one (or
several) → record the duty. Recording writes **two** things in one
transaction:

* the Monthly Diary slot (``work_diary``) via :mod:`app.services.diary_planner`,
  so the day appears in the official Work Diary report;
* a draft **Inspection** row, so the premise shows up on the inspection list
  and the officer can carry it out.

Both writes are confirmed before they happen — ``/plan`` proposes and writes
nothing; ``/confirm`` is the single route that persists.

RBAC: ``owner`` is force-stamped from ``scoped_officer_name(current_user)``
(SEC-1); a non-admin cannot target another officer's diary, and the draft
Inspection is stamped with the same bound name.
"""

from __future__ import annotations

import datetime as _dt

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.diary import FIELD_ACTIVITY, resolve_owner
from app.extensions import db
from app.inspection.code_generation import calculate_compliance_deadline, generate_inspection_code
from app.models import FSO, Inspection
from app.services import diary_planner, premises_resolver
from app.utils.filters import parse_date

schedule_bp = Blueprint(
    "schedule",
    __name__,
    url_prefix="/schedule",
    template_folder="templates",
)


def _default_date() -> str:
    return _dt.date.today().isoformat()


@schedule_bp.route("/search", methods=["GET", "POST"])
@login_required
def search():
    """Resolve free text to premises candidates (writes nothing)."""
    query = (request.values.get("q") or "").strip()
    result = None
    if query:
        result = premises_resolver.resolve(query)

    # Split canonical matches from look-alike roads so the UI never presents
    # Sarat Bose Road as if it were NSC Bose Road (FR-27).
    primary, confusable = [], []
    if result is not None and result.error is None:
        for cand in result.candidates:
            (confusable if cand.road.is_confusable else primary).append(cand)

    return render_template(
        "schedule/search.html",
        query=query,
        result=result,
        primary=primary,
        confusable=confusable,
        work_date=(request.values.get("work_date") or _default_date()),
    )


@schedule_bp.route("/confirm", methods=["POST"])
@login_required
def confirm():
    """Persist the selected premises: one diary slot + one draft inspection."""
    work_date = (request.form.get("work_date") or _default_date()).strip()
    notes = (request.form.get("notes") or "").strip()
    numbers = [n for n in request.form.getlist("fssai_number") if n.strip()]

    if not numbers:
        flash("Select at least one premises before saving.", "warning")
        return redirect(url_for("schedule.search", q=request.form.get("q") or "", work_date=work_date))

    owner = resolve_owner()
    if not owner:
        flash("Your account is not bound to an officer. Ask an admin to bind it.", "error")
        return redirect(url_for("schedule.search", q=request.form.get("q") or "", work_date=work_date))
    if db.session.get(FSO, owner) is None:
        flash(f'Officer "{owner}" is not registered. Nothing was saved.', "error")
        return redirect(url_for("schedule.search", q=request.form.get("q") or "", work_date=work_date))

    inspection_dt = parse_date(work_date)
    if inspection_dt is None:
        flash(f'"{work_date}" is not a valid date. Nothing was saved.', "error")
        return redirect(url_for("schedule.search", q=request.form.get("q") or "", work_date=work_date))

    # Resolve each selection against the registry (server-side, never trusting
    # the company name the client echoed back).
    chosen = []
    for number in numbers:
        hit = premises_resolver.search_by_fssai(number)
        if hit.found and hit.candidates:
            chosen.append(hit.candidates[0])
    if not chosen:
        flash("None of the selected premises could be resolved. Nothing was saved.", "error")
        return redirect(url_for("schedule.search", q=request.form.get("q") or "", work_date=work_date))

    # --- Monthly Diary slot -------------------------------------------------
    place = chosen[0].full_address or ""
    tally = diary_planner.record_many(
        owner,
        work_date,
        [
            {
                "duty_seq": 1,
                "activity": FIELD_ACTIVITY,
                "premises": len(chosen),
                "samples": 0,
                "notes": notes or _default_notes(chosen),
                "place": place,
            },
        ],
    )
    db.session.commit()

    # --- Draft inspections --------------------------------------------------
    created = 0
    for cand in chosen:
        already = Inspection.query.filter(
            Inspection.fssai_license == cand.fssai_number,
            Inspection.inspection_date == inspection_dt,
            Inspection.fso_name == owner,
        ).first()
        if already is not None:
            continue  # already on the list for this day
        db.session.add(
            Inspection(
                inspection_code=generate_inspection_code(),
                fso_name=owner,
                fssai_license=cand.fssai_number,
                fbo_name=(cand.company_name or None),
                fbo_address=cand.full_address,
                visit_purpose="routine",
                inspection_date=inspection_dt,
                compliance_deadline=calculate_compliance_deadline(inspection_dt),
                is_dismissed=False,
            ),
        )
        created += 1
    db.session.commit()

    flash(
        f"Saved {tally['saved']} diary slot(s) and {created} inspection(s) for {work_date}.",
        "success",
    )
    return redirect(url_for("schedule.search", q=request.form.get("q") or "", work_date=work_date))


def _default_notes(chosen: list) -> str:
    names = [c.company_name for c in chosen if c.company_name]
    joined = ", ".join(names[:3])
    if len(names) > 3:
        joined += f" +{len(names) - 3} more"
    return f"Scheduled: {joined}" if joined else "Scheduled premises"


__all__ = ["schedule_bp"]
