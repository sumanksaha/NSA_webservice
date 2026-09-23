"""Regression tests for non-sample-adjudication form visibility.

Covers the 2026-09 KMC-lookup breakage on the non-sample adjudication
create form (``app/adjudication/templates/adjudication/index.html``):

- ``syncLicenseLookupVisibility()`` set inline ``display: block/none`` on
  ``#license_lookup_block`` but never toggled the ``active`` class that the
  shared ``.conditional-block`` rule requires for ``opacity: 1``. Checking
  "Non-Licensed / Unregistered FBO?" therefore expanded a fully
  transparent block: the CE lookup box looked "missing" while the invisible
  block pushed Case Information down out of view ("disappearing").
- The edit form checkbox had no show/hide wiring at all; its KMC Trade
  License Details section is now gated on the same flag.

All Render-level faults, so these tests assert on the rendered HTML of the
same routes the browser hits. Mirrors ``test_case_file_form_visibility.py``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.extensions import db
from app.models import Adjudication, User


@pytest.fixture()
def client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_RBAC"] = True

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            db.session.add(User(username="testuser", password_hash="pbkdf2:sha256$test$dummy", is_admin=True))
            db.session.commit()
        yield client
        with app.app_context():
            db.drop_all()


def _login(client):
    with client.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True


def _seed_adjudication(client, **overrides) -> int:
    dt = datetime(2026, 1, 15)
    fields = dict(
        case_number="2026/ADJ/800",
        food_safety_officer="FSO Eight",
        fbo_owner="Owner",
        fbo_name="FBO",
        fbo_address="Addr",
        fssai_license="10012043001234",
        First_inspection_date=dt,
        compliance_deadline=datetime(2026, 2, 15),
        inspection_date=datetime(2026, 1, 20),
        authorization_date=datetime(2026, 1, 25),
    )
    fields.update(overrides)
    with client.application.app_context():
        adj = Adjudication(**fields)
        db.session.add(adj)
        db.session.commit()
        return adj.id


class TestLicenseLookupVisibilityWiring:
    def test_create_form_toggles_active_class_not_inline_display(self, client):
        _login(client)
        html = client.get("/adjudication/").get_data(as_text=True)
        assert 'id="license_lookup_block"' in html
        assert 'licenseLookupBlock.classList.toggle("active"' in html
        assert "licenseLookupBlock.style.display" not in html

    def test_create_form_keeps_case_information_unconditional(self, client):
        _login(client)
        html = client.get("/adjudication/").get_data(as_text=True)
        assert "<h3>Case Information</h3>" in html
        assert 'id="ce_fetch_btn"' in html

    def test_create_form_renders_inspection_checklist_items(self, client):
        # The shared index route renders this template without a `checklist`
        # variable; the loop must supply its own item list or the section
        # renders as header-only (checklist "hidden").
        _login(client)
        html = client.get("/adjudication/").get_data(as_text=True)
        assert "<h3>Inspection Checklist</h3>" in html
        assert html.count("checklist-card") >= 12
        for item in ("clean_premise", "refrigerator_clean", "license_display", "Expired_item"):
            assert f'name="{item}"' in html


class TestEditFormKmcSectionWiring:
    def test_edit_form_wires_checkbox_to_kmc_section(self, client):
        _login(client)
        case_id = _seed_adjudication(client)
        html = client.get(f"/adjudication/case/{case_id}/edit").get_data(as_text=True)
        assert 'id="kmc_license_section"' in html
        assert 'input[name="non_license"]' in html
        assert "syncKmcSectionVisibility" in html
        assert "kmcHasData" in html

    def test_edit_form_hides_kmc_section_unless_non_license(self, client):
        _login(client)
        case_id = _seed_adjudication(client)
        html = client.get(f"/adjudication/case/{case_id}/edit").get_data(as_text=True)
        assert 'id="kmc_license_section" class="conditional-block"' in html

    def test_edit_form_shows_kmc_section_when_non_license(self, client):
        _login(client)
        case_id = _seed_adjudication(client, case_number="2026/ADJ/801", non_license="yes")
        html = client.get(f"/adjudication/case/{case_id}/edit").get_data(as_text=True)
        assert 'id="kmc_license_section" class="conditional-block active"' in html

    def test_edit_form_shows_kmc_section_when_ce_data_present(self, client):
        # Stored trade-license values must stay viewable even if the flag is
        # unchecked (e.g. data saved before the toggle wiring existed).
        _login(client)
        case_id = _seed_adjudication(client, case_number="2026/ADJ/802", ce_license_no="KMC-123")
        html = client.get(f"/adjudication/case/{case_id}/edit").get_data(as_text=True)
        assert 'id="kmc_license_section" class="conditional-block active"' in html
        assert 'value="KMC-123"' in html
