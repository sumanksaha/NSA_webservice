import requests
import time

# Module-level variable for rate limiting
_last_request_time = 0


def reverse_geocode(lat: float, lng: float) -> dict:
    """
    Calls Nominatim (OpenStreetMap) reverse geocoding API.
    Returns: {"locality": str or None, "raw_response": dict or None, "error": str or None}
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
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        'lat': lat,
        'lon': lng,
        'format': 'json'
    }
    headers = {
        'User-Agent': 'NSA_webservice/1.0'
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()

        # Extract locality
        locality = None
        address = data.get('address', {})

        # Prefer suburb, then city_district, then city, then display_name
        if address.get('suburb'):
            locality = address['suburb']
        elif address.get('city_district'):
            locality = address['city_district']
        elif address.get('city'):
            locality = address['city']
        elif 'display_name' in data:
            locality = data['display_name']

        return {
            'locality': locality,
            'raw_response': data,
            'error': None
        }
    except requests.exceptions.Timeout:
        return {
            'locality': None,
            'raw_response': None,
            'error': 'Request timed out'
        }
    except requests.exceptions.RequestException as e:
        return {
            'locality': None,
            'raw_response': None,
            'error': f'Request failed: {str(e)}'
        }
    except Exception as e:
        return {
            'locality': None,
            'raw_response': None,
            'error': f'Unexpected error: {str(e)}'
        }