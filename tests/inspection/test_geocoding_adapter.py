"""Unit tests for geocoding adapter."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from unittest.mock import MagicMock, patch

import pytest

from app.inspection.verification.geocoding_adapter import NominatimGeocoder


class TestNominatimGeocoder:
    """Tests for NominatimGeocoder adapter."""

    @pytest.fixture
    def geocoder(self):
        return NominatimGeocoder()

    @patch("app.inspection.verification.geocoding_adapter.requests.get")
    def test_reverse_success(self, mock_get, geocoder):
        """Reverse geocode returns locality on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "address": {"suburb": "Testville", "city": None},
            "display_name": "Testville, Testland",
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = geocoder.reverse(52.5200, 13.4050)
        assert result["locality"] == "Testville"
        assert result["error"] is None

    @patch("app.inspection.verification.geocoding_adapter.requests.get")
    def test_reverse_timeout(self, mock_get, geocoder):
        """Reverse geocode handles timeout gracefully."""
        import requests

        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        result = geocoder.reverse(52.5200, 13.4050)
        assert result["locality"] is None
        assert result["error"] == "timeout"

    @patch("app.inspection.verification.geocoding_adapter.requests.get")
    def test_reverse_generic_error(self, mock_get, geocoder):
        """Reverse geocode handles generic errors."""
        mock_get.side_effect = Exception("Connection refused")

        result = geocoder.reverse(52.5200, 13.4050)
        assert result["locality"] is None
        assert "Connection refused" in result["error"]

    @patch("app.inspection.verification.geocoding_adapter.time.sleep")
    def test_rate_limit_enforced(self, mock_sleep, geocoder):
        """Rate limiting is enforced between calls."""
        geocoder._last_request_time = 0

        # First call
        with patch("app.inspection.verification.geocoding_adapter.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"display_name": "Test"}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            geocoder.reverse(52.5200, 13.4050)

        # Second call immediately - should trigger rate limit sleep
        with patch("app.inspection.verification.geocoding_adapter.requests.get") as mock_get2:
            mock_response2 = MagicMock()
            mock_response2.json.return_value = {"display_name": "Test2"}
            mock_response2.raise_for_status.return_value = None
            mock_get2.return_value = mock_response2
            geocoder.reverse(52.5200, 13.4050)

        # Sleep should have been called due to rate limiting
        assert mock_sleep.called
