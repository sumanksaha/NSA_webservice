import re

import requests


def ip_geolocate(ip_address: str) -> dict:
    """
    Calls ip-api.com (free tier) to geolocate an IP address.
    Returns: {"region": str or None, "city": str or None, "error": str or None}
    """
    # Handle localhost/private IPs
    private_ip_pattern = re.compile(r'^(127\.0\.0\.1|192\.168\.|10\.)')
    if private_ip_pattern.match(ip_address):
        return {
            'region': None,
            'city': None,
            'error': 'private_ip'
        }

    # Prepare the request
    url = f"http://ip-api.com/json/{ip_address}"
    params = {
        'fields': 'status,region,city,message'
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data.get('status') == 'success':
            return {
                'region': data.get('region'),
                'city': data.get('city'),
                'error': None
            }
        else:
            return {
                'region': None,
                'city': None,
                'error': data.get('message', 'Unknown error')
            }
    except requests.exceptions.Timeout:
        return {
            'region': None,
            'city': None,
            'error': 'Request timed out'
        }
    except requests.exceptions.RequestException as e:
        return {
            'region': None,
            'city': None,
            'error': f'Request failed: {str(e)}'
        }
    except Exception as e:
        return {
            'region': None,
            'city': None,
            'error': f'Unexpected error: {str(e)}'
        }


def region_match(ip_city: str, ip_region: str, geocoded_locality: str) -> bool:
    """
    Compares IP-based city/region against the reverse-geocoded locality
    using simple case-insensitive substring matching.
    Returns True if any overlap found, False otherwise.
    Returns False if any input is None.
    """
    if ip_city is None or ip_region is None or geocoded_locality is None:
        return False

    # Case-insensitive substring matching
    geocoded_locality_lower = geocoded_locality.lower()
    ip_city_lower = ip_city.lower()
    ip_region_lower = ip_region.lower()

    # Check if either ip_city or ip_region is a substring of geocoded_locality
    return (ip_city_lower in geocoded_locality_lower) or (ip_region_lower in geocoded_locality_lower)