"""Verification service using adapters for external services."""

from typing import Any

from .verification.geocoding_adapter import NominatimGeocoder
from .verification.ip_adapter import IpGeolocationAdapter, region_match
from .verification.lookup_adapter import LicenseLookupAdapter

_geocoder = NominatimGeocoder()
_ip_adapter = IpGeolocationAdapter()
_license_adapter = LicenseLookupAdapter()


def _guarded(label: str, call, fallback: dict[str, Any]) -> dict[str, Any]:
    """Run an external-service *call*, degrading to *fallback* on any error."""
    try:
        return call()
    except Exception as exc:
        try:
            from flask import current_app

            if current_app:
                current_app.logger.warning(f"{label} failed: {exc}")
        except Exception:
            pass
        return fallback


def verify_photo_location(
    raw_lat: float,
    raw_lng: float,
    accuracy: float | None,
    ip_address: str,
    fbo: Any,
) -> dict[str, Any]:
    """Runs all verification checks and returns a combined result.

    Uses adapters for external services (Nominatim, IP geolocation).
    Degrades gracefully if any external call times out or raises.
    """
    result: dict[str, Any] = {
        "locality": None,
        "ip_match": False,
        "distance_to_fbo_m": None,
        "verification_status": "PASS",
        "flag_reasons": [],
    }

    # 1. Reverse geocode to get locality
    geocode_result = _guarded(
        "geocoding",
        lambda: _geocoder.reverse(raw_lat, raw_lng),
        {"locality": None},
    )

    if geocode_result.get("error") is None:
        result["locality"] = geocode_result.get("locality")

    # 2. Geolocate IP address
    ip_result = _guarded(
        "ip_geolocate",
        lambda: _ip_adapter.geolocate(ip_address),
        {"city": None, "region": None},
    )

    ip_city = ip_result.get("city")
    ip_region = ip_result.get("region")

    # 3. Check IP match
    if result["locality"] is not None and ip_city is not None and ip_region is not None:
        result["ip_match"] = region_match(str(ip_city), str(ip_region), str(result["locality"]))

    # 4. License lookup (if applicable)
    if hasattr(fbo, "license_number") and fbo.license_number:
        lookup_res = _license_adapter.lookup(fbo.license_number, source="fssai")
        result["license_valid"] = (
            getattr(lookup_res, "found", False)
            or (getattr(lookup_res, "__getitem__", None) and lookup_res.get("found"))
            or False
        )

    # 5. Set verification status
    if accuracy is not None and accuracy > 100:
        result["flag_reasons"].append("accuracy_exceeds_100m")

    if not result["ip_match"]:
        result["flag_reasons"].append("ip_region_mismatch")

    if result["distance_to_fbo_m"] is None:
        result["flag_reasons"].append("fbo_location_unavailable")

    if result["flag_reasons"]:
        result["verification_status"] = "FLAG"

    return result


__all__ = ["verify_photo_location"]
