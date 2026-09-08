"""Step 3 tests - lookup and interface format verification."""

import pytest

from app.utils.lookup import LookupResult, lookup_fssai


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
