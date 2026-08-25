"""Tests for the Postgres-backed lookup_fssai() (Steps 3+6).

Covers prefix dispatch ('1' -> FssaiLicense, '2' -> FssaiRegistration —
mechanical mapping, semantically inverted; see app/models/lookup.py),
the byte-exact return contract, and DD-MM-YYYY expiry pass-through.

Reference: docs/FSSAI_LOOKUP_POSTGRES_RESEARCH.md §2 rows 7-8, §3.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from flask import Flask

from app.extensions import db
from app.models import FssaiLicense, FssaiRegistration
from app.utils.lookup import lookup_fssai


@pytest.fixture
def app():
    """Create and configure a test Flask app."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seeded(app):
    """Seed one license record and one registration record."""
    lic = FssaiLicense(
        license_no="11522000000482",
        company_name="Test Confectionery",
        full_address="12 Park Street, Kolkata",
        expiry_date="31-03-2027",
    )
    reg = FssaiRegistration(
        registration_no="25722000000482",
        company_name="Roadside Snacks",
        full_address="45 MG Road, Kolkata",
        expiry_date="18-03-2026",
    )
    db.session.add_all([lic, reg])
    db.session.commit()
    return lic, reg


class TestLookupFssaiPostgres:
    def test_prefix_1_hits_license_table(self, app, seeded):
        result, error = lookup_fssai("11522000000482")
        assert error is None
        assert result == {
            "companyName": "Test Confectionery",
            "fullAddress": "12 Park Street, Kolkata",
            "expiryDate": "31-03-2027",
            "source": "license_data",
        }

    def test_prefix_2_hits_registration_table(self, app, seeded):
        result, error = lookup_fssai("25722000000482")
        assert error is None
        assert result == {
            "companyName": "Roadside Snacks",
            "fullAddress": "45 MG Road, Kolkata",
            # DD-MM-YYYY string passes through verbatim — never parsed
            "expiryDate": "18-03-2026",
            "source": "registration_data",
        }

    def test_not_found(self, app, seeded):
        result, error = lookup_fssai("19999999999999")
        assert result is None
        assert error == "License/Registration number not found."

    def test_invalid_prefix(self, app):
        result, error = lookup_fssai("31522000000482")
        assert result is None
        assert error == ("Unrecognized License/Registration number prefix (expected to start with 1 or 2).")

    def test_empty_input(self, app):
        result, error = lookup_fssai("")
        assert result is None
        assert error == "License/Registration number is required."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
