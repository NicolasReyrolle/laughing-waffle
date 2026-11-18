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
    parts = re.split(r"[,;\s]+", location_str.strip())
    coord_match = len(parts) == 2 and all(
        re.fullmatch(r"[+-]?\d+(?:\.\d+)?", p) for p in parts
    )
    if coord_match:
        lat = float(parts[0])
        lon = float(parts[1])
        country_code = "FR"
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json"},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                country_code = (
                    r.json().get("address", {}).get("country_code", "FR").upper()
                )
        except (OSError, ValueError) as exc:
            print(
                f"Warning: reverse geocoding failed ({exc}). Defaulting to FR.",
                file=sys.stderr,
            )
        return lat, lon, f"{lat:.4f}, {lon:.4f}", country_code

    params: dict[str, str | int] = {"q": location_str, "format": "json", "addressdetails": 1, "limit": 1}
    response = requests.get(
        NOMINATIM_SEARCH_URL, params=params, headers=headers, timeout=15
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise ValueError(f"Location not found via geocoding: '{location_str}'")

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    display_name = results[0].get("display_name", location_str).split(",")[0]
    country_code = results[0].get("address", {}).get("country_code", "FR").upper()
    return lat, lon, display_name, country_code


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
        if not c_code:
            continue
        c_file = cache_path / f"airspaces_{c_code}.json"

        # Use cache if less than 7 days old
        if c_file.exists() and (time.time() - c_file.stat().st_mtime) < 7 * 86400:
            try:
                cached_data = json.loads(c_file.read_text(encoding="utf-8"))
                all_country_airspaces.extend(cached_data)
                print(
                    f"Loaded {len(cached_data)} airspaces for country {c_code} from local cache."
                )
                continue
            except (OSError, ValueError) as exc:
                print(
                    f"Warning: could not read cache file {c_file} ({exc}), re-fetching.",
                    file=sys.stderr,
                )

        print(f"Fetching airspaces for country {c_code} from OpenAIP API...")
        country_items: list[dict[str, Any]] = []
        page = 1
        while True:
            params: dict[str, Any] = {
                "country": c_code,
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
                detail = response.text.strip()
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
            response.raise_for_status()

            payload = response.json()
            items = payload.get("items", [])
            country_items.extend(items)

            total_pages = payload.get("totalPages", 1)
            if page >= total_pages or not items:
                break
            page += 1

        try:
            c_file.write_text(json.dumps(country_items), encoding="utf-8")
        except OSError as exc:
            print(
                f"Warning: could not write cache file {c_file} ({exc}).",
                file=sys.stderr,
            )

        print(f"Fetched {len(country_items)} airspaces for country {c_code}.")
        all_country_airspaces.extend(country_items)

    # Filter geometrically by radius around (lat, lon)
    filtered = [
        asp
        for asp in all_country_airspaces
        if is_airspace_in_radius(asp, lat, lon, radius_km)
    ]
    return filtered
