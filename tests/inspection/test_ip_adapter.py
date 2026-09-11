"""Unit tests for IP adapter."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from unittest.mock import MagicMock, patch

import pytest

from app.inspection.verification.ip_adapter import IpGeolocationAdapter, region_match


class TestIpGeolocationAdapter:
    """Tests for IpGeolocationAdapter."""

    @pytest.fixture
    def adapter(self):
        return IpGeolocationAdapter()

    @patch("app.inspection.verification.ip_adapter.requests.get")
    def test_geolocate_private_ip(self, mock_get, adapter):
        """Private IPs return private_ip error without making HTTP calls."""
        result = adapter.geolocate("127.0.0.1")
        assert result["error"] == "private_ip"
        assert result["region"] is None
        assert result["city"] is None
        mock_get.assert_not_called()

    @patch("app.inspection.verification.ip_adapter.requests.get")
    def test_geolocate_success(self, mock_get, adapter):
        """Geolocate returns region and city on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "success", "region": "CA", "city": "San Jose"}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = adapter.geolocate("8.8.8.8")
        assert result["region"] == "CA"
        assert result["city"] == "San Jose"
        assert result["error"] is None

    @patch("app.inspection.verification.ip_adapter.requests.get")
    def test_geolocate_failure(self, mock_get, adapter):
        """Geolocate handles failure gracefully."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "fail", "message": "not found"}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = adapter.geolocate("1.2.3.4")
        assert result["error"] is not None


class TestRegionMatch:
    """Tests for region_match function."""

    def test_region_match_returns_true(self):
        """Returns True when IP city/region matches geocoded locality."""
        assert region_match("san jose", "CA", "San Jose, California") is True
        assert (
            region_match("california", "", "San Francisco, California") is True
        )  # ponytail: use empty string instead of None

    def test_region_match_returns_false(self):
        """Returns False when no overlap."""
        assert region_match("tokyo", "Kanto", "San Jose, California") is False

    def test_region_match_case_insensitive(self):
        """Matching is case-insensitive."""
        assert region_match("SAN JOSE", "KANTO", "san jose, california") is True
