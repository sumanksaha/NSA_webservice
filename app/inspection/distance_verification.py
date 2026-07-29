import math
import time

import requests

# Module-level variable for rate limiting
_last_request_time: float = 0.0


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Returns distance in meters between two lat/lng points."""
    # Convert latitude and longitude from degrees to radians
    lat1_rad = math.radians(lat1)
    lng1_rad = math.radians(lng1)
    lat2_rad = math.radians(lat2)
    lng2_rad = math.radians(lng2)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlng = lng2_rad - lng1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # Earth's radius in meters
    radius = 6371000
    distance = radius * c

    return distance


def geocode_fbo_address(address: str) -> dict:
    """Calls Nominatim forward-geocoding for a given address string.
    Returns {"lat": float or None, "lng": float or None, "error": str or None}
    """
    global _last_request_time

    # Rate limiting: no more than 1 request per second
    current_time = time.time()
    time_since_last = current_time - _last_request_time
    if time_since_last < 1.0:
        time.sleep(1.0 - time_since_last)

    # Update the last request time
    _last_request_time = time.time()

    # Prepare the request
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": "NSA_webservice/1.0"}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data:
            lat = float(data[0].get("lat"))
            lng = float(data[0].get("lon"))
            return {"lat": lat, "lng": lng, "error": None}
        return {"lat": None, "lng": None, "error": "No results found"}
    except requests.exceptions.Timeout:
        return {"lat": None, "lng": None, "error": "Request timed out"}
    except requests.exceptions.RequestException as e:
        return {"lat": None, "lng": None, "error": f"Request failed: {e!s}"}
    except Exception as e:
        return {"lat": None, "lng": None, "error": f"Unexpected error: {e!s}"}


def get_or_geocode_fbo_location(fbo) -> tuple:
    """Takes an FBO object (has .reg_lat, .reg_lng, .geocoded_at, .address).
    If reg_lat/reg_lng already set, return (reg_lat, reg_lng) immediately.
    If not set: call geocode_fbo_address(fbo.address), then return the result.
    Return (None, None) if geocoding fails.
    """
    if fbo.reg_lat is not None and fbo.reg_lng is not None:
        return (fbo.reg_lat, fbo.reg_lng)

    # Geocode the address
    result = geocode_fbo_address(fbo.address)
    if result["error"] is None:
        return (result["lat"], result["lng"])
    return (None, None)
