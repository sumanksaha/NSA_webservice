"""Tests for probabilistic photo-evidence assessment (ADR-0011).

Covers the four seams the module exists to hold: the **derived vocabulary** (no
checklist item re-declared, the two document-checks excluded), the **single
polarity inversion** (round-tripped against ``derive_violations``), the
**abstention gate** (a non-premises image must score nothing, which is the
failure a ``noul``-only decision model makes by default), and **fail-closed**
parsing.

Pure seams run with no app and no network. The scorer, service, and route seams
use a stubbed HTTP client. Expected values are literals from the FSSAI
checklist, not recomputed from the code under test.
"""

import json
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Vocabulary — derived, not re-declared
# ---------------------------------------------------------------------------


def test_scorable_fields_exclude_the_two_document_checks():
    """A photo cannot establish a pest-control record or a water test report."""
    from app.inspection.photo_scoring import PHOTO_UNOBSERVABLE_FIELDS

    assert set(PHOTO_UNOBSERVABLE_FIELDS) == {"Pest_report", "Water_report"}


def test_scorable_and_excluded_partition_the_checklist():
    """Adding a checklist item must get it scored without editing the scorer."""
    from app.inspection.photo_scoring import PHOTO_UNOBSERVABLE_FIELDS, SCORABLE_FIELDS
    from app.shared.context_derivers import CHECKLIST_FIELDS

    assert set(SCORABLE_FIELDS) | PHOTO_UNOBSERVABLE_FIELDS == set(CHECKLIST_FIELDS)
    assert not set(SCORABLE_FIELDS) & PHOTO_UNOBSERVABLE_FIELDS


def test_no_duplicate_scorable_fields():
    from app.inspection.photo_scoring import SCORABLE_FIELDS

    assert len(SCORABLE_FIELDS) == len(set(SCORABLE_FIELDS))


def test_inverted_fields_match_the_special_violation_table():
    from app.inspection.photo_scoring import INVERTED_FORM_VALUE_FIELDS
    from app.shared.context_derivers import SPECIAL_VIOLATION_RULES

    assert set(INVERTED_FORM_VALUE_FIELDS) == set(SPECIAL_VIOLATION_RULES)


def test_every_question_map_covers_the_gate_and_all_candidates():
    """One call, one image: the gate and the candidates cannot disagree."""
    from app.inspection.photo_scoring import SCORABLE_FIELDS, build_questions

    q = build_questions()
    assert set(q) == {"premises_photograph", *SCORABLE_FIELDS}
    assert all(v["type"] == "noul" for v in q.values())


# ---------------------------------------------------------------------------
# Polarity — the one inversion point, round-tripped against the deriver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["clean_premise", "refrigerator_clean", "proper_attire"])
def test_checklist_violation_records_as_no(field):
    from app.inspection.photo_scoring import form_value_for_violation

    assert form_value_for_violation(field, violation_present=True) == "no"
    assert form_value_for_violation(field, violation_present=False) == "yes"


@pytest.mark.parametrize("field", ["artificial_colour", "Expired_item"])
def test_special_violation_records_as_yes(field):
    from app.inspection.photo_scoring import form_value_for_violation

    assert form_value_for_violation(field, violation_present=True) == "yes"
    assert form_value_for_violation(field, violation_present=False) == "no"


def test_polarity_round_trips_through_derive_violations():
    """What we store must read back the same way ``derive_violations`` reads it."""
    from app.inspection.photo_scoring import SCORABLE_FIELDS, form_value_for_violation
    from app.shared.context_derivers import derive_violations

    for field in SCORABLE_FIELDS:
        for present in (True, False):
            value = form_value_for_violation(field, violation_present=present)
            readback = any(v["field"] == field for v in derive_violations({field: value}))
            assert readback is present, f"{field}={value} read back {readback}, expected {present}"


# ---------------------------------------------------------------------------
# Parsing — the gate first, then candidates
# ---------------------------------------------------------------------------


def _answers(gate=0.9, **overrides):
    """A realistic ``/v1/decisions`` answers map.

    Keyword overrides are per-field probabilities, e.g. ``_answers(clean_premise=0.93)``;
    pass a dict directly to exercise a malformed answer.
    """
    from app.inspection.photo_scoring import SCORABLE_FIELDS

    answers = {"premises_photograph": {"type": "noul", "noul": gate}}
    for f in SCORABLE_FIELDS:
        answers[f] = {"type": "noul", "noul": 0.1}
    for name, value in overrides.items():
        answers[name] = value if isinstance(value, dict) else {"type": "noul", "noul": value}
    return answers


def test_parse_reads_a_gated_in_response():
    from app.inspection.photo_scoring import parse_decision_response

    result = parse_decision_response(_answers(gate=0.98))
    assert result.ok is True
    assert result.gate_passed is True
    assert result.overall_observable is True
    assert len(result.items) == 10
    assert result.error is None
    assert all(i.observable and i.p_violation == 0.1 for i in result.items)


def test_gate_blocks_a_non_premises_image():
    """The measured failure mode: a noul must answer, so the gate supplies abstention."""
    from app.inspection.photo_scoring import GATE_THRESHOLD, parse_decision_response

    # The model "finds" a violation, but the gate says this is not a premises photo.
    result = parse_decision_response(_answers(gate=0.001, clean_premise=0.91))
    assert result.ok is True
    assert result.gate_passed is False
    assert result.overall_observable is False
    assert result.suggested_fields(0.70) == []
    assert all(i.p_violation is None for i in result.items)
    assert "not a scorable premises" in result.error
    assert GATE_THRESHOLD > 0.001


def test_gate_exactly_at_threshold_passes():
    from app.inspection.photo_scoring import GATE_THRESHOLD, parse_decision_response

    result = parse_decision_response(_answers(gate=GATE_THRESHOLD))
    assert result.gate_passed is True


def test_missing_gate_fails_closed():
    """A response with no gate answer must not be read as scorable."""
    from app.inspection.photo_scoring import SCORABLE_FIELDS, parse_decision_response

    answers = _answers()
    del answers["premises_photograph"]
    result = parse_decision_response(answers)
    assert result.ok is False
    assert result.gate_passed is False
    assert all(i.p_violation is None for i in result.items)
    assert len(result.items) == len(SCORABLE_FIELDS)


def test_non_numeric_gate_fails_closed():
    from app.inspection.photo_scoring import parse_decision_response

    answers = _answers()
    answers["premises_photograph"] = {"type": "noul", "noul": "yes"}
    assert parse_decision_response(answers).ok is False


def test_non_object_response_fails_closed():
    from app.inspection.photo_scoring import SCORABLE_FIELDS, parse_decision_response

    result = parse_decision_response(["not", "an", "object"])
    assert result.ok is False
    assert result.gate_passed is False
    assert len(result.items) == len(SCORABLE_FIELDS)
    assert all(i.p_violation is None for i in result.items)


def test_missing_candidate_becomes_none_and_is_reported():
    from app.inspection.photo_scoring import parse_decision_response

    answers = _answers()
    dropped = "license_display"
    del answers[dropped]
    result = parse_decision_response(answers)
    assert result.ok is True
    assert result.by_field()[dropped].observable is False
    assert result.by_field()[dropped].p_violation is None
    assert dropped in result.error


def test_probabilities_are_clamped():
    from app.inspection.photo_scoring import parse_decision_response

    result = parse_decision_response(_answers(clean_premise=7.5, refrigerator_clean=-3))
    assert result.by_field()["clean_premise"].p_violation == 1.0
    assert result.by_field()["refrigerator_clean"].p_violation == 0.0


def test_fields_outside_the_scorable_set_are_ignored():
    from app.inspection.photo_scoring import parse_decision_response

    result = parse_decision_response(_answers(not_a_real_field=0.99))
    assert "not_a_real_field" not in result.by_field()
    assert len(result.items) == 10


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def test_suggested_fields_respects_the_threshold():
    from app.inspection.photo_scoring import parse_decision_response

    result = parse_decision_response(_answers(clean_premise=0.85))
    assert result.suggested_fields(0.70) == ["clean_premise"]
    assert result.suggested_fields(0.95) == []


def test_unobservable_item_is_never_suggested():
    from app.inspection.photo_scoring import parse_decision_response

    answers = _answers()
    del answers["clean_premise"]
    assert "clean_premise" not in parse_decision_response(answers).suggested_fields(0.50)


# ---------------------------------------------------------------------------
# Scorer transport (stubbed HTTP)
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _StubClient:
    """Stubbed httpx.Client: intercepts the image GET and the decisions POST."""

    def __init__(self, answers=None, status=200, image_bytes=b"\xff\xd8\xff\xe0JFIF", raises=None):
        self.answers = answers
        self.status = status
        self.image_bytes = image_bytes
        self.raises = raises
        self.posts: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        if self.raises:
            raise self.raises
        return _StubImage(self.image_bytes)

    def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        if self.raises:
            raise self.raises
        if self.status != 200:
            return _StubResponse({"error": "boom"}, status=self.status)
        return _StubResponse({
            "model": "inception/mercury-decide-20260930",
            "answers": self.answers if self.answers is not None else _answers(),
            "usage": {"input_tokens": 600, "output_tokens": 11},
        })


class _StubImage:
    def __init__(self, content):
        self.content = content
        self.status_code = 200
        self.headers = {"content-type": "image/jpeg"}


@pytest.fixture
def scoring_env(monkeypatch):
    monkeypatch.setenv("PHOTO_SCORING_ENABLED", "true")
    monkeypatch.setenv("PHOTO_SCORING_MODEL", "inception/mercury-decide:free")
    monkeypatch.setenv("PHOTO_SCORING_ENDPOINT", "https://openrouter.ai/api/v1/systemone")
    monkeypatch.setenv("PHOTO_SCORING_THRESHOLD", "0.70")
    monkeypatch.setenv("PHOTO_SCORING_TIMEOUT", "30")
    monkeypatch.setenv("PHOTO_SCORING_MAX_BYTES", "4000000")
    monkeypatch.setenv("AI_ASSISTANT_API_KEY", "test-key-not-real")


def _patch_httpx(monkeypatch, client):
    """Point every httpx.Client() the scorer builds at *client*."""
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)


def test_score_url_sends_the_image_as_a_data_uri(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    client = _StubClient()
    _patch_httpx(monkeypatch, client)
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")

    sent = client.posts[0]["json"]
    state = sent["state"][0]
    assert state["type"] == "image_url"
    assert state["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert sent["model"] == "inception/mercury-decide:free"
    assert result.ok is True


def test_score_url_records_the_resolved_model(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    _patch_httpx(monkeypatch, _StubClient())
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")
    assert result.provider_model == "inception/mercury-decide:free"
    assert result.resolved_model == "inception/mercury-decide-20260930"
    assert result.token_usage == 611


def test_score_url_rejects_a_nonexistent_local_path(scoring_env, monkeypatch):
    """A local path that does not exist as a file cannot be scored."""
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    result = PhotoEvidenceScorer().score_url(r"C:\temp\uploads\e.jpg")
    assert result.ok is False
    assert "could not read image" in result.error


def test_score_url_reads_a_local_file(scoring_env, monkeypatch, tmp_path):
    """An existing local image file is read and base64-encoded for scoring."""
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    img = tmp_path / "premises.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake_jpeg")
    monkeypatch.setenv("AI_ASSISTANT_API_KEY", "test-key")
    result = PhotoEvidenceScorer().score_url(str(img))
    # The file exists but isn't a real image; the decision endpoint call would
    # be attempted. Without httpx patched it may reach the network — we only
    # assert it got past the local-file read stage (ok may be True or False
    # depending on provider, but not a "could not read image" failure).
    assert "could not read image" not in (result.error or "")


def test_score_url_fails_closed_on_http_error(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    _patch_httpx(monkeypatch, _StubClient(status=500))
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")
    assert result.ok is False
    assert result.gate_passed is False
    assert "HTTP 500" in result.error


def test_score_url_fails_closed_when_the_image_cannot_be_read(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    _patch_httpx(monkeypatch, _StubClient(raises=RuntimeError("connection reset")))
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")
    assert result.ok is False
    assert "could not read image" in result.error


def test_score_url_fails_closed_without_an_api_key(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    monkeypatch.setenv("AI_ASSISTANT_API_KEY", "")
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")
    assert result.ok is False
    assert "no API key" in result.error


def test_score_raises_when_disabled(monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    monkeypatch.setenv("PHOTO_SCORING_ENABLED", "false")
    with pytest.raises(ValueError, match="disabled"):
        PhotoEvidenceScorer().score(object())


def test_image_over_the_limit_is_rejected(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    monkeypatch.setenv("PHOTO_SCORING_MAX_BYTES", "100")
    client = _StubClient(image_bytes=b"x" * 5000)
    _patch_httpx(monkeypatch, client)
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")
    assert result.ok is False
    assert "over the" in result.error
    assert client.posts == [], "an oversize image must not be sent"


def test_non_image_content_type_is_rejected(scoring_env, monkeypatch):
    from app.inspection.photo_scoring import PhotoEvidenceScorer

    class _HTML(_StubImage):
        def __init__(self, content):
            super().__init__(content)
            # Instance attr, so it must be set here — a class attr would be
            # shadowed by _StubImage.__init__.
            self.headers = {"content-type": "text/html"}

    class _C(_StubClient):
        def get(self, url, **kw):
            return _HTML(b"<html>")

    _patch_httpx(monkeypatch, _C())
    result = PhotoEvidenceScorer().score_url("https://res.cloudinary.com/demo/x.jpg")
    assert result.ok is False
    assert "not an image" in result.error


# ---------------------------------------------------------------------------
# App-backed seams
# ---------------------------------------------------------------------------


def _enable_env(monkeypatch, enabled="true"):
    """Export before ``create_app`` — ``cfg`` reads ``app.config`` in-context."""
    monkeypatch.setenv("PHOTO_SCORING_ENABLED", enabled)
    monkeypatch.setenv("PHOTO_SCORING_MODEL", "inception/mercury-decide:free")
    monkeypatch.setenv("PHOTO_SCORING_ENDPOINT", "https://openrouter.ai/api/v1/systemone")
    monkeypatch.setenv("PHOTO_SCORING_THRESHOLD", "0.70")
    monkeypatch.setenv("PHOTO_SCORING_TIMEOUT", "30")
    monkeypatch.setenv("PHOTO_SCORING_MAX_BYTES", "4000000")
    monkeypatch.setenv("AI_ASSISTANT_API_KEY", "test-key-not-real")


@pytest.fixture
def db_app(monkeypatch):
    """Fresh app, scoring ON, admin user id=1, one adjudication-scoped photo."""
    from app import create_app
    from app.extensions import db as _db
    from app.models import Adjudication, Evidence, FSO, User

    _enable_env(monkeypatch)

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        _db.create_all()
        _db.session.add(User(username="scoreruser", password_hash="pbkdf2:sha256$test$dummy"))
        _db.session.add(FSO(fso_name="Test Officer"))
        adj = Adjudication(
            case_number="A/2026/1", food_safety_officer="Test Officer",
            fbo_owner="Owner", fbo_name="Shop", fbo_address="Addr",
            fssai_license="10000000000000",
            First_inspection_date=datetime(2026, 1, 1),
            compliance_deadline=datetime(2026, 1, 16),
            inspection_date=datetime(2026, 1, 16),
        )
        _db.session.add(adj)
        _db.session.commit()
        photo = Evidence(
            adjudication_id=adj.id, evidence_type="photo",
            filepath="https://res.cloudinary.com/demo/image/upload/x.jpg", filename="x.jpg",
        )
        _db.session.add(photo)
        _db.session.commit()
        ids = {"adjudication_id": adj.id, "photo_id": photo.id}

    yield app, ids

    with app.app_context():
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(db_app):
    app, _ids = db_app
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        yield c


def _add_non_photo(app):
    from app.extensions import db
    from app.models import Evidence

    with app.app_context():
        row = Evidence(evidence_type="lab_report",
                       filepath="https://res.cloudinary.com/demo/x.pdf", filename="x.pdf")
        db.session.add(row)
        db.session.commit()
        return row.id


# -- pre-save flow ---------------------------------------------------------- #


def test_upload_scores_before_saving(db_app, monkeypatch):
    """One transaction: the photo and its assessment both land together."""
    from app.extensions import db
    from app.inspection.photo_service import InspectionPhotoService
    from app.models import Evidence, EvidenceAssessment

    client_stub = _StubClient(answers=_answers(gate=0.98, clean_premise=0.93, Expired_item=0.81))
    _patch_httpx(monkeypatch, client_stub)
    monkeypatch.setattr(
        "app.inspection.photo_service.upload_photo",
        lambda file_obj, adj_id, name: f"https://res.cloudinary.com/demo/{adj_id}/{name}",
    )

    app, ids = db_app
    with app.app_context():
        from werkzeug.datastructures import FileStorage
        from io import BytesIO

        fs = FileStorage(stream=BytesIO(b"\xff\xd8\xff\xe0JFIF-data"), filename="shot.jpg")
        result = InspectionPhotoService().upload_adjudication_photo(ids["adjudication_id"], fs)

        assert result.scoring is not None
        assert result.scoring.ok is True
        assert result.scoring.by_field()["clean_premise"].p_violation == 0.93

        rows = EvidenceAssessment.query.filter_by(evidence_id=result.photo_id).all()
        assert len(rows) == 1
        assert rows[0].resolved_model == "inception/mercury-decide-20260930"
        assert json.loads(rows[0].suggested_fields) == ["clean_premise", "Expired_item"]
        assert db.session.get(Evidence, result.photo_id) is not None
        db.session.remove()


def test_upload_saves_the_photo_even_when_scoring_fails(db_app, monkeypatch):
    """A provider problem must never cost the evidence."""
    from app.extensions import db
    from app.inspection.photo_service import InspectionPhotoService
    from app.models import Evidence, EvidenceAssessment

    _patch_httpx(monkeypatch, _StubClient(raises=RuntimeError("provider down")))
    monkeypatch.setattr(
        "app.inspection.photo_service.upload_photo",
        lambda file_obj, adj_id, name: f"https://res.cloudinary.com/demo/{adj_id}/{name}",
    )

    app, ids = db_app
    with app.app_context():
        from io import BytesIO

        from werkzeug.datastructures import FileStorage

        fs = FileStorage(stream=BytesIO(b"\xff\xd8\xff\xe0JFIF"), filename="shot.jpg")
        result = InspectionPhotoService().upload_adjudication_photo(ids["adjudication_id"], fs)

        assert result.scoring is not None
        assert result.scoring.ok is False
        assert db.session.get(Evidence, result.photo_id) is not None
        rows = EvidenceAssessment.query.filter_by(evidence_id=result.photo_id).all()
        assert len(rows) == 1 and rows[0].error
        db.session.remove()


def test_upload_skips_scoring_when_disabled(db_app, monkeypatch):
    from app.extensions import db
    from app.inspection.photo_service import InspectionPhotoService

    monkeypatch.setattr(
        "app.inspection.photo_service.upload_photo",
        lambda file_obj, adj_id, name: f"https://res.cloudinary.com/demo/{adj_id}/{name}",
    )
    client_stub = _StubClient()
    _patch_httpx(monkeypatch, client_stub)

    app, ids = db_app
    with app.app_context():
        from io import BytesIO

        from werkzeug.datastructures import FileStorage

        fs = FileStorage(stream=BytesIO(b"\xff\xd8\xff\xe0JFIF"), filename="shot.jpg")
        result = InspectionPhotoService().upload_adjudication_photo(
            ids["adjudication_id"], fs, score=False,
        )
        assert result.scoring is None
        assert client_stub.posts == []
        db.session.remove()


# -- re-score a stored photo (read back from Cloudinary) ------------------- #


def test_assess_route_rescores_a_stored_photo(db_app, client, monkeypatch):
    answers_map = _answers(gate=0.97, clean_premise=0.88)
    _patch_httpx(monkeypatch, _StubClient(answers=answers_map))

    _app, ids = db_app
    r = client.post(f"/inspection/photos/{ids['photo_id']}/assess")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["gate_passed"] is True
    assert body["suggested_fields"] == ["clean_premise"]
    assert len(body["items"]) == 10
    assert body["assessment"]["resolved_model"] == "inception/mercury-decide-20260930"


def test_assess_route_suggests_a_polarity_correct_form_value(db_app, client, monkeypatch):
    """The inverted pair must surface as "yes", the checklist pair as "no"."""
    answers_map = _answers(gate=0.97, clean_premise=0.95, Expired_item=0.95)
    _patch_httpx(monkeypatch, _StubClient(answers=answers_map))

    _app, ids = db_app
    body = client.post(f"/inspection/photos/{ids['photo_id']}/assess").get_json()
    suggested = {i["field"]: i["suggested_form_value"] for i in body["items"] if i["suggested_form_value"]}
    assert suggested == {"clean_premise": "no", "Expired_item": "yes"}


def test_assess_route_is_200_but_suggests_nothing_when_gated_out(db_app, client, monkeypatch):
    """A document photo is a successful run that scores nothing — not a 502."""
    answers_map = _answers(gate=0.004, clean_premise=0.95)
    _patch_httpx(monkeypatch, _StubClient(answers=answers_map))

    _app, ids = db_app
    r = client.post(f"/inspection/photos/{ids['photo_id']}/assess")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["gate_passed"] is False
    assert body["suggested_fields"] == []
    assert all(i["p_violation"] is None for i in body["items"])


def test_assess_route_reports_a_provider_failure_as_502(db_app, client, monkeypatch):
    _patch_httpx(monkeypatch, _StubClient(raises=RuntimeError("provider down")))

    _app, ids = db_app
    r = client.post(f"/inspection/photos/{ids['photo_id']}/assess")
    assert r.status_code == 502
    assert r.get_json()["suggested_fields"] == []


def test_assess_route_is_404_for_unknown_evidence(client):
    assert client.post("/inspection/photos/nope/assess").status_code == 404


def test_assess_route_is_400_for_non_photo_evidence(db_app, client):
    assert client.post(f"/inspection/photos/{_add_non_photo(db_app[0])}/assess").status_code == 400


def test_assess_route_is_503_when_disabled(monkeypatch):
    from app import create_app
    from app.extensions import db as _db
    from app.models import FSO, User

    _enable_env(monkeypatch, enabled="false")
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        _db.create_all()
        _db.session.add(User(username="offuser", password_hash="pbkdf2:sha256$test$dummy"))
        _db.session.add(FSO(fso_name="Off Officer"))
        _db.session.commit()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        assert c.post("/inspection/photos/whatever/assess").status_code == 503
    with app.app_context():
        _db.session.remove()
        _db.drop_all()


def test_assessments_are_append_only(db_app, client, monkeypatch):
    _patch_httpx(monkeypatch, _StubClient())

    _app, ids = db_app
    client.post(f"/inspection/photos/{ids['photo_id']}/assess")
    client.post(f"/inspection/photos/{ids['photo_id']}/assess")

    rows = client.get(f"/inspection/photos/{ids['photo_id']}/assessments").get_json()
    assert len(rows) == 2, "re-running must append, not overwrite"
    assert rows[0]["id"] > rows[1]["id"]


def test_bulk_route_scores_every_photo_of_an_adjudication(db_app, client, monkeypatch):
    from app.extensions import db
    from app.models import Evidence

    app, ids = db_app
    with app.app_context():
        for name in ("b.jpg", "c.jpg"):
            db.session.add(Evidence(
                adjudication_id=ids["adjudication_id"], evidence_type="photo",
                filepath=f"https://res.cloudinary.com/demo/{name}", filename=name,
            ))
        db.session.commit()
        db.session.remove()

    _patch_httpx(monkeypatch, _StubClient())
    r = client.post(f"/inspection/{ids['adjudication_id']}/photos/assess")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 3
    assert all(i["ok"] for i in body["items"])
