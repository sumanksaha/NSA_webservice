"""Step 3 tests - lookup and interface format verification."""

import pytest

from app.utils.lookup import LookupResult, lookup_fssai


@pytest.fixture
def app():
    """Minimal app context with reference tables created."""
    from app import create_app
    from app.extensions import db

    application = create_app()
    ctx = application.app_context()
    ctx.push()
    db.create_all()
    yield application
    db.session.remove()
    ctx.pop()


@pytest.fixture
def seeded(app):
    """Seed one known license row (fresh-SELECT verified)."""
    from sqlalchemy import text

    from app.extensions import db
    from app.models.lookup import FssaiLicense

    license_no = "11522000000482"
    if db.session.get(FssaiLicense, license_no) is None:
        db.session.add(
            FssaiLicense(
                license_no=license_no,
                company_name="Step3 Test Foods Pvt Ltd",
                full_address="3 Step Lane, Kolkata",
                expiry_date="31-12-2027",
            )
        )
        db.session.commit()
    db.session.expunge_all()
    count = db.session.execute(
        text("SELECT COUNT(*) FROM fssai_licenses WHERE license_no = :k"),
        {"k": license_no},
    ).scalar()
    assert count == 1, f"seeded license not visible after commit (count={count})"


class TestFssaiLookupFormat:
    """Test that FSSAI lookup returns expected format (LookupResult)."""

    def test_fssai_lookup_format(self, app):
        """Test that FSSAI lookup returns expected format (LookupResult)."""
        # This test verifies the format of the lookup_fssai function
        # Note: This depends on the actual database files being present
        # We are just testing the function signature and return format

        # Test with empty input - returns LookupResult
        result = lookup_fssai("")
        assert isinstance(result, LookupResult)
        assert result.found is False
        assert result.error is not None

        # Test with invalid prefix - returns LookupResult
        result = lookup_fssai("3123456789")
        assert isinstance(result, LookupResult)
        assert result.found is False
        assert result.error is not None


class TestFssaiLookupSuccess:
    """Test that FSSAI lookup returns correct data on success."""

    def test_fssai_lookup_success_data_fields(self, app, seeded):
        """Test that successful lookup has expected data fields."""
        result = lookup_fssai("11522000000482")
        assert isinstance(result, LookupResult)
        assert result.found is True
        assert result.error is None
        assert "companyName" in result.data
        assert "fullAddress" in result.data
        assert "expiryDate" in result.data
        assert "source" in result.data
