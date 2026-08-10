"""Tests for the Phase 11 AI Assistant module (app/ai_assistant/).

Covers:
- Service construction (enabled/disabled based on config)
- Each action method with mocked httpx responses
- Token tracking accumulation
- Route: 200 (success), 400 (bad payload), 302 (unauthenticated), 503 (not configured)
- Blueprint registration (route exists in app url_map)

Follows the test pattern from tests/test_food_cell_do_intimation.py and
tests/test_validation.py: _setup_test_env() creates app with in-memory
SQLite + db.create_all(), seeds User/FSO, authenticates via session_transaction().
"""

from __future__ import annotations

import json
from unittest import mock

import pytest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO.

    Returns (app, client, app_context). The client is pre-authenticated
    via session_transaction() (same pattern as test_food_cell_do_intimation.py).
    """
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="aitestuser", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, app_context


def _setup_unauthenticated_client():
    """Create a test app/client without authentication (for 302 redirect tests)."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="aitestuser", password_hash="pbkdf2:sha256$test$dummy")  # noqa: S106
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()  # No session_transaction — unauthenticated
    return app, client, app_context


def _mock_httpx_response(content, tokens=42, status_code=200):
    """Create a mock httpx.Response compatible object."""
    import httpx

    mock_resp = mock.MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": tokens},
    }
    mock_resp.raise_for_status.return_value = None
    return mock_resp


@pytest.fixture(autouse=True)
def _cleanup():
    """Ensure fresh state per test."""
    yield
    from app.extensions import db

    db.session.remove()


# --------------------------------------------------------------------------- #
# Service construction tests
# --------------------------------------------------------------------------- #


class TestServiceConstruction:
    """AIAssistantService should correctly report enabled/disabled state."""

    def test_disabled_when_no_api_key(self):
        """Service is disabled when AI_ASSISTANT_API_KEY is empty."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = ""
        from app.ai_assistant.service import AIAssistantService

        with app.app_context():
            svc = AIAssistantService()
            assert svc.is_enabled() is False

    def test_disabled_when_no_provider(self):
        """Service is disabled when AI_ASSISTANT_PROVIDER is empty."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = ""
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"
        from app.ai_assistant.service import AIAssistantService

        with app.app_context():
            svc = AIAssistantService()
            assert svc.is_enabled() is False

    def test_enabled_when_configured(self):
        """Service is enabled when both provider and key are set."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"
        from app.ai_assistant.service import AIAssistantService

        with app.app_context():
            svc = AIAssistantService()
            assert svc.is_enabled() is True
            assert svc.tokens_used == 0
            assert svc.provider == "openrouter"

    def test_provider_override(self):
        """Constructor accepts a provider override."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = ""
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"
        from app.ai_assistant.service import AIAssistantService

        with app.app_context():
            svc = AIAssistantService(provider="openai")
            assert svc.is_enabled() is True
            assert svc.provider == "openai"


# --------------------------------------------------------------------------- #
# Service action tests (mocked httpx)
# --------------------------------------------------------------------------- #


@pytest.fixture
def svc_enabled():
    """An AIAssistantService with config set for OpenRouter."""
    app, client, ctx = _setup_test_env()
    app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
    app.config["AI_ASSISTANT_API_KEY"] = "fake-key"
    app.config["AI_ASSISTANT_MODEL"] = "test-model"
    from app.ai_assistant.service import AIAssistantService

    with app.app_context():
        yield AIAssistantService()


class TestServiceActions:
    """Each action method should call the LLM and return the parsed result."""

    def test_summarize_text(self, svc_enabled):
        """summarize_text returns the LLM response content as a string."""
        svc = svc_enabled
        mock_resp = _mock_httpx_response("This is a summary.", tokens=50)

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.summarize_text("Some legal text here.")
            assert result == "This is a summary."
            assert svc.tokens_used == 50

    def test_refine_legal_language(self, svc_enabled):
        """refine_legal_language returns refined text."""
        svc = svc_enabled
        mock_resp = _mock_httpx_response("Refined legal text.", tokens=75)

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.refine_legal_language("Original legal text.")
            assert result == "Refined legal text."
            assert svc.tokens_used == 75

    def test_detect_contradictions_returns_list(self, svc_enabled):
        """detect_contradictions parses JSON array from LLM response."""
        svc = svc_enabled
        mock_resp = _mock_httpx_response(
            json.dumps(["Contradiction 1", "Contradiction 2"]),
            tokens=30,
        )

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.detect_contradictions("Some text with contradictions.")
            assert isinstance(result, list)
            assert len(result) == 2
            assert result[0] == "Contradiction 1"
            assert svc.tokens_used == 30

    def test_detect_contradictions_fallback_on_invalid_json(self, svc_enabled):
        """detect_contradictions falls back to raw text when JSON is invalid."""
        svc = svc_enabled
        mock_resp = _mock_httpx_response("Not JSON at all", tokens=20)

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.detect_contradictions("Some text.")
            assert isinstance(result, list)
            assert result == ["Not JSON at all"]

    def test_detect_contradictions_empty_result(self, svc_enabled):
        """detect_contradictions returns empty list for empty LLM response."""
        svc = svc_enabled
        mock_resp = _mock_httpx_response("   ", tokens=10)

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.detect_contradictions("Some text.")
            assert result == []

    def test_suggest_missing_annexures(self, svc_enabled):
        """suggest_missing_annexures returns a parsed list."""
        svc = svc_enabled
        mock_resp = _mock_httpx_response(
            json.dumps(["lab report", "site layout plan"]),
            tokens=40,
        )

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.suggest_missing_annexures("Some document text.")
            assert result == ["lab report", "site layout plan"]
            assert svc.tokens_used == 40

    def test_draft_prayers(self, svc_enabled):
        """draft_prayers returns the drafted prayer text."""
        svc = svc_enabled
        prayers = "1. That this matter be tried summarily.\n2. That penalties be imposed."
        mock_resp = _mock_httpx_response(prayers, tokens=200)

        with mock.patch("httpx.Client.post", return_value=mock_resp):
            result = svc.draft_prayers("Sample facts.", "Sample grounds.")
            assert result == prayers
            assert svc.tokens_used == 200

    def test_token_accumulation_across_calls(self, svc_enabled):
        """tokens_used accumulates across multiple calls."""
        svc = svc_enabled
        with mock.patch(
            "httpx.Client.post",
            side_effect=[
                _mock_httpx_response("Summary 1", tokens=50),
                _mock_httpx_response("Refined", tokens=75),
            ],
        ):
            svc.summarize_text("Text 1.")
            svc.refine_legal_language("Text 2.")
            assert svc.tokens_used == 125


# --------------------------------------------------------------------------- #
# Service error handling tests
# --------------------------------------------------------------------------- #


class TestServiceErrors:
    """Service should raise meaningful errors on misconfigured or failed requests."""

    def test_request_when_disabled_raises(self):
        """Calling _request when disabled raises RuntimeError."""
        app, client, ctx = _setup_test_env()
        # Force unconfigured state — env may define AI creds on the dev box.
        app.config["AI_ASSISTANT_PROVIDER"] = ""
        app.config["AI_ASSISTANT_API_KEY"] = ""
        from app.ai_assistant.service import AIAssistantService

        with app.app_context():
            svc = AIAssistantService()
            with pytest.raises(RuntimeError, match="not configured"):
                svc._request("test prompt", 100)

    def test_retry_on_429_then_succeed(self):
        """Service retries on 429 and eventually succeeds."""
        import httpx

        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"
        from app.ai_assistant.service import AIAssistantService

        with app.app_context():
            svc = AIAssistantService()
            err_resp = mock.MagicMock()
            err_resp.status_code = 429
            err_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "rate limited", request=mock.MagicMock(), response=err_resp
            )
            ok_resp = _mock_httpx_response("Success after retry", tokens=60)

            with mock.patch("httpx.Client.post", side_effect=[err_resp, ok_resp]):
                with mock.patch("time.sleep"):
                    result, tokens = svc._request("test", 100)
                assert result == "Success after retry"
                assert tokens == 60


# --------------------------------------------------------------------------- #
# Route tests
# --------------------------------------------------------------------------- #


class TestAssistRoute:
    """POST /ai-assistant/assist endpoint behavior."""

    def test_route_registered(self):
        """The /ai-assistant/assist route should be in the app's URL map."""
        app, client, ctx = _setup_test_env()
        rules = [str(r) for r in app.url_map.iter_rules()]
        ai_rules = [r for r in rules if "ai-assistant" in r]
        assert len(ai_rules) > 0, "No /ai-assistant routes found in URL map"

    def test_not_configured_returns_503(self):
        """When AI is not configured, the route returns 503."""
        app, client, ctx = _setup_test_env()
        # Force unconfigured state — env may define AI creds on the dev box.
        app.config["AI_ASSISTANT_PROVIDER"] = ""
        app.config["AI_ASSISTANT_API_KEY"] = ""
        resp = client.post(
            "/ai-assistant/assist",
            json={"action": "summarize", "content": "test text"},
        )
        assert resp.status_code == 503
        data = resp.get_json()
        assert "error" in data

    def test_invalid_action_returns_400(self):
        """An invalid action name returns 400."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"

        resp = client.post(
            "/ai-assistant/assist",
            json={"action": "invalid_action", "content": "test text"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_missing_content_returns_400(self):
        """Missing content returns 400."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"

        resp = client.post(
            "/ai-assistant/assist",
            json={"action": "summarize", "content": ""},
        )
        assert resp.status_code == 400

    def test_non_dict_payload_returns_400(self):
        """A non-dict JSON body returns 400."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"

        resp = client.post(
            "/ai-assistant/assist",
            json="not a dict",
        )
        assert resp.status_code == 400

    def test_unauthenticated_redirects(self):
        """Unauthenticated requests are redirected to login (302)."""
        app, unauth_client, ctx = _setup_unauthenticated_client()
        resp = unauth_client.post(
            "/ai-assistant/assist",
            json={"action": "summarize", "content": "test"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)

    def test_successful_request_returns_200(self):
        """A valid request with mocked LLM returns 200 + result."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"

        mock_resp = _mock_httpx_response("AI summary result", tokens=55)
        with mock.patch("httpx.Client.post", return_value=mock_resp):
            resp = client.post(
                "/ai-assistant/assist",
                json={"action": "summarize", "content": "Some legal text."},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["result"] == "AI summary result"
        assert data["tokens_used"] == 55
        assert data["action"] == "summarize"

    def test_list_result_is_json_stringified_for_transport(self):
        """detect_contradictions result is JSON-stringified in the response."""
        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"

        mock_resp = _mock_httpx_response(
            json.dumps(["Contradiction A", "Contradiction B"]),
            tokens=33,
        )
        with mock.patch("httpx.Client.post", return_value=mock_resp):
            resp = client.post(
                "/ai-assistant/assist",
                json={"action": "detect_contradictions", "content": "Some text."},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        result_list = json.loads(data["result"])
        assert result_list == ["Contradiction A", "Contradiction B"]

    def test_draft_prayers_uses_context(self):
        """draft_prayers uses context.facts and context.grounds in the prompt."""
        import httpx

        app, client, ctx = _setup_test_env()
        app.config["AI_ASSISTANT_PROVIDER"] = "openrouter"
        app.config["AI_ASSISTANT_API_KEY"] = "fake-key"

        mock_resp = _mock_httpx_response("1. Prayer clause.", tokens=100)
        with mock.patch("httpx.Client.post", return_value=mock_resp) as mock_post:
            resp = client.post(
                "/ai-assistant/assist",
                json={
                    "action": "draft_prayers",
                    "content": "ignored for this action",
                    "context": {"facts": "Fact 1.", "grounds": "Ground 1."},
                },
            )
        assert resp.status_code == 200
        call_kwargs = mock_post.call_args.kwargs
        sent_body = call_kwargs["json"]
        user_message = sent_body["messages"][1]["content"]
        assert "Fact 1." in user_message
        assert "Ground 1." in user_message
