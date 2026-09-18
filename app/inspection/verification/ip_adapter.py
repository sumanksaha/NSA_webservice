"""IP geolocation adapter with caching."""

import re

import requests


class IpGeolocationAdapter:
    """IP-based location detection with fallback.

    Single interface: geolocate(ip) -> dict (with region, city, error).
    """

    def geolocate(self, ip_address: str) -> dict:
        """Geolocate an IP address using ip-api.com."""
        private_pattern = re.compile(r"^(127\.0\.0\.1|192\.168\.|10\.)")
        if private_pattern.match(ip_address):
            return {"region": None, "city": None, "error": "private_ip"}

        url = f"http://ip-api.com/json/{ip_address}"
        params = {"fields": "status,region,city,message"}
        try:
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return {"region": data.get("region"), "city": data.get("city"), "error": None}
            return {"region": None, "city": None, "error": data.get("message", "Unknown error")}
        except requests.exceptions.Timeout:
            return {"region": None, "city": None, "error": "timeout"}
        except Exception as e:
            return {"region": None, "city": None, "error": str(e)}


def region_match(ip_city: str, ip_region: str, geocoded_locality: str) -> bool:
    """Compare IP city/region against geocoded locality using substring matching."""
    geo_lower = geocoded_locality.lower()
    return ip_city.lower() in geo_lower or ip_region.lower() in geo_lower
