"""Tests for premises resolution and schedule recording.

Covers the two seams behind "search a restaurant on NSC Bose Road, pick one,
record the duty":

- ``app/services/premises_resolver.py`` — address normalisation, road-alias
  expansion, LIKE-metacharacter escaping, the clean-no-match vs failed-lookup
  distinction (spec NFR-1), and confusable-road labelling (FR-27)
- ``app/services/diary_planner.py`` — INV-1…INV-6 (split-duty promotion,
  holiday/leave exclusivity, count zeroing, place truncation, clamping)
- ``app/schedule.py`` — the search/confirm routes and officer scoping (SEC-1)

Supabase is stubbed at the resolver seam, so no test touches the network
(NFR-2). The live behaviour was verified separately against the registry.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.services.premises_resolver import (
    PremiseCandidate,
    ResolveResult,
    RoadGroup,
    normalize_address,
)

# --------------------------------------------------------------------------- #
# Shared registry stub — every test drives the resolver through this, so no
# test touches the network (spec NFR-2).
# --------------------------------------------------------------------------- #


class _FakeResult:
    """Stand-in for a SQLAlchemy result; yields no rows by default."""

    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, capture, rows=(), error=None):
        self._capture = capture
        self._rows = rows
        self._error = error

    def execute(self, stmt, params):
        self._capture.append({"sql": str(stmt), "params": dict(params)})
        if self._error is not None:
            raise self._error
        return _FakeResult(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, capture, rows=(), error=None):
        self._capture = capture
        self._rows = rows
        self._error = error

    def connect(self):
        return _FakeConn(self._capture, self._rows, self._error)


@pytest.fixture
def captured_sql(monkeypatch):
    """Patch ``_get_engine`` and return the list of executed statements."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.services.premises_resolver._get_engine",
        lambda: _FakeEngine(calls),
    )
    return calls


@pytest.fixture
def failing_engine(monkeypatch):
    """Patch ``_get_engine`` with one that raises on connect/execute."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.services.premises_resolver._get_engine",
        lambda: _FakeEngine(calls, error=RuntimeError("connection refused")),
    )
    return calls


# --------------------------------------------------------------------------- #
# Normalisation (spec §5.6, steps N1-N3)
# --------------------------------------------------------------------------- #


class TestNormalization:
    def test_strips_dots_and_collapses_space(self):
        assert normalize_address("N. S. C  BOSE ROAD") == "N S C BOSE ROAD"

    def test_case_insensitive(self):
        assert normalize_address("n.s.c.bose road") == "N S C BOSE ROAD"

    def test_strips_control_characters(self):
        assert "\x0d" not in normalize_address("SALT\x0dLAKE")

    def test_strips_literal_x000d_artifact(self):
        # The registry carries literal "_x000d_" text, not a real control byte.
        assert "_X000D_" not in normalize_address("KOLKATA-700040,_x000d_ WARD 97")

    def test_blank_is_blank(self):
        assert normalize_address(None) == ""
        assert normalize_address("   ") == ""


# --------------------------------------------------------------------------- #
# Resolver behaviour (stubbed — no network)
# --------------------------------------------------------------------------- #


def _cand(number="1", name="ANYBELLY", addr="SHOP NO. 140/35 N.S.C. BOSE ROAD", road=RoadGroup("nsc_bose")):
    return PremiseCandidate(
        fssai_number=number,
        company_name=name,
        full_address=addr,
        expiry_date="31-12-2027",
        source_table="fssai_registrations",
        road=road,
    )


class TestQuerySplitting:
    """'balaram mullick in NSC Bose Road' must become two filters, not one
    literal phrase (which appears in neither column)."""

    def test_splits_name_from_locality(self):
        from app.services.premises_resolver import split_query

        assert split_query("balaram mullick in NSC Bose Road") == (
            "BALARAM MULLICK",
            "NSC BOSE ROAD",
        )

    @pytest.mark.parametrize(
        "connective",
        ["in", "at", "near", "on", "IN", "AT"],
    )
    def test_handles_common_connectives(self, connective):
        from app.services.premises_resolver import split_query

        name, locality = split_query(f"balaram mullick {connective} NSC Bose Road")
        assert name == "BALARAM MULLICK"
        assert locality == "NSC BOSE ROAD"

    def test_generic_category_word_is_dropped(self):
        """'restaurant in X' means anything on X — not a name filter."""
        from app.services.premises_resolver import split_query

        name, locality = split_query("restaurant in NSC Bose Road")
        assert name == ""
        assert locality == "NSC BOSE ROAD"

    def test_real_name_is_kept_alongside_generic_words(self):
        from app.services.premises_resolver import split_query

        name, locality = split_query("balaram mullick restaurant in NSC Bose Road")
        assert name == "BALARAM MULLICK"
        assert locality == "NSC BOSE ROAD"

    def test_no_connective_returns_whole_as_name(self):
        from app.services.premises_resolver import split_query

        name, locality = split_query("balaram mullick")
        assert name == "BALARAM MULLICK"
        assert locality == ""

    def test_leading_connective_is_not_a_split(self):
        from app.services.premises_resolver import split_query

        _name, locality = split_query("in NSC Bose Road")
        assert locality == ""


class TestLocalityDetection:
    def test_road_is_recognised_as_a_place(self):
        from app.services.premises_resolver import looks_like_locality

        assert looks_like_locality("NSC Bose Road") is True
        assert looks_like_locality("Sarat Bose Road") is True
        assert looks_like_locality("Salt Lake Sector V") is True

    def test_business_name_is_not_a_place(self):
        from app.services.premises_resolver import looks_like_locality

        assert looks_like_locality("balaram mullick") is False
        assert looks_like_locality("anybelly") is False


class TestNameAndLocalityAreIndependentFilters:
    """The critical property: a name+road query must NOT silently degrade to a
    road-only search, which would answer 'Balaram Mullick in NSC Bose Road'
    with every business on that road."""

    def test_name_and_road_are_both_bound(self, captured_sql):
        from app.services import premises_resolver as pr

        pr.resolve("balaram mullick in NSC Bose Road")
        assert captured_sql
        sql, params = captured_sql[0]["sql"], captured_sql[0]["params"]
        # Name tokens bound against company_name...
        assert any("BALARAM" in str(v) for v in params.values())
        assert any("MULLICK" in str(v) for v in params.values())
        # ...and the road bound against full_address.
        assert any("BOSE" in str(v) for v in params.values())
        assert "company_name ILIKE :n0" in sql
        assert "full_address ILIKE :b0" in sql

    def test_name_only_query_binds_no_address_filter(self, captured_sql):
        from app.services import premises_resolver as pr

        pr.resolve("balaram mullick")
        assert captured_sql
        assert "full_address ILIKE" not in captured_sql[0]["sql"]

    def test_generic_category_query_stays_loose(self, captured_sql):
        from app.services import premises_resolver as pr

        pr.resolve("restaurant in NSC Bose Road")
        assert captured_sql
        sql = captured_sql[0]["sql"]
        # No company_name-only AND filter (the category word was dropped).
        assert "company_name ILIKE :n0" not in sql
        assert "company_name ILIKE :b0" in sql

    def test_name_filter_is_reported_back(self, captured_sql):
        from app.services import premises_resolver as pr

        result = pr.resolve("balaram mullick in NSC Bose Road")
        assert result.name_filter == "BALARAM MULLICK"
        assert result.locality_filter == "NSC BOSE ROAD"


class TestResolveResultContract:
    def test_clean_no_match_is_distinguishable_from_failure(self):
        """NFR-1: found=False + error=None must mean 'matched nothing'."""
        empty = ResolveResult(False)
        failed = ResolveResult(False, error="boom")
        assert not empty.found and empty.error is None
        assert not failed.found and failed.error == "boom"

    def test_truthiness_follows_found(self):
        assert bool(ResolveResult(True)) is True
        assert bool(ResolveResult(False)) is False


class TestResolve:
    def test_unconfigured_returns_error_not_empty_success(self, monkeypatch):
        from app.services import premises_resolver as pr

        monkeypatch.setattr(pr, "_get_engine", lambda: None)
        result = pr.resolve("anything")
        assert result.error == "Supabase is not configured."
        assert result.found is False

    def test_empty_query_does_not_hit_the_database(self, monkeypatch):
        from app.services import premises_resolver as pr

        def _boom():
            raise AssertionError("must not query")

        monkeypatch.setattr(pr, "_get_engine", _boom)
        assert pr.resolve("   ").found is False

    def test_escapes_like_metacharacters(self, captured_sql):
        """FR-9: a user-typed '%' must not widen the scan to a full table."""
        from app.services import premises_resolver as pr

        pr.resolve("100%")
        assert captured_sql
        assert all("\\%" in v for v in captured_sql[0]["params"].values() if isinstance(v, str))

    def test_query_failure_is_returned_as_error(self, failing_engine):
        from app.services import premises_resolver as pr

        result = pr.resolve("NSC Bose Road")
        assert result.found is False
        assert "connection refused" in (result.error or "")
        assert result.candidates == ()

    def test_alias_expansion_finds_punctuated_variants(self, captured_sql):
        """The registry spells one road many ways; all must be OR-ed in."""
        from app.services import premises_resolver as pr

        result = pr.resolve("restaurant in NSC Bose Road")
        assert result.road_group == "nsc_bose"
        patterns = [v for v in captured_sql[0]["params"].values() if isinstance(v, str)]
        assert any("N.S.C" in p for p in patterns)
        assert any("NETAJI SUBHAS CHANDRA" in p for p in patterns)


class TestRoadGrouping:
    def test_canonical_road_is_recognised(self):
        from app.services.premises_resolver import _road_group_for_query

        assert _road_group_for_query("restaurant in NSC Bose Road") == "nsc_bose"
        assert _road_group_for_query("N. S. C. Bose Road") == "nsc_bose"

    def test_unknown_road_is_not_claimed(self):
        from app.services.premises_resolver import _road_group_for_query

        assert _road_group_for_query("Salt Lake Sector V") is None

    def test_confusable_roads_are_labelled_separately(self):
        """FR-27: Sarat Bose Road must never be silently merged into NSC."""
        from app.services.premises_resolver import _classify_road

        assert _classify_road("63/1/1A SARAT BOSE ROAD") == RoadGroup("sarat_bose", is_confusable=True)
        assert _classify_road("SHOP 140 NSC BOSE ROAD") == RoadGroup("nsc_bose")
        assert _classify_road("SOMEWHERE ELSE") == RoadGroup()


# --------------------------------------------------------------------------- #
# DiaryPlanner invariants
# --------------------------------------------------------------------------- #


@pytest.fixture
def diary_app():
    from app import create_app
    from app.extensions import db

    app = create_app()
    app.config["TESTING"] = True
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


DAY = "2026-03-10"


class TestDiaryPlannerInvariants:
    def test_writes_a_slot(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", premises=2, place="NSC Bose Road")
        db.session.commit()
        row = db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 1})
        assert row is not None
        assert row.activity == "field"
        assert row.premises == 2
        assert row.place_of_visit == "NSC Bose Road"

    def test_unknown_activity_is_a_noop(self, diary_app):
        from app.extensions import db
        from app.services import diary_planner

        assert diary_planner.record("Officer A", DAY, 1, "not-an-activity") == "noop"
        db.session.commit()

    def test_inv3_non_counting_activity_zeroes_counts(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "holiday", premises=7, samples=3)
        db.session.commit()
        row = db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 1})
        assert (row.premises, row.samples) == (0, 0)

    def test_inv5_place_is_truncated_and_empty_becomes_null(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", place="x" * 250)
        db.session.commit()
        row = db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 1})
        assert len(row.place_of_visit) == 200

        diary_planner.record("Officer B", DAY, 1, "field", place="")
        db.session.commit()
        row_b = db.session.get(WorkDiaryEntry, {"fso_name": "Officer B", "work_date": DAY, "duty_seq": 1})
        assert row_b.place_of_visit is None

    def test_inv6_counts_are_clamped(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", premises=99_999_999)
        db.session.commit()
        row = db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 1})
        assert row.premises == 999_999

    def test_inv1_lone_second_slot_promotes_to_slot_one(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record_many(
            "Officer A",
            DAY,
            [{"duty_seq": 2, "activity": "field", "premises": 1, "place": "NSC"}],
        )
        db.session.commit()
        assert db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 2}) is None
        first = db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 1})
        assert first is not None and first.activity == "field"

    def test_inv2_holiday_clears_a_second_duty(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record_many(
            "Officer A",
            DAY,
            [
                {"duty_seq": 1, "activity": "holiday"},
                {"duty_seq": 2, "activity": "field", "premises": 4},
            ],
        )
        db.session.commit()
        assert db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 2}) is None

    def test_inv2_exclusive_slot_two_loses_to_working_slot_one(self, diary_app):
        from app.extensions import db
        from app.models import WorkDiaryEntry
        from app.services import diary_planner

        diary_planner.record_many(
            "Officer A",
            DAY,
            [
                {"duty_seq": 1, "activity": "field", "premises": 2},
                {"duty_seq": 2, "activity": "leave"},
            ],
        )
        db.session.commit()
        assert db.session.get(WorkDiaryEntry, {"fso_name": "Officer A", "work_date": DAY, "duty_seq": 2}) is None

    def test_slots_for_reports_stored_day(self, diary_app):
        from app.extensions import db
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", premises=1, place="NSC Bose Road")
        db.session.commit()
        slots = diary_planner.slots_for("Officer A", DAY)
        assert slots[1]["premises"] == 1
        assert slots[1]["place_of_visit"] == "NSC Bose Road"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def schedule_env(monkeypatch):
    from app import create_app
    from app.extensions import db
    from app.models import FSO, Role, User
    from app.services import premises_resolver as pr

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    db.session.add(FSO(fso_name="Officer A"))
    db.session.add(Role(name="fso"))
    user = User(
        username="officerA",
        password_hash="pbkdf2:sha256$test$dummy",
        is_admin=False,
        fso_name="Officer A",
    )
    db.session.add(user)
    db.session.commit()
    db.session.query(User).first().roles.append(db.session.query(Role).first())
    db.session.commit()
    uid = db.session.query(User).first().id
    db.session.remove()
    ctx.pop()

    # Stub the registry so no test touches the network (NFR-2).
    monkeypatch.setattr(
        pr,
        "resolve",
        lambda q, **_k: ResolveResult(True, candidates=(_cand(number="12345678901234"),), road_group="nsc_bose"),
    )
    monkeypatch.setattr(
        pr,
        "search_by_fssai",
        lambda n: ResolveResult(True, candidates=(_cand(number=n),)),
    )

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
    yield app, client
    with app.app_context():
        db.session.remove()
        db.drop_all()


class TestScheduleRoutes:
    def test_requires_login(self):
        from app import create_app

        app = create_app()
        app.config["TESTING"] = True
        resp = app.test_client().get("/schedule/search?q=test")
        assert resp.status_code in (301, 302)

    def test_schedule_is_in_the_fso_role_map(self):
        """RBAC is disabled session-wide in conftest, so the route tests above
        cannot catch a missing ROLE_BLUEPRINTS entry. Assert the map directly —
        without this, an FSO is bounced with 'You do not have access'."""
        from app.shared.rbac import FSO_ROLE, ROLE_BLUEPRINTS

        assert "schedule" in ROLE_BLUEPRINTS[FSO_ROLE]

    def test_search_renders_candidates(self, schedule_env):
        _, client = schedule_env
        resp = client.get("/schedule/search?q=restaurant+in+NSC+Bose+Road")
        assert resp.status_code == 200
        assert b"ANYBELLY" in resp.data

    def test_search_logs_the_lookup_failure(self, schedule_env, monkeypatch):
        """The route must not swallow ``result.error`` — production diagnosis
        depends on this line. Observed 2026-10-13: the red 'Lookup failed' box
        appeared while the server logs stayed completely silent because the
        failure reason never reached them.
        """
        from app.services import premises_resolver as pr

        app, client = schedule_env
        monkeypatch.setattr(
            pr,
            "resolve",
            lambda q, **_k: ResolveResult(False, error="Supabase is not configured."),
        )
        # Spy on the logger — the app's structlog setup makes caplog unreliable
        # for this logger (see tests/test_rag_ui_gaps.py:262).
        captured: list[str] = []
        original_warning = app.logger.warning

        def _spy(msg, *args, **kwargs):
            captured.append(str(msg))
            return original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(app.logger, "warning", _spy)
        resp = client.get("/schedule/search?q=devyani")
        assert resp.status_code == 200
        assert b"Lookup failed" in resp.data
        assert any("Supabase is not configured" in m for m in captured)

    def test_confirm_writes_diary_slot_and_inspection(self, schedule_env):
        from app.models import Inspection, WorkDiaryEntry

        app, client = schedule_env
        resp = client.post(
            "/schedule/confirm",
            data={
                "q": "restaurant in NSC Bose Road",
                "work_date": DAY,
                "notes": "",
                "fssai_number": "12345678901234",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = WorkDiaryEntry.query.filter_by(fso_name="Officer A", work_date=DAY).first()
            assert entry is not None
            assert entry.activity == "field"
            assert entry.premises == 1
            ins = Inspection.query.filter_by(fso_name="Officer A").all()
            assert len(ins) == 1
            assert ins[0].fbo_name == "ANYBELLY"
            assert ins[0].visit_purpose == "routine"

    def test_confirm_without_selection_writes_nothing(self, schedule_env):
        from app.models import Inspection, WorkDiaryEntry

        app, client = schedule_env
        client.post("/schedule/confirm", data={"q": "x", "work_date": DAY})
        with app.app_context():
            assert WorkDiaryEntry.query.filter_by(work_date=DAY).count() == 0
            assert Inspection.query.count() == 0

    def test_confirm_ignores_forged_company_name(self, schedule_env):
        """The client may echo a name; the server re-resolves by FSSAI number."""
        from app.models import Inspection

        app, client = schedule_env
        client.post(
            "/schedule/confirm",
            data={
                "work_date": DAY,
                "fssai_number": "12345678901234",
                "fbo_name": "TOTALLY FORGED NAME",
            },
        )
        with app.app_context():
            ins = Inspection.query.filter_by(fso_name="Officer A").first()
            assert ins is not None
            assert ins.fbo_name == "ANYBELLY"
            assert ins.fbo_name != "TOTALLY FORGED NAME"

    def test_confirm_is_idempotent_for_the_same_day(self, schedule_env):
        from app.models import Inspection

        app, client = schedule_env
        for _ in range(2):
            client.post("/schedule/confirm", data={"work_date": DAY, "fssai_number": "12345678901234"})
        with app.app_context():
            assert Inspection.query.filter_by(fssai_license="12345678901234").count() == 1


def test_today_format_is_iso():
    assert _dt.date.today().isoformat() == _dt.date.today().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Audit coverage (FR-25 / AUD-1)
# --------------------------------------------------------------------------- #


class TestDiaryAuditTrail:
    """`WorkDiaryEntry` is the first audited model with no `id` column. The
    hook must derive an identifier from its composite primary key rather than
    raising on ``target.id``."""

    @pytest.fixture
    def audited_app(self):
        from app import audit_hooks, create_app
        from app.extensions import db

        app = create_app()
        app.config["TESTING"] = True
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        audit_hooks.register_audit_hooks()
        yield app
        db.session.remove()
        db.drop_all()
        ctx.pop()

    def test_write_creates_an_audit_row(self, audited_app):
        from app.extensions import db
        from app.models import RecordAudit
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", premises=2, place="NSC Bose Road")
        db.session.commit()
        audit = RecordAudit.query.filter_by(record_type="WorkDiaryEntry").first()
        assert audit is not None
        assert audit.action == "create"
        # Composite key rendered into a stable identifier.
        assert "fso_name=Officer A" in audit.record_id
        assert "work_date=2026-03-10" in audit.record_id
        assert "duty_seq=1" in audit.record_id

    def test_update_is_recorded(self, audited_app):
        from app.extensions import db
        from app.models import RecordAudit
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", premises=2)
        db.session.commit()
        diary_planner.record("Officer A", DAY, 1, "field", premises=5)
        db.session.commit()
        actions = {r.action for r in RecordAudit.query.filter_by(record_type="WorkDiaryEntry").all()}
        assert actions == {"create", "update"}

    def test_delete_is_recorded(self, audited_app):
        from app.extensions import db
        from app.models import RecordAudit
        from app.services import diary_planner

        diary_planner.record("Officer A", DAY, 1, "field", premises=2)
        db.session.commit()
        diary_planner.record("Officer A", DAY, 1, "", 0, 0, "", "")
        db.session.commit()
        actions = [r.action for r in RecordAudit.query.filter_by(record_type="WorkDiaryEntry").all()]
        assert "delete" in actions

    def test_single_pk_models_are_unaffected(self, audited_app):
        """Existing audited models still resolve their plain ``id``."""
        from app.audit_hooks import _record_identifier
        from app.models import Adjudication

        assert _record_identifier(Adjudication(id=17)) == 17

    def test_long_officer_name_identifier_is_stored_intact(self, audited_app):
        """Regression for the production 500: a long officer name makes the
        composite-key audit identifier exceed the old VARCHAR(50).

        ``fso_name=Suman Saha|work_date=2026-03-10|duty_seq=1`` is 51 chars —
        one over the old limit — so on Postgres the audit insert used to
        abort the diary save's whole transaction. The column is now
        VARCHAR(255); the identifier must be stored in full.

        SQLite (the test DB) does not enforce VARCHAR(n), so it cannot
        reproduce the exact Postgres ``StringDataRightTruncation``; we assert
        on the identifier length instead as a durable proxy for that error.
        """
        from app.extensions import db
        from app.models import RecordAudit
        from app.services import diary_planner

        diary_planner.record("Suman Saha", DAY, 1, "field", premises=2)
        db.session.commit()
        audit = RecordAudit.query.filter_by(record_type="WorkDiaryEntry").first()
        assert audit is not None
        # Full composite key rendered, nothing dropped.
        assert audit.record_id == f"fso_name=Suman Saha|work_date={DAY}|duty_seq=1"
        # Over the old VARCHAR(50) limit, but comfortably within VARCHAR(255).
        assert 50 < len(audit.record_id) <= 255

    def test_overlong_identifier_is_capped_and_logged_not_raised(self, audited_app, monkeypatch):
        """The length guard's cap branch must truncate + warn, not raise.

        A future identifier format that outgrows the column must never abort
        the caller's transaction (that was the original production 500), and a
        genuinely over-long identifier must be logged so it is never silently
        mutated into something that looks like a real, distinct record."""
        from app import audit_hooks
        from app.audit_hooks import _MAX_RECORD_ID_LEN, _record_audit
        from app.extensions import db
        from app.models import RecordAudit

        # Spy on the logger (caplog is unreliable here: the app's logging
        # config can suppress propagation for this logger — same caveat as
        # tests/test_rag_ui_gaps.py).
        warnings_seen: list[str] = []
        original_warning = audit_hooks.logger.warning

        def _spy(msg, *args, **kwargs):
            warnings_seen.append(str(msg))
            return original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(audit_hooks.logger, "warning", _spy)

        overlong = "x" * (_MAX_RECORD_ID_LEN + 500)
        # Must not raise even though the identifier exceeds the column.
        _record_audit("create", "WorkDiaryEntry", overlong)
        db.session.commit()

        audit = RecordAudit.query.filter_by(record_type="WorkDiaryEntry").first()
        assert audit is not None
        # Stored identifier is capped at the column width, nothing dropped
        # below the cap.
        assert audit.record_id == overlong[:_MAX_RECORD_ID_LEN]
        assert len(audit.record_id) == _MAX_RECORD_ID_LEN
        # And the truncation was logged so it is not silent.
        assert any("truncated" in w for w in warnings_seen)
