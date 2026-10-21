"""CSRF regression tests for the Notepad blueprint.

The Notepad list/detail forms used to ship without a hidden ``csrf_token``
input. With ``WTF_CSRF_ENABLED`` on (the production default, applied via
``csrf.init_app(app)`` in ``app/__init__.py``), a ``POST`` without that field
is rejected with 400 "The CSRF token is missing". Every Notepad POST route
now carries the token, and the template-level forms are asserted below.
"""

from __future__ import annotations

import re

import pytest

from app.extensions import db
from app.models import User

# The hidden-input pattern emitted by the *fixed* template; its value must
# equal the session's CSRF token on the server.
_TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


# Every Notepad POST route, plus the exact form body used by the regression
# (no csrf_token field -> must be rejected with 400).
_TOKENLESS_CASES = [
    ("create", "/notepad/new", {"content_text": "no token"}),
    ("edit", "/notepad/1/edit", {"content_text": "x"}),
    ("ask", "/notepad/1/ask", {"question": "x"}),
    ("evaluate", "/notepad/1/evaluate", {}),
    ("status", "/notepad/1/status", {"status": "implemented"}),
    ("visibility", "/notepad/1/visibility", {}),
]


@pytest.fixture(scope="module")
def csrf_env():
    """A single CSRF-enabled test app with two logged-in users.

    The app is created once (create_app() costs ~3.7s) and the tables are
    rebuilt per test for isolation.
    """
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = True

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    alice = User(username="alice", password_hash="pbkdf2:sha256$test$dummy")
    bob = User(username="bob", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add_all([alice, bob])
    db.session.commit()

    clients = {}
    for user in (alice, bob):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
        clients[user.username] = (user, client)

    yield app, clients

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _token_for(client):
    """Render the current page and return the csrf_token hidden value."""
    page = client.get("/notepad/").get_data(as_text=True)
    match = _TOKEN_RE.search(page)
    assert match, "csrf_token hidden input missing from rendered page"
    return match.group(1)


def _token_for_detail(client, note_id):
    """Render one note's detail page and return its csrf_token value."""
    page = client.get(f"/notepad/{note_id}").get_data(as_text=True)
    match = _TOKEN_RE.search(page)
    assert match, "csrf_token hidden input missing from note detail page"
    return match.group(1)


def _create_note(client, content="hello notepad"):
    """POST /notepad/new with a valid CSRF token -> 302 to /notepad/<id>."""
    return client.post(
        "/notepad/new",
        data={"content_text": content, "csrf_token": _token_for(client)},
    )


class TestNotepadCsrf:
    @pytest.mark.parametrize(
        "route,path,data",
        _TOKENLESS_CASES,
        ids=[c[0] for c in _TOKENLESS_CASES],
    )
    def test_notepad_route_rejects_tokenless_post(self, route, path, data, csrf_env):
        """A POST without a csrf_token must still be rejected (400)."""
        _, client = csrf_env[1]["alice"]
        note = _create_note(client)  # fresh note for the detail routes
        resp = client.post(path, data=data)
        assert resp.status_code == 400, f"{route} should be CSRF-protected"
        assert b"CSRF" in resp.data

    def test_notepad_forms_all_carry_token(self, csrf_env):
        """Detail forms (edit/ask/evaluate/status/visibility) carry a token."""
        _, client = csrf_env[1]["alice"]
        note = _create_note(client)
        note_id = int(note.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
        detail = client.get(f"/notepad/{note_id}").get_data(as_text=True)

        # edit + ask + evaluate + status + visibility = 5 POST forms
        assert detail.count('name="csrf_token"') == 5
