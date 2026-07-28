from typing import Any

from .distance_verification import get_or_geocode_fbo_location, haversine_distance
from .geo_verification import reverse_geocode
from .ip_verification import ip_geolocate, region_match


def verify_photo_location(
    raw_lat: float, raw_lng: float, accuracy: float | None, ip_address: str, fbo: Any
) -> dict[str, Any]:
    """
    Runs all verification checks and returns a combined result.
    Degrades gracefully if any external call times out or raises.
    """
    # Initialize result with proper type annotation
    result: dict[str, Any] = {
        "locality": None,
        "ip_match": False,
        "distance_to_fbo_m": None,
        "verification_status": "PASS",
        "flag_reasons": [],
    }

    # 1. Reverse geocode to get locality (with per-call timeout)
    try:
        geocode_result = reverse_geocode(raw_lat, raw_lng)
    except Exception as exc:  # type: ignore[broad-except]  # intentional for graceful degradation
        try:
            from flask import current_app

            if current_app:
                current_app.logger.warning(f"reverse_geocode failed: {exc}")
        except Exception:  # type: ignore[broad-except]
            pass
        geocode_result = {"error": str(exc), "locality": None}

    if geocode_result.get("error") is None:
        result["locality"] = geocode_result.get("locality")

    # 2. Geolocate IP address (with per-call timeout)
    try:
        ip_result = ip_geolocate(ip_address)
    except Exception as exc:  # type: ignore[broad-except]  # intentional for graceful degradation
        try:
            from flask import current_app

            if current_app:
                current_app.logger.warning(f"ip_geolocate failed: {exc}")
        except Exception:  # type: ignore[broad-except]
            pass
        ip_result = {"error": str(exc), "city": None, "region": None}

    ip_city = ip_result.get("city")
    ip_region = ip_result.get("region")

    # 3. Check IP match - convert to str for region_match
    if result["locality"] is not None and ip_city is not None and ip_region is not None:
        result["ip_match"] = region_match(str(ip_city), str(ip_region), str(result["locality"]))

    # 4. Get FBO location (with per-call timeout)
    try:
        fbo_lat, fbo_lng = get_or_geocode_fbo_location(fbo)
    except Exception as exc:  # type: ignore[broad-except]  # intentional for graceful degradation
        try:
            from flask import current_app

            if current_app:
                current_app.logger.warning(f"get_or_geocode_fbo_location failed: {exc}")
        except Exception:  # type: ignore[broad-except]
            pass
        fbo_lat, fbo_lng = None, None

    # 5. Calculate distance to FBO if available
    if fbo_lat is not None and fbo_lng is not None:
        result["distance_to_fbo_m"] = haversine_distance(raw_lat, raw_lng, fbo_lat, fbo_lng)

    # 6. Check flag conditions
    if accuracy is not None and accuracy > 100:
        result["flag_reasons"].append("accuracy_exceeds_100m")

    if not result["ip_match"]:
        result["flag_reasons"].append("ip_region_mismatch")

    if result["distance_to_fbo_m"] is not None and result["distance_to_fbo_m"] > 500:
        result["flag_reasons"].append("distance_exceeds_500m")

    if result["distance_to_fbo_m"] is None:
        result["flag_reasons"].append("fbo_location_unavailable")

    # 7. Set verification status
    if result["flag_reasons"]:
        result["verification_status"] = "FLAG"

    return result
