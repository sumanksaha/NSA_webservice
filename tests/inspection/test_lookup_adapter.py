"""Unit tests for lookup adapter."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from unittest.mock import patch

import pytest

from app.inspection.verification.lookup_adapter import LicenseLookupAdapter
from app.utils.lookup import LookupResult


class TestLicenseLookupAdapter:
    """Tests for LicenseLookupAdapter."""

    @pytest.fixture
    def adapter(self):
        return LicenseLookupAdapter()

    @patch("app.inspection.verification.lookup_adapter.lookup_fssai")
    @patch("app.inspection.verification.lookup_adapter.lookup_ce")
    def test_lookup_fssai(self, mock_ce, mock_fssai, adapter):
        """FSSAI lookup routes to lookup_fssai."""
        mock_fssai.return_value = LookupResult(found=True, data={"companyName": "Test Co"})
        result = adapter.lookup("12345", source="fssai")
        mock_fssai.assert_called_once_with("12345")
        mock_ce.assert_not_called()
        assert result.found is True

    @patch("app.inspection.verification.lookup_adapter.lookup_fssai")
    @patch("app.inspection.verification.lookup_adapter.lookup_ce")
    def test_lookup_ce(self, mock_ce, mock_fssai, adapter):
        """CE lookup routes to lookup_ce."""
        mock_ce.return_value = LookupResult(found=False, error="not found")
        result = adapter.lookup("CE-123", source="ce")
        mock_ce.assert_called_once_with("CE-123")
        mock_fssai.assert_not_called()
        assert result.found is False

    @patch("app.inspection.verification.lookup_adapter.lookup_fssai")
    @patch("app.inspection.verification.lookup_adapter.lookup_ce")
    def test_lookup_unknown_source(self, mock_ce, mock_fssai, adapter):
        """Unknown source returns not found."""
        result = adapter.lookup("123", source="unknown")
        assert result.found is False
        assert "Unknown source" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
