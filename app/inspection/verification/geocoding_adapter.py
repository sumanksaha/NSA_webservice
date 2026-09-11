"""Rate-limited Nominatim geocoding adapter with retry/caching."""

import time

import requests


class NominatimGeocoder:
    """Rate-limited geocoding adapter for reverse geocoding.

    Single interface: reverse(lat, lng) -> dict (with locality, error).
    """

    _last_request_time: float = 0.0

    def reverse(self, lat: float, lng: float) -> dict:
        """Reverse geocode to get locality from lat/lng."""
        self._enforce_rate_limit()
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lng, "format": "json"}
        headers = {"User-Agent": "NSA_webservice/1.0"}
        try:
            response = requests.get(url, params=params, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            locality = (
                data.get("address", {}).get("suburb")
                or data.get("address", {}).get("city_district")
                or data.get("address", {}).get("city")
                or data.get("display_name")
            )
            return {"locality": locality, "error": None}
        except requests.exceptions.Timeout:
            return {"locality": None, "error": "timeout"}
        except Exception as e:
            return {"locality": None, "error": str(e)}

    def _enforce_rate_limit(self) -> None:
        current = time.time()
        since = current - self._last_request_time
        if since < 1.0:
            time.sleep(1.0 - since)
        self._last_request_time = time.time()
