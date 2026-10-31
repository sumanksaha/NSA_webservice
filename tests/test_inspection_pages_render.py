"""Regression guards for the inspection UI pages.

Background: a lint sweep dropped the ``@inspection_bp.route`` decorator from
``premises_search`` and mangled Jinja in ``history.html`` / ``pending_action.html``
/ ``detail.html``. Every one of those produced a 500 on a page an FSO reaches from
the inspection index, so these tests fail loudly when a page, its template, or a
``url_for`` target breaks again.
"""

from __future__ import annotations

import pathlib
import re

import pytest

TEMPLATE_DIR = pathlib.Path("app/inspection/templates")
REFERENCE_DIR = "html_references"


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


@pytest.fixture(scope="module")
def app():
    from app import create_app
    from app.extensions import db
    from app.models import FSO, Inspection, User
    from datetime import UTC, datetime

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()

        user = User(username="page_render_fso", password_hash="pbkdf2:sha256$test$dummy", is_admin=True)
        db.session.add(user)
        db.session.add(FSO(fso_name="Page Render Officer"))
        db.session.commit()

        # One bare inspection: no checklist_json (exercises checklist=... or {})
        # and a compliance deadline (exercises the overdue calculation).
        db.session.add(
            Inspection(
                fso_name="Page Render Officer",
                inspection_date=datetime(2026, 10, 1, tzinfo=UTC),
                compliance_deadline=datetime(2026, 10, 16, tzinfo=UTC),
            )
        )
        db.session.commit()
        app.config["_PAGE_TEST_USER_ID"] = user.id
        app.config["_PAGE_TEST_INSPECTION_ID"] = Inspection.query.first().id

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="module")
def client(app):
    client = app.test_client()
    _login(client, app.config["_PAGE_TEST_USER_ID"])
    return client


def _inspection_templates() -> list[pathlib.Path]:
    """Rendered templates only — ``html_references`` holds inert snapshots."""
    return sorted(p for p in TEMPLATE_DIR.rglob("*.html") if REFERENCE_DIR not in p.parts)


class TestInspectionTemplatesCompile:
    def test_every_inspection_template_compiles(self, app):
        """A Jinja syntax error 500s the page even when the branch is not taken."""
        bad = []
        with app.app_context():
            env = app.jinja_env
            for path in _inspection_templates():
                name = path.relative_to(TEMPLATE_DIR).as_posix()
                try:
                    env.get_template(name)
                except Exception as exc:  # noqa: BLE001 - report every broken template
                    bad.append(f"{name}: {type(exc).__name__}: {exc}")
        assert not bad, "Templates failed to compile:\n" + "\n".join(bad)


class TestInspectionTemplateEndpoints:
    def test_url_for_targets_are_registered(self, app):
        """Every ``url_for('blueprint.endpoint')`` in a template must exist."""
        registered = {rule.endpoint for rule in app.url_map.iter_rules()}
        pattern = re.compile(r"""url_for\(\s*["']([A-Za-z0-9_.]+)["']""")
        missing: dict[str, list[str]] = {}
        for path in _inspection_templates():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for endpoint in pattern.findall(text):
                if "." in endpoint and endpoint not in registered:
                    missing.setdefault(endpoint, []).append(path.name)
        assert not missing, f"Unregistered url_for targets: {missing}"

    def test_premises_search_route_is_registered(self, app):
        rules = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}
        assert "/inspection/premises/search" in rules, "premises_search route missing (lost decorator?)"
        assert {"GET", "POST"} <= rules["/inspection/premises/search"]


class TestInspectionPagesRender:
    @pytest.mark.parametrize(
        "url",
        [
            "/inspection/",
            "/inspection/list",
            "/inspection/open",
            "/inspection/pending",
            "/inspection/history",
            "/inspection/samples",
            "/inspection/premises/search?q=",
        ],
    )
    def test_page_renders(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"

    def test_detail_renders_without_saved_checklist(self, client, app):
        """checklist_json NULL used to 500 the detail page on checklist.get()."""
        inspection_id = app.config["_PAGE_TEST_INSPECTION_ID"]
        resp = client.get(f"/inspection/{inspection_id}/detail")
        assert resp.status_code == 200, resp.status_code

    def test_edit_page_renders(self, client, app):
        inspection_id = app.config["_PAGE_TEST_INSPECTION_ID"]
        resp = client.get(f"/inspection/{inspection_id}/edit")
        assert resp.status_code == 200, resp.status_code

    def test_premises_search_accepts_post(self, client):
        resp = client.post("/inspection/premises/search", data={"q": ""})
        assert resp.status_code == 200, resp.status_code
