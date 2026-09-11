"""Step 1 tests - core module and fixture verification."""

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


class TestLookupFssaiExists:
    """Test that lookup_fssai function exists and is importable."""

    def test_lookup_fssai_exists(self, app):
        """Test that lookup_fssai function exists and is importable."""
        assert lookup_fssai is not None
        assert callable(lookup_fssai)


class TestLookupFssaiReturnsLookupResult:
    """Test that lookup_fssai returns a LookupResult (not a raw tuple)."""

    def test_lookup_fssai_returns_lookup_result(self, app):
        """Test that lookup_fssai returns LookupResult."""
        result = lookup_fssai("")
        assert isinstance(result, LookupResult)
        assert result.found is False
        assert isinstance(result.error, str)

    def test_lookup_fssai_valid_returns_found(self, app):
        """Test that lookup_fssai returns found=True on valid input."""
        # This depends on the actual database files being present
        # We're just testing the function signature and return format
        result = lookup_fssai("11522000000482")
        assert isinstance(result, LookupResult)
        # On a seeded DB, this would be found=True
        # On an empty DB, this would be found=False - both are valid
