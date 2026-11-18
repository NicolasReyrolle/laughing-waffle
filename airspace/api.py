"""API clients and location services for Nominatim and OpenAIP."""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from airspace.constants import (
    HAVE_CURL_CFFI,
    NOMINATIM_SEARCH_URL,
    OPENAIP_AIRSPACES_URL,
    requests,
)
from airspace.geometry import is_airspace_in_radius

__all__ = [
    "fetch_openaip_airspaces",
    "load_dotenv_file",
    "resolve_location",
]


def load_dotenv_file(env_path: str = ".env") -> None:
    """Load key=value pairs from a .env file into os.environ if not already set.

    Args:
        env_path: Path to the .env file. Defaults to ".env".
    """
    path = Path(env_path)
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_coordinates(location_str: str) -> tuple[float, float] | None:
    """Extract (lat, lon) from location string if it matches numeric coordinates.

    Args:
        location_str: Location string to test and parse.

    Returns:
        Tuple of (lat, lon) in decimal degrees if format matches, None otherwise.
    """
    parts = re.split(r"[,;\s]+", location_str.strip())
    if len(parts) == 2 and all(
        re.fullmatch(r"[+-]?\d+(?:\.\d+)?", p) for p in parts
    ):
        return float(parts[0]), float(parts[1])
    return None


def _reverse_geocode_country(
    lat: float, lon: float, headers: dict[str, str]
) -> str:
    """Retrieve ISO-2 country code for coordinates via Nominatim reverse geocoding.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        headers: HTTP request headers.

    Returns:
        Uppercase ISO-2 country code (e.g. 'FR'), defaulting to 'FR' on failure.
    """
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            raw_json = r.json()
            addr = raw_json.get("address", {}) if isinstance(raw_json, dict) else {}
            if isinstance(addr, dict):
                return str(addr.get("country_code", "FR")).upper()
    except (OSError, ValueError) as exc:
        print(
            f"Warning: reverse geocoding failed ({exc}). Defaulting to FR.",
            file=sys.stderr,
        )
    return "FR"


def _geocode_search(
    location_str: str, headers: dict[str, str]
) -> tuple[float, float, str, str]:
    """Search for a place name via Nominatim geocoding.

    Args:
        location_str: Place name query string.
        headers: HTTP request headers.

    Returns:
        Tuple of (lat, lon, display_name, country_code).

    Raises:
        ValueError: If location cannot be found or geocoding yields no results.
        requests.RequestException: If the HTTP request to Nominatim fails.
    """
    params: dict[str, str | int] = {
        "q": location_str,
        "format": "json",
        "addressdetails": 1,
        "limit": 1,
    }
    response = requests.get(
        NOMINATIM_SEARCH_URL, params=params, headers=headers, timeout=15
    )
    response.raise_for_status()
    results: Any = response.json()
    if not isinstance(results, list) or not results:
        raise ValueError(f"Location not found via geocoding: '{location_str}'")

    first: dict[str, Any] = results[0] if isinstance(results[0], dict) else {}
    lat = float(first.get("lat", 0.0))
    lon = float(first.get("lon", 0.0))
    raw_display = str(first.get("display_name", location_str))
    display_name = raw_display.split(",")[0]
    raw_addr = first.get("address", {})
    addr_dict = raw_addr if isinstance(raw_addr, dict) else {}
    country_code = str(addr_dict.get("country_code", "FR")).upper()
    return lat, lon, display_name, country_code


def resolve_location(location_str: str) -> tuple[float, float, str, str]:
    """Parse 'lat,lon' coordinates or geocode a place name via Nominatim.

    Args:
        location_str: Either explicit coordinates (e.g. '49.4422,6.0816' or '49.4422 6.0816')
            or a place name query (e.g. 'Volmerange-les-Mines').

    Returns:
        A tuple of:
            - lat (float): Latitude in decimal degrees.
            - lon (float): Longitude in decimal degrees.
            - label (str): Human-readable display label or formatted coordinate string.
            - country_code (str): Uppercase ISO-2 country code (e.g. 'FR').

    Raises:
        ValueError: If location cannot be found or geocoding yields no results.
        requests.RequestException: If the HTTP request to Nominatim fails.
    """
    headers = {"User-Agent": "ParaglidingAirspaceKML/1.1"}
    coords = _parse_coordinates(location_str)
    if coords is not None:
        lat, lon = coords
        country_code = _reverse_geocode_country(lat, lon, headers)
        return lat, lon, f"{lat:.4f}, {lon:.4f}", country_code

    return _geocode_search(location_str, headers)


def _load_cached_country_airspaces(
    cache_file: Path, max_age_seconds: float = 7 * 86400
) -> list[dict[str, Any]] | None:
    """Load country airspace data from local cache if still valid.

    Args:
        cache_file: Path to the cached JSON file.
        max_age_seconds: Maximum allowed age in seconds. Defaults to 7 days.

    Returns:
        List of airspace dictionaries if cache is valid, None otherwise.
    """
    if not cache_file.exists():
        return None
    if (time.time() - cache_file.stat().st_mtime) >= max_age_seconds:
        return None
    try:
        data: Any = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (OSError, ValueError) as exc:
        print(
            f"Warning: could not read cache file {cache_file} ({exc}), re-fetching.",
            file=sys.stderr,
        )
    return None


def _save_cached_country_airspaces(
    cache_file: Path, data: list[dict[str, Any]]
) -> None:
    """Write country airspace data to a local cache file.

    Args:
        cache_file: Path to destination JSON cache file.
        data: List of airspace dictionaries to serialize.
    """
    try:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        print(
            f"Warning: could not write cache file {cache_file} ({exc}).",
            file=sys.stderr,
        )


def _handle_openaip_error(response: Any) -> None:
    """Raise PermissionError with diagnostic hints for 401/403 responses.

    Args:
        response: Response object from requests.

    Raises:
        PermissionError: Describing the rejection and optional Cloudflare hints.
    """
    detail = getattr(response, "text", "").strip()
    cf_hint = ""
    if not HAVE_CURL_CFFI and "challenge-platform" in detail:
        cf_hint = (
            "\nHint: Cloudflare bot protection is blocking python-requests.\n"
            "Install curl_cffi with: pip install curl_cffi"
        )
    raise PermissionError(
        f"OpenAIP request rejected ({response.status_code} Forbidden/Unauthorized).\n"
        f"API response: {detail or 'No error message provided'}\n"
        "Please verify your OPENAIP_API_KEY in .env."
        f"{cf_hint}"
    )


def _fetch_all_country_pages(
    country_code: str,
    headers: dict[str, str],
    extra_kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch all paginated airspace items for a country from OpenAIP API.

    Args:
        country_code: Uppercase ISO-2 country code.
        headers: HTTP headers including OpenAIP API key.
        extra_kwargs: Additional kwargs passed to requests.get (e.g. impersonate).

    Returns:
        List of all airspace dictionaries retrieved across all pages.

    Raises:
        PermissionError: If API key is rejected.
        requests.RequestException: If network error occurs.
    """
    country_items: list[dict[str, Any]] = []
    page = 1
    while True:
        params: dict[str, Any] = {
            "country": country_code,
            "limit": 100,
            "page": page,
        }
        response = requests.get(
            OPENAIP_AIRSPACES_URL,
            params=params,
            headers=headers,
            timeout=25,
            **extra_kwargs,
        )
        if response.status_code in (401, 403):
            _handle_openaip_error(response)
        response.raise_for_status()

        payload: Any = response.json()
        items: list[dict[str, Any]] = (
            payload.get("items", []) if isinstance(payload, dict) else []
        )
        country_items.extend(items)

        total_pages = (
            int(payload.get("totalPages", 1)) if isinstance(payload, dict) else 1
        )
        if page >= total_pages or not items:
            break
        page += 1

    return country_items


def _get_country_airspaces(
    country_code: str,
    cache_path: Path,
    headers: dict[str, str],
    extra_kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    """Retrieve airspaces for one country from cache or OpenAIP API.

    Args:
        country_code: Uppercase ISO-2 country code.
        cache_path: Base directory for local cache.
        headers: HTTP headers for API requests.
        extra_kwargs: Additional kwargs for requests.get.

    Returns:
        List of airspace dictionaries for the country.
    """
    cache_file = cache_path / f"airspaces_{country_code}.json"
    cached = _load_cached_country_airspaces(cache_file)
    if cached is not None:
        print(
            f"Loaded {len(cached)} airspaces for country {country_code} from local cache."
        )
        return cached

    print(f"Fetching airspaces for country {country_code} from OpenAIP API...")
    items = _fetch_all_country_pages(country_code, headers, extra_kwargs)
    _save_cached_country_airspaces(cache_file, items)
    print(f"Fetched {len(items)} airspaces for country {country_code}.")
    return items


def fetch_openaip_airspaces(
    lat: float,
    lon: float,
    radius_km: float,
    countries: list[str],
    api_key: str,
    cache_dir: str = ".cache",
) -> list[dict[str, Any]]:
    """Fetch airspaces for countries from OpenAIP, using local caching, and filter within radius.

    Retrieves paginated airspace data from the OpenAIP API for each specified country,
    caches responses locally for up to 7 days, and filters results geometrically
    by the specified radius around (lat, lon).

    Args:
        lat: Latitude of the center point in decimal degrees.
        lon: Longitude of the center point in decimal degrees.
        radius_km: Search radius in kilometers.
        countries: List of ISO-2 country codes to query (e.g. ['FR', 'LU']).
        api_key: OpenAIP API authentication key.
        cache_dir: Directory path for caching downloaded JSON responses. Defaults to ".cache".

    Returns:
        List of OpenAIP airspace dictionaries intersecting the search radius.

    Raises:
        PermissionError: If the API request is rejected due to invalid API key or Cloudflare block.
        requests.RequestException: If an unhandled network error occurs.
    """
    headers = {
        "x-openaip-api-key": api_key,
        "Accept": "application/json",
    }
    extra_kwargs: dict[str, Any] = {"impersonate": "chrome"} if HAVE_CURL_CFFI else {}
    cache_path = Path(cache_dir)
    cache_path.mkdir(exist_ok=True)

    all_country_airspaces: list[dict[str, Any]] = []
    for country in countries:
        c_code = country.strip().upper()
        if c_code:
            all_country_airspaces.extend(
                _get_country_airspaces(c_code, cache_path, headers, extra_kwargs)
            )

    return [
        asp
        for asp in all_country_airspaces
        if is_airspace_in_radius(asp, lat, lon, radius_km)
    ]
