from .geo_verification import reverse_geocode
from .ip_verification import ip_geolocate, region_match
from .distance_verification import haversine_distance, get_or_geocode_fbo_location


def verify_photo_location(raw_lat, raw_lng, accuracy, ip_address, fbo) -> dict:
    """
    Runs all verification checks and returns a combined result.
    """
    # Initialize result
    result = {
        "locality": None,
        "ip_match": False,
        "distance_to_fbo_m": None,
        "verification_status": "PASS",
        "flag_reasons": []
    }

    # 1. Reverse geocode to get locality
    geocode_result = reverse_geocode(raw_lat, raw_lng)
    if geocode_result["error"] is None:
        result["locality"] = geocode_result["locality"]

    # 2. Geolocate IP address
    ip_result = ip_geolocate(ip_address)
    ip_city = ip_result.get("city")
    ip_region = ip_result.get("region")

    # 3. Check IP match
    if result["locality"] is not None and ip_city is not None and ip_region is not None:
        result["ip_match"] = region_match(ip_city, ip_region, result["locality"])

    # 4. Get FBO location
    fbo_lat, fbo_lng = get_or_geocode_fbo_location(fbo)

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