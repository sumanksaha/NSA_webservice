"""Tests for the Work Diary bulk editor (app/diary.py).

Covers:
- Officer scoping: an FSO only ever reads/writes their own rows; a second
  officer neither sees nor overwrites them, and a cross-officer ``fso_name``
  in the query string is ignored
- Save loop: field/office/vvip/meeting keep their counts, holiday/leave zero
  them, numbers with no activity imply field work, unknown activities are
  ignored, place of visit round-trips (truncated to 200)
- Clearing a day: a fully blank row, and flipping a saved day to "—" without
  retyping the pre-filled numbers
- Only days that actually had a row are counted as cleared
- ``_parse_count`` clamping (32-bit INTEGER column)
- Month resolution, summary lines (count sentence hidden on 0/0), shift
  across a year boundary
- Auth gate + RBAC map entry
"""

from __future__ import annotations

import calendar
import datetime as _dt

import pytest

MONTH = "2026-03"  # 31 days


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def env():
    """App + logged-in clients for two officers, clean schema.

    The seeding context is popped before the clients are used: a test client
    reuses an already-pushed app context, and Flask-Login caches the loaded
    user on that context's ``g``, so the second request in the same context
    would still be seen as the first user.
    """
    from app import create_app
    from app.extensions import db
    from app.models import FSO, Role, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()

    db.session.add_all([FSO(fso_name="Officer A"), FSO(fso_name="Officer B")])
    fso_role = Role(name="fso")
    db.session.add(fso_role)
    db.session.add_all([
        User(
            username="officerA",
            password_hash="pbkdf2:sha256$test$dummy",
            is_admin=False,
            fso_name="Officer A",
        ),
        User(
            username="officerB",
            password_hash="pbkdf2:sha256$test$dummy",
            is_admin=False,
            fso_name="Officer B",
        ),
    ])
    db.session.commit()
    for user in db.session.query(User).all():
        user.roles.append(fso_role)
    db.session.commit()
    user_ids = {user.username: user.id for user in db.session.query(User).all()}
    db.session.remove()
    ctx.pop()

    clients = {"anon": app.test_client()}
    for username in ("officerA", "officerB"):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_ids[username])  # Flask-Login key
        clients[username] = client

    yield app, clients

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _blank_form() -> dict[str, str]:
    """A form with every day of the month left empty (both duty slots)."""
    form: dict[str, str] = {}
    for day in range(1, 32):
        for base in ("activity", "premises", "samples", "notes", "place"):
            form[f"{base}_{day}"] = ""
            form[f"{base}2_{day}"] = ""
    return form


def _save_day(client, day: int, **fields) -> str:
    """POST one populated diary row (plus blanks for the rest of the month)."""
    form = _blank_form()
    form.update({f"{key}_{day}": str(value) for key, value in fields.items()})
    resp = client.post(f"/diary/bulk?m={MONTH}", data=form, follow_redirects=True)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _row(app, fso_name: str, day: int, duty_seq: int = 1):
    """The stored diary row for one officer, day and duty slot (``None`` when absent)."""
    from app.extensions import db
    from app.models import WorkDiaryEntry

    with app.app_context():
        return db.session.get(
            WorkDiaryEntry, {"fso_name": fso_name, "work_date": f"{MONTH}-{day:02d}", "duty_seq": duty_seq}
        )


# --------------------------------------------------------------------------- #
# Auth / RBAC
# --------------------------------------------------------------------------- #


class TestAccess:
    def test_anonymous_is_redirected_to_login(self, env):
        _, clients = env
        resp = clients["anon"].get("/diary/bulk")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_diary_is_in_the_fso_role_map(self):
        from app.shared.rbac import FSO_ROLE, ROLE_BLUEPRINTS

        assert "diary" in ROLE_BLUEPRINTS[FSO_ROLE]


# --------------------------------------------------------------------------- #
# Save loop
# --------------------------------------------------------------------------- #


class TestSave:
    def test_field_work_round_trips(self, env):
        app, clients = env
        body = _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        row = _row(app, "Officer A", 5)
        assert (row.activity, row.premises, row.samples, row.notes) == ("field", 3, 2, "Market")
        assert "On 2026-03-05: Field work." in row.summary
        assert "Inspected 3 premises, collected 2 sample(s)." in row.summary
        assert "Remarks: Market" in row.summary
        assert "On 2026-03-05: Field work." in body

    def test_non_field_activity_zeroes_counts(self, env):
        app, clients = env
        _save_day(clients["officerA"], 7, activity="leave", premises=4, samples=1, notes="")
        row = _row(app, "Officer A", 7)
        assert row.activity == "leave"
        assert (row.premises, row.samples) == (0, 0)
        _save_day(clients["officerA"], 8, activity="holiday", premises=2, samples=2, notes="")
        assert (_row(app, "Officer A", 8).premises, _row(app, "Officer A", 8).samples) == (0, 0)

    def test_vvip_meeting_office_keep_counts(self, env):
        """VVIP/meeting/office days can carry inspection + sample data."""
        app, clients = env
        # One POST for all three days: each _save_day blanks the rest of the
        # month, so sequential single-day saves would clear each other.
        form = _blank_form()
        for day, activity, premises, samples in [
            (10, "vvip", 2, 1),
            (12, "meeting", 1, 0),
            (14, "office", 3, 3),
        ]:
            form[f"activity_{day}"] = activity
            form[f"premises_{day}"] = str(premises)
            form[f"samples_{day}"] = str(samples)
        resp = clients["officerA"].post(f"/diary/bulk?m={MONTH}", data=form, follow_redirects=True)
        assert resp.status_code == 200
        assert (_row(app, "Officer A", 10).premises, _row(app, "Officer A", 10).samples) == (2, 1)
        assert (_row(app, "Officer A", 12).premises, _row(app, "Officer A", 12).samples) == (1, 0)
        assert (_row(app, "Officer A", 14).premises, _row(app, "Officer A", 14).samples) == (3, 3)

    def test_place_round_trips_and_truncates(self, env):
        app, clients = env
        _save_day(clients["officerA"], 15, activity="field", place="Town Hall")
        assert _row(app, "Officer A", 15).place_of_visit == "Town Hall"
        # NOTE: each _save_day blanks the rest of the month, so day 15 is
        # cleared by this second save — only day 16 is asserted below.
        _save_day(clients["officerA"], 16, activity="field", place="x" * 500)
        assert _row(app, "Officer A", 16).place_of_visit == "x" * 200

    def test_zero_counts_hide_the_count_sentence(self, env):
        from app.diary import _summary_line

        assert _summary_line("2026-03-05", "field", 0, 0, "", "Bazaar") == (
            "On 2026-03-05: Field work at Bazaar."
        )
        assert "Inspected 3 premises" in _summary_line("2026-03-05", "field", 3, 2, "", "")

    def test_numbers_without_activity_become_field_work(self, env):
        app, clients = env
        _save_day(clients["officerA"], 9, activity="", premises=6, samples=0, notes="")
        row = _row(app, "Officer A", 9)
        assert row.activity == "field"
        assert (row.premises, row.samples) == (6, 0)

    def test_unknown_activity_is_ignored(self, env):
        app, clients = env
        _save_day(clients["officerA"], 11, activity="teleport", premises=1, notes="")
        assert _row(app, "Officer A", 11) is None

    def test_counts_are_clamped_to_the_column_range(self, env):
        from app.diary import MAX_COUNT, _parse_count

        app, clients = env
        _save_day(clients["officerA"], 13, activity="field", premises="99999999999", samples="-4", notes="")
        row = _row(app, "Officer A", 13)
        assert row.premises == MAX_COUNT
        assert row.samples == 0
        assert _parse_count("not a number") == 0
        assert _parse_count(None) == 0
        assert _parse_count("") == 0


# --------------------------------------------------------------------------- #
# Clearing
# --------------------------------------------------------------------------- #


class TestClear:
    def test_blank_month_reports_no_deletions(self, env):
        """Nothing was stored, so nothing can have been cleared."""
        _, clients = env
        body = (
            clients["officerA"]
            .post(
                f"/diary/bulk?m={MONTH}",
                data=_blank_form(),
                follow_redirects=True,
            )
            .get_data(as_text=True)
        )
        assert "Cleared" not in body
        assert "Saved" not in body

    def test_blank_activity_clears_a_saved_day(self, env):
        """Selecting "—" on a saved day clears it despite the pre-filled inputs."""
        app, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        # The grid re-submits the stored numbers with the dropdown set to "—".
        _save_day(clients["officerA"], 5, activity="", premises=3, samples=2, notes="Market")
        assert _row(app, "Officer A", 5) is None

    def test_edited_numbers_with_blank_activity_stay_field_work(self, env):
        app, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="")
        _save_day(clients["officerA"], 5, activity="", premises=4, samples=1, notes="")
        row = _row(app, "Officer A", 5)
        assert (row.activity, row.premises, row.samples) == ("field", 4, 1)

    def test_fully_blank_row_clears_and_counts(self, env):
        app, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="")
        body = (
            clients["officerA"]
            .post(
                f"/diary/bulk?m={MONTH}",
                data=_blank_form(),
                follow_redirects=True,
            )
            .get_data(as_text=True)
        )
        assert _row(app, "Officer A", 5) is None
        assert "Cleared 1 blank day(s)." in body

    def test_clearing_one_officers_day_leaves_another_alone(self, env):
        app, clients = env
        _save_day(clients["officerB"], 5, activity="field", premises=9, samples=9, notes="theirs")
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="mine")
        _save_day(clients["officerA"], 5, activity="", premises=3, samples=2, notes="mine")
        assert _row(app, "Officer A", 5) is None
        assert _row(app, "Officer B", 5).premises == 9


# --------------------------------------------------------------------------- #
# Officer scoping
# --------------------------------------------------------------------------- #


class TestOfficerScoping:
    def test_second_officer_does_not_see_the_first_ones_rows(self, env):
        _, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        body = clients["officerB"].get(f"/diary/bulk?m={MONTH}").get_data(as_text=True)
        assert "Market" not in body

    def test_second_officer_cannot_overwrite_the_first_ones_rows(self, env):
        app, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        _save_day(clients["officerB"], 5, activity="field", premises=8, samples=8, notes="Mine")
        assert _row(app, "Officer A", 5).notes == "Market"
        assert _row(app, "Officer B", 5).notes == "Mine"

    def test_requested_officer_is_ignored_for_non_admins(self, env):
        _, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        body = clients["officerB"].get(f"/diary/bulk?m={MONTH}&fso_name=Officer A").get_data(as_text=True)
        assert "Market" not in body

    def test_officer_choices_hidden_from_non_admin(self, env):
        _, clients = env
        body = clients["officerA"].get(f"/diary/bulk?m={MONTH}").get_data(as_text=True)
        assert 'name="fso_name"' not in body


# --------------------------------------------------------------------------- #
# Idempotent saves
# --------------------------------------------------------------------------- #


class TestIdempotentSave:
    def test_resaving_identical_values_reports_nothing(self, env):
        app, clients = env
        first = _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        assert "Saved 1 day(s)" in first
        # Re-submit the exact stored values, as the rendered grid would.
        second = _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        assert "Saved" not in second
        assert "Cleared" not in second
        row = _row(app, "Officer A", 5)
        assert (row.activity, row.premises, row.samples, row.notes) == ("field", 3, 2, "Market")

    def test_only_changed_days_are_counted(self, env):
        app, clients = env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        form = _blank_form()
        form.update({"activity_5": "field", "premises_5": "3", "samples_5": "2", "notes_5": "Market"})
        form.update({"activity_7": "leave"})
        body = (
            clients["officerA"]
            .post(
                f"/diary/bulk?m={MONTH}",
                data=form,
                follow_redirects=True,
            )
            .get_data(as_text=True)
        )
        assert "Saved 1 day(s)" in body
        assert _row(app, "Officer A", 7).activity == "leave"


# --------------------------------------------------------------------------- #
# Retired activities
# --------------------------------------------------------------------------- #


class TestRetiredActivity:
    def test_unknown_stored_activity_survives_resave(self, env):
        """A stored activity no longer in ACTIVITIES must not be cleared.

        The grid keeps it selected under a "(retired)" label, and the save
        loop ignores unknown values instead of deleting the day.
        """
        from app.extensions import db
        from app.models import WorkDiaryEntry

        app, clients = env
        with app.app_context():
            db.session.add(
                WorkDiaryEntry(
                    fso_name="Officer A",
                    work_date=f"{MONTH}-05",
                    duty_seq=1,
                    activity="inspection",
                    premises=3,
                    samples=1,
                    notes="Market",
                    summary="legacy line",
                )
            )
            db.session.commit()

        body = clients["officerA"].get(f"/diary/bulk?m={MONTH}").get_data(as_text=True)
        assert "(retired)" in body

        # Re-submit exactly what the grid sends (retired key stays selected).
        form = _blank_form()
        form.update({"activity_5": "inspection", "premises_5": "3", "samples_5": "1", "notes_5": "Market"})
        clients["officerA"].post(f"/diary/bulk?m={MONTH}", data=form, follow_redirects=True)

        row = _row(app, "Officer A", 5)
        assert row is not None
        assert (row.activity, row.premises, row.samples, row.notes) == ("inspection", 3, 1, "Market")


# --------------------------------------------------------------------------- #
# Admin owner selection
# --------------------------------------------------------------------------- #


@pytest.fixture()
def admin_env(env):
    """The diary ``env`` plus a logged-in admin bound to Officer A."""
    from app.models import User

    app, clients = env
    with app.app_context():
        from app.extensions import db

        boss = User(
            username="boss",
            password_hash="pbkdf2:sha256$test$dummy",
            is_admin=True,
            fso_name="Officer A",
        )
        db.session.add(boss)
        db.session.commit()
        boss_id = boss.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(boss_id)  # Flask-Login key
    clients["boss"] = client
    return app, clients


class TestAdminOwner:
    def test_missing_param_edits_own_officer(self, admin_env):
        _, clients = admin_env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        body = clients["boss"].get(f"/diary/bulk?m={MONTH}").get_data(as_text=True)
        assert "Market" in body

    def test_empty_param_selects_unassigned(self, admin_env):
        _, clients = admin_env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        body = clients["boss"].get(f"/diary/bulk?m={MONTH}&fso_name=").get_data(as_text=True)
        assert "Market" not in body
        assert 'value="" selected' in body

    def test_unknown_name_falls_back_to_own(self, admin_env):
        _, clients = admin_env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        body = clients["boss"].get(f"/diary/bulk?m={MONTH}&fso_name=Nope").get_data(as_text=True)
        assert "Market" in body

    def test_admin_can_pick_another_officer(self, admin_env):
        _, clients = admin_env
        _save_day(clients["officerA"], 5, activity="field", premises=3, samples=2, notes="Market")
        _save_day(clients["officerB"], 6, activity="field", premises=9, samples=9, notes="Theirs")
        body = clients["boss"].get(f"/diary/bulk?m={MONTH}&fso_name=Officer B").get_data(as_text=True)
        assert "Theirs" in body
        assert "Market" not in body


# --------------------------------------------------------------------------- #
# Month handling
# --------------------------------------------------------------------------- #


class TestMonth:
    def test_invalid_month_falls_back_to_the_current_month(self, env):
        _, clients = env
        today = _dt.date.today()
        for bad in ("2026-13", "26-09", "", "nonsense"):
            body = clients["officerA"].get(f"/diary/bulk?m={bad}").get_data(as_text=True)
            assert f"{calendar.month_name[today.month]} {today.year}" in body

    def test_shift_month_crosses_the_year_boundary(self):
        from app.diary import _shift_month

        assert _shift_month(_dt.date(2026, 1, 1), -1) == _dt.date(2025, 12, 1)
        assert _shift_month(_dt.date(2026, 12, 1), 1) == _dt.date(2027, 1, 1)

    def test_rows_cover_every_day_of_the_month(self, env):
        _, clients = env
        body = clients["officerA"].get(f"/diary/bulk?m={MONTH}").get_data(as_text=True)
        for day in (1, 15, 31):
            assert f'name="activity_{day}"' in body
        assert 'name="activity_32"' not in body


# --------------------------------------------------------------------------- #
# Split-duty days (field + VVIP on one date)
# --------------------------------------------------------------------------- #


def _save_split_day(client, day: int, **fields) -> str:
    """POST a split-duty day: ``field`` keys target duty 1, ``second_*`` target duty 2."""
    form = _blank_form()
    for key, value in fields.items():
        if key.startswith("second_"):
            form[f"{key[7:]}2_{day}"] = str(value)
        else:
            form[f"{key}_{day}"] = str(value)
    resp = client.post(f"/diary/bulk?m={MONTH}", data=form, follow_redirects=True)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


class TestSplitDuty:
    def test_field_plus_vvip_on_one_date(self, env):
        app, clients = env
        _save_split_day(
            clients["officerA"],
            5,
            activity="field",
            premises=3,
            samples=2,
            place="Market",
            second_activity="vvip",
            second_place="Town Hall",
            second_premises=1,
        )
        first = _row(app, "Officer A", 5, 1)
        second = _row(app, "Officer A", 5, 2)
        assert (first.activity, first.premises, first.samples, first.place_of_visit) == ("field", 3, 2, "Market")
        assert (second.activity, second.premises, second.place_of_visit) == ("vvip", 1, "Town Hall")

    def test_lone_second_duty_promotes_to_slot_1(self, env):
        app, clients = env
        _save_split_day(clients["officerA"], 6, second_activity="vvip", second_place="Hall")
        assert _row(app, "Officer A", 6, 1).activity == "vvip"
        assert _row(app, "Officer A", 6, 2) is None

    def test_clearing_one_slot_keeps_the_other(self, env):
        app, clients = env
        _save_split_day(clients["officerA"], 5, activity="field", premises=3, second_activity="vvip")
        form = _blank_form()
        form.update({"activity_5": "field", "premises_5": "3", "activity2_5": "", "premises2_5": ""})
        clients["officerA"].post(f"/diary/bulk?m={MONTH}", data=form, follow_redirects=True)
        assert _row(app, "Officer A", 5, 1).activity == "field"
        assert _row(app, "Officer A", 5, 2) is None

    def test_clearing_duty_1_compacts_survivor_to_slot_1(self, env):
        """No bare seq=2 row: clearing duty 1 moves the survivor down."""
        app, clients = env
        _save_split_day(
            clients["officerA"],
            5,
            activity="field",
            premises=3,
            samples=2,
            place="Market",
            notes="morning",
            second_activity="vvip",
            second_place="Town Hall",
            second_notes="evening",
        )
        # Select "—" on duty 1 with its stored numbers re-submitted.
        form = _blank_form()
        form.update({
            "activity_5": "", "premises_5": "3", "samples_5": "2",
            "place_5": "Market", "notes_5": "morning",
            "activity2_5": "vvip", "place2_5": "Town Hall", "notes2_5": "evening",
        })
        clients["officerA"].post(f"/diary/bulk?m={MONTH}", data=form, follow_redirects=True)
        survivor = _row(app, "Officer A", 5, 1)
        assert survivor is not None
        assert (survivor.activity, survivor.place_of_visit, survivor.notes) == ("vvip", "Town Hall", "evening")
        assert _row(app, "Officer A", 5, 2) is None

    def test_holiday_clears_the_second_duty(self, env):
        app, clients = env
        _save_split_day(clients["officerA"], 5, activity="field", second_activity="vvip")
        _save_split_day(clients["officerA"], 5, activity="holiday", second_activity="vvip")
        row = _row(app, "Officer A", 5, 1)
        assert row.activity == "holiday"
        assert (row.premises, row.samples) == (0, 0)
        assert _row(app, "Officer A", 5, 2) is None

    def test_totals_sum_both_duties(self, env):
        _, clients = env
        _save_split_day(clients["officerA"], 5, activity="field", premises=3, second_activity="vvip", second_premises=1)
        body = clients["officerA"].get(f"/diary/bulk?m={MONTH}").get_data(as_text=True)
        assert "<strong>4</strong> premises inspected" in body
