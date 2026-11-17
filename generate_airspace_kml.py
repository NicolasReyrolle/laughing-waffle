#!/usr/bin/env python3
"""Generate a 3D KML file of surrounding airspaces from OpenAIP for paragliding,
with automatic DEM terrain elevation resolution for AGL/GND vertical limits.
"""

import argparse
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.dom import minidom

try:
    from curl_cffi import requests  # type: ignore[no-redef]

    _curl_cffi_available = True
except ImportError:
    import requests  # type: ignore[no-redef]

    _curl_cffi_available = False

HAVE_CURL_CFFI: bool = _curl_cffi_available

OPENAIP_AIRSPACES_URL = "https://api.core.openaip.net/api/airspaces"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# OpenAIP ICAO class integer codes
ICAO_CLASS_MAP: dict[int, str] = {
    0: "A",
    1: "B",
    2: "C",
    3: "D",
    4: "E",
    5: "F",
    6: "G",
    8: "SUA",
}

# OpenAIP airspace type integer codes
AIRSPACE_TYPE_MAP: dict[int, str] = {
    0: "OTHER",
    1: "R",
    2: "D",
    3: "P",
    4: "CTR",
    5: "TMZ",
    6: "RMZ",
    7: "TMA",
    8: "TRA",
    9: "TSA",
    10: "FIR",
    11: "UIR",
    12: "ADIZ",
    13: "ATZ",
    14: "MATZ",
    15: "AIRWAY",
    16: "MTR",
    17: "ALERT",
    18: "WARNING",
    19: "PROTECTED",
    20: "HTZ",
    21: "GLIDING",
    22: "TRP",
    23: "TIZ",
    24: "TIA",
    25: "MTMA",
    26: "CTA",
    27: "ACC",
    28: "SPORT",
    29: "LOW_ALT_RESTRICTION",
}

# Color definitions (hex RGB + KML folder label)
CATEGORY_STYLES: dict[str, dict[str, str]] = {
    "RED": {
        "folder": "1. Rouge - Interdit / Controle H24 (CTR, P, TMA A-D)",
        "hex": "#E53935",
    },
    "ORANGE": {
        "folder": "2. Orange - Activable / Reglemente (R, D, TMA HX/NOTAM)",
        "hex": "#FB8C00",
    },
    "YELLOW": {
        "folder": "3. Jaune - Vigilance / Radio-Transpondeur (RMZ, TMZ, ATZ, Sport)",
        "hex": "#FDD835",
    },
    "BLUE": {
        "folder": "4. Bleu - Classe E (VFR autorise, trafic IFR)",
        "hex": "#1E88E5",
    },
}


class ElevationResolver:
    """Batch terrain elevation resolver using Open-Meteo DEM API with in-memory caching."""

    BATCH_SIZE = 100

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._cache: dict[tuple[float, float], float] = {}
        self._warned = False

    def _fetch_elevation_chunk(self, chunk: list[tuple[float, float]]) -> None:
        """Fetch elevations for one batch and populate the cache."""
        lats = ",".join(f"{lat:.4f}" for lat, _ in chunk)
        lons = ",".join(f"{lon:.4f}" for _, lon in chunk)
        try:
            resp = requests.get(
                OPEN_METEO_ELEVATION_URL,
                params={"latitude": lats, "longitude": lons},
                headers={"User-Agent": "ParaglidingAirspaceKML/1.1"},
                timeout=15,
            )
            resp.raise_for_status()
            elevations = resp.json().get("elevation", [])
            if len(elevations) != len(chunk):
                raise ValueError("Unexpected elevation array length from DEM API.")
            for key, elev in zip(chunk, elevations):
                self._cache[key] = float(elev) if elev is not None else 0.0
        except (OSError, ValueError) as exc:
            if not self._warned:
                print(
                    f"Warning: DEM elevation lookup failed ({exc}). Falling back to 0 m.",
                    file=sys.stderr,
                )
                self._warned = True
            for key in chunk:
                self._cache[key] = 0.0

    def get_elevations(self, coords: list[tuple[float, float]]) -> list[float]:
        """Return terrain elevations in meters AMSL for a list of (lon, lat) tuples."""
        if not coords or not self.enabled:
            return [0.0] * len(coords)

        # Round to 4 decimals (~11 m grid) to maximize cache hits
        keys = [(round(lat, 4), round(lon, 4)) for lon, lat in coords]
        missing = list(dict.fromkeys(k for k in keys if k not in self._cache))

        for start in range(0, len(missing), self.BATCH_SIZE):
            self._fetch_elevation_chunk(missing[start : start + self.BATCH_SIZE])

        return [self._cache[k] for k in keys]

    def get_single(self, lon: float, lat: float) -> float:
        """Return terrain elevation in meters AMSL for a single (lon, lat) point."""
        return self.get_elevations([(lon, lat)])[0]


def load_dotenv_file(env_path: str = ".env") -> None:
    """Load key=value pairs from a .env file into os.environ if not already set."""
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
    """Parse 'lat,lon' coordinates or geocode a place name via Nominatim, returning (lat, lon, label, country_code)."""
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

    params = {"q": location_str, "format": "json", "addressdetails": 1, "limit": 1}
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


def parse_vertical_limit(limit_obj: dict[str, Any] | None) -> tuple[float, str, bool]:
    """Convert an OpenAIP vertical limit object to (meters, label, is_agl)."""
    if not limit_obj:
        return 0.0, "SFC", True

    value = float(limit_obj.get("value", 0))
    unit = limit_obj.get("unit", 1)  # 0: m, 1: ft, 6: FL
    datum = limit_obj.get("referenceDatum", 1)  # 0: GND (AGL), 1: MSL, 2: STD

    is_agl = datum == 0
    datum_str = {0: "AGL", 1: "AMSL", 2: "STD"}.get(datum, "AMSL")

    if value == 0 and is_agl:
        return 0.0, "SFC", True

    if unit == 6:  # Flight Level
        meters = value * 100.0 * 0.3048
        label = f"FL {int(value):03d}"
    elif unit == 0:  # Meters
        meters = value
        label = f"{int(value)} m {datum_str}"
    else:  # Feet (default unit == 1)
        meters = value * 0.3048
        label = f"{int(value)} ft {datum_str}"

    return round(meters, 1), label, is_agl


def _is_h24_schedule(oh: Any) -> bool:
    """Return True if operating hours represent a permanent H24 schedule."""
    if isinstance(oh, str):
        return oh.upper() == "H24"
    if isinstance(oh, list) and oh:
        return len(oh) == 7 and all(
            d.get("startTime") == "00:00"
            and d.get("endTime") == "00:00"
            and not d.get("byNotam")
            for d in oh
        )
    return True


def is_activatable_airspace(airspace: dict[str, Any]) -> bool:
    """Detect whether an airspace is conditional/activatable rather than permanent H24."""
    if airspace.get("byNotam") or airspace.get("onDemand") or airspace.get("onRequest"):
        return True

    hours = airspace.get("hoursOfOperation")
    if isinstance(hours, dict):
        oh = hours.get("operatingHours")
        if not _is_h24_schedule(oh):
            return True

    text_blob = " ".join(
        str(airspace.get(field, "")) for field in ("name", "remarks", "activity")
    ).upper()
    conditional_keywords = ("HX", "NOTAM", "ACTIVABLE", "SUP AIP", "AZBA", "BY NOTAM")
    return any(kw in text_blob for kw in conditional_keywords)


def _classify_controlled(
    airspace: dict[str, Any], asp_type: str, icao_class: str
) -> str:
    """Return RED or ORANGE for controlled/restricted airspace types."""
    if is_activatable_airspace(airspace):
        return "ORANGE"
    if asp_type in ("TMA", "CTA", "MTMA"):
        return "RED" if icao_class in ("A", "B", "C", "D") else "ORANGE"
    if icao_class in ("A", "B", "C", "D") or asp_type in ("P", "CTR"):
        return "RED"
    return "ORANGE"


def classify_airspace(
    airspace: dict[str, Any],
    include_other: bool = False,
    excluded_types: set[str] | None = None,
) -> str | None:
    """Assign a color category (RED, ORANGE, YELLOW, BLUE) or None if irrelevant."""
    asp_type = AIRSPACE_TYPE_MAP.get(airspace.get("type", 0), "OTHER")
    icao_class = ICAO_CLASS_MAP.get(airspace.get("icaoClass", 8), "SUA")

    if asp_type in ("FIR", "UIR", "ACC", "ADIZ"):
        return None
    if asp_type == "OTHER" and not include_other:
        return None
    if excluded_types and asp_type in excluded_types:
        return None
    if icao_class == "E":
        return "BLUE"
    if asp_type in (
        "RMZ",
        "TMZ",
        "ATZ",
        "MATZ",
        "HTZ",
        "TIZ",
        "TIA",
        "GLIDING",
        "SPORT",
    ):
        return "YELLOW"
    if asp_type in ("R", "D", "TRA", "TSA", "MTR", "ALERT", "WARNING", "PROTECTED"):
        return "ORANGE"
    return _classify_controlled(airspace, asp_type, icao_class)


def point_in_polygon(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting algorithm to test if (lon, lat) is inside a polygon ring."""
    inside = False
    n = len(ring)
    for i in range(n):
        j = (i - 1) % n
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
    return inside


def is_airspace_in_radius(
    asp: dict[str, Any], center_lat: float, center_lon: float, radius_km: float
) -> bool:
    """Check if any point of an airspace polygon is within radius_km, or center is inside."""
    rings = extract_polygon_rings(asp.get("geometry", {}))
    for ring in rings:
        for lon, lat in ring:
            if haversine_km(center_lon, center_lat, lon, lat) <= radius_km:
                return True
        if point_in_polygon(center_lon, center_lat, ring):
            return True
    return False


def fetch_openaip_airspaces(
    lat: float,
    lon: float,
    radius_km: float,
    countries: list[str],
    api_key: str,
    cache_dir: str = ".cache",
) -> list[dict[str, Any]]:
    """Fetch airspaces for countries from OpenAIP, using local caching, and filter within radius_km."""
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


def kml_color(alpha_percent: int, hex_rgb: str) -> str:
    """Convert opacity percentage (0-100) and #RRGGBB to KML aabbggrr format."""
    clean_hex = hex_rgb.lstrip("#")
    r, g, b = clean_hex[0:2], clean_hex[2:4], clean_hex[4:6]
    alpha = f"{int(alpha_percent / 100.0 * 255):02x}"
    return f"{alpha}{b}{g}{r}".lower()


def extract_polygon_rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Extract outer coordinate rings from a GeoJSON Polygon or MultiPolygon."""
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])
    rings: list[list[tuple[float, float]]] = []

    if geom_type == "Polygon" and coords:
        rings.append([(float(pt[0]), float(pt[1])) for pt in coords[0]])
    elif geom_type == "MultiPolygon":
        for poly in coords:
            if poly and poly[0]:
                rings.append([(float(pt[0]), float(pt[1])) for pt in poly[0]])

    return rings


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calculate great-circle distance in kilometers between two WGS84 points."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def densify_ring(
    ring: list[tuple[float, float]], max_segment_km: float = 1.5
) -> list[tuple[float, float]]:
    """Subdivide long polygon segments so terrain-following AGL surfaces hug relief."""
    if len(ring) < 2:
        return ring
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]

    densified: list[tuple[float, float]] = []
    for i in range(len(ring) - 1):
        lon1, lat1 = ring[i]
        lon2, lat2 = ring[i + 1]
        dist = haversine_km(lon1, lat1, lon2, lat2)
        steps = max(1, math.ceil(dist / max_segment_km))
        for step in range(steps):
            t = step / steps
            densified.append(
                (round(lon1 + (lon2 - lon1) * t, 6), round(lat1 + (lat2 - lat1) * t, 6))
            )
    densified.append(densified[0])
    return densified


def generate_circle_coords(
    lat: float, lon: float, radius_km: float, num_points: int = 72
) -> list[tuple[float, float]]:
    """Generate a closed geographic circle ring around (lat, lon)."""
    earth_radius_km = 6371.0
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    angular_dist = radius_km / earth_radius_km

    ring: list[tuple[float, float]] = []
    for i in range(num_points + 1):
        bearing = 2.0 * math.pi * (i % num_points) / num_points
        pt_lat = math.asin(
            math.sin(lat_rad) * math.cos(angular_dist)
            + math.cos(lat_rad) * math.sin(angular_dist) * math.cos(bearing)
        )
        pt_lon = lon_rad + math.atan2(
            math.sin(bearing) * math.sin(angular_dist) * math.cos(lat_rad),
            math.cos(angular_dist) - math.sin(lat_rad) * math.sin(pt_lat),
        )
        ring.append((round(math.degrees(pt_lon), 6), round(math.degrees(pt_lat), 6)))
    return ring


def add_3d_volume_to_multigeometry(
    multi_geo: ET.Element,
    ring: list[tuple[float, float]],
    floor_amsls: list[float],
    ceil_amsls: list[float],
    draw_floor_face: bool,
) -> None:
    """Append closed 3D faces (floor, ceiling, and walls) with per-vertex AMSL altitudes."""
    if len(ring) < 3:
        return
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
        floor_amsls = floor_amsls + [floor_amsls[0]]
        ceil_amsls = ceil_amsls + [ceil_amsls[0]]

    # Prevent inverted walls if local terrain rises above an AMSL ceiling
    safe_ceils = [max(c, f) for f, c in zip(floor_amsls, ceil_amsls)]

    # 1. Floor face (drawn when floor is strictly above ground)
    if draw_floor_face:
        poly_floor = ET.SubElement(multi_geo, "Polygon")
        ET.SubElement(poly_floor, "altitudeMode").text = "absolute"
        outer_floor = ET.SubElement(
            ET.SubElement(poly_floor, "outerBoundaryIs"), "LinearRing"
        )
        ET.SubElement(outer_floor, "coordinates").text = " ".join(
            f"{lon},{lat},{round(fl, 1)}" for (lon, lat), fl in zip(ring, floor_amsls)
        )

    # 2. Ceiling face
    poly_ceil = ET.SubElement(multi_geo, "Polygon")
    ET.SubElement(poly_ceil, "altitudeMode").text = "absolute"
    outer_ceil = ET.SubElement(
        ET.SubElement(poly_ceil, "outerBoundaryIs"), "LinearRing"
    )
    ET.SubElement(outer_ceil, "coordinates").text = " ".join(
        f"{lon},{lat},{round(ce, 1)}" for (lon, lat), ce in zip(ring, safe_ceils)
    )

    # 3. Vertical side walls
    for i in range(len(ring) - 1):
        lon1, lat1 = ring[i]
        lon2, lat2 = ring[i + 1]
        f1, f2 = round(floor_amsls[i], 1), round(floor_amsls[i + 1], 1)
        c1, c2 = round(safe_ceils[i], 1), round(safe_ceils[i + 1], 1)

        wall = ET.SubElement(multi_geo, "Polygon")
        ET.SubElement(wall, "altitudeMode").text = "absolute"
        outer_wall = ET.SubElement(ET.SubElement(wall, "outerBoundaryIs"), "LinearRing")
        ET.SubElement(outer_wall, "coordinates").text = (
            f"{lon1},{lat1},{f1} "
            f"{lon2},{lat2},{f2} "
            f"{lon2},{lat2},{c2} "
            f"{lon1},{lat1},{c1} "
            f"{lon1},{lat1},{f1}"
        )


def format_amsl_range(values: list[float]) -> str:
    """Format a single altitude or min-max range in meters AMSL."""
    if not values:
        return "0 m AMSL"
    lo, hi = round(min(values)), round(max(values))
    return f"{lo} m AMSL" if abs(hi - lo) <= 5 else f"~{lo}-{hi} m AMSL"


def _build_reference_folder(
    doc: ET.Element,
    center_lat: float,
    center_lon: float,
    location_label: str,
    radius_km: float,
    max_altitude_m: float,
    center_elev: float,
) -> None:
    """Append the central marker and radius ring folder to the KML document."""
    ref_folder = ET.SubElement(doc, "Folder")
    ET.SubElement(ref_folder, "name").text = "0. Repere Central & Rayon"

    center_pm = ET.SubElement(ref_folder, "Placemark")
    ET.SubElement(center_pm, "name").text = f"Point Central : {location_label}"
    ET.SubElement(center_pm, "description").text = (
        f"Coordonnees : {center_lat:.5f}, {center_lon:.5f}<br>"
        f"Altitude terrain (DEM) : {round(center_elev)} m AMSL<br>"
        f"Rayon de recherche : {radius_km:g} km<br>"
        f"Plafond visuel : {int(max_altitude_m)} m AMSL"
    )
    pt = ET.SubElement(center_pm, "Point")
    ET.SubElement(pt, "altitudeMode").text = "clampToGround"
    ET.SubElement(pt, "coordinates").text = f"{center_lon},{center_lat},0"

    radius_pm = ET.SubElement(ref_folder, "Placemark")
    ET.SubElement(radius_pm, "name").text = f"Cercle Rayon ({radius_km:g} km)"
    ring_style = ET.SubElement(radius_pm, "Style")
    ring_line = ET.SubElement(ring_style, "LineStyle")
    ET.SubElement(ring_line, "color").text = kml_color(75, "#FFFFFF")
    ET.SubElement(ring_line, "width").text = "2.0"
    line_string = ET.SubElement(radius_pm, "LineString")
    ET.SubElement(line_string, "tessellate").text = "1"
    ET.SubElement(line_string, "altitudeMode").text = "clampToGround"
    ET.SubElement(line_string, "coordinates").text = " ".join(
        f"{lon},{lat},0"
        for lon, lat in generate_circle_coords(center_lat, center_lon, radius_km)
    )


_RingData = tuple[list[tuple[float, float]], list[float], list[float]]


def _resolve_ring_altitudes(
    raw_ring: list[tuple[float, float]],
    floor_val_m: float,
    floor_is_agl: bool,
    ceil_val_m: float,
    ceil_is_agl: bool,
    max_altitude_m: float,
    elevation_resolver: ElevationResolver,
    needs_terrain: bool,
) -> tuple[list[tuple[float, float]], list[float], list[float], list[float]] | None:
    """Resolve AMSL altitudes for one ring; returns (ring, floor_amsls, visual_ceil_amsls, raw_ceil_amsls) or None if filtered."""
    ring = densify_ring(raw_ring, max_segment_km=1.5) if needs_terrain else raw_ring
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]

    terrains = (
        elevation_resolver.get_elevations(ring) if needs_terrain else [0.0] * len(ring)
    )

    if floor_is_agl:
        floor_amsls = (
            [t + floor_val_m for t in terrains]
            if floor_val_m > 0
            else [0.0] * len(ring)
        )
    else:
        floor_amsls = [floor_val_m] * len(ring)

    raw_ceil_amsls = (
        [t + ceil_val_m for t in terrains] if ceil_is_agl else [ceil_val_m] * len(ring)
    )
    visual_ceil_amsls = [min(c, max_altitude_m) for c in raw_ceil_amsls]

    if min(floor_amsls) >= max_altitude_m or max(visual_ceil_amsls) <= min(floor_amsls):
        return None
    return ring, floor_amsls, visual_ceil_amsls, raw_ceil_amsls


def _collect_resolved_rings(
    raw_rings: list[list[tuple[float, float]]],
    floor_val_m: float,
    floor_is_agl: bool,
    ceil_val_m: float,
    ceil_is_agl: bool,
    max_altitude_m: float,
    elevation_resolver: ElevationResolver,
) -> tuple[list[_RingData], list[float], list[float]]:
    """Resolve all rings for one airspace; returns (resolved_rings_data, all_floors_amsl, all_raw_ceils_amsl)."""
    needs_terrain = (floor_is_agl and floor_val_m > 0) or ceil_is_agl
    resolved: list[_RingData] = []
    all_floors: list[float] = []
    all_ceils: list[float] = []
    for raw_ring in raw_rings:
        result = _resolve_ring_altitudes(
            raw_ring,
            floor_val_m,
            floor_is_agl,
            ceil_val_m,
            ceil_is_agl,
            max_altitude_m,
            elevation_resolver,
            needs_terrain,
        )
        if result is None:
            continue
        ring, floor_amsls, visual_ceil_amsls, raw_ceil_amsls = result
        resolved.append((ring, floor_amsls, visual_ceil_amsls))
        all_floors.extend(floor_amsls)
        all_ceils.extend(raw_ceil_amsls)
    return resolved, all_floors, all_ceils


def _build_airspace_placemark(
    folder: ET.Element,
    asp: dict[str, Any],
    category: str,
    icao_class: str,
    floor_val_m: float,
    floor_label: str,
    floor_is_agl: bool,
    ceil_label: str,
    max_altitude_m: float,
    all_floors_amsl: list[float],
    all_raw_ceils_amsl: list[float],
    resolved_rings_data: list[
        tuple[list[tuple[float, float]], list[float], list[float]]
    ],
) -> None:
    """Append a KML Placemark with 3D geometry for one airspace into its category folder."""
    asp_name = asp.get("name", "Unnamed Airspace")
    asp_type = AIRSPACE_TYPE_MAP.get(asp.get("type", 0), "OTHER")
    remarks = asp.get("remarks", "")
    floor_amsl_info = (
        "Sol (SFC)"
        if (floor_is_agl and floor_val_m == 0)
        else format_amsl_range(all_floors_amsl)
    )

    pm = ET.SubElement(folder, "Placemark")
    ET.SubElement(pm, "name").text = f"{asp_name} [{floor_label} - {ceil_label}]"
    ET.SubElement(pm, "description").text = (
        f"<b>Type :</b> {asp_type} | <b>Classe :</b> {icao_class}<br>"
        f"<b>Plancher :</b> {floor_label} ({floor_amsl_info})<br>"
        f"<b>Plafond reel :</b> {ceil_label} ({format_amsl_range(all_raw_ceils_amsl)})<br>"
        f"<b>Plafond affiche :</b> max {int(max_altitude_m)} m AMSL<br>"
        f"{f'<br><i>{remarks}</i>' if remarks else ''}"
    )
    ET.SubElement(pm, "styleUrl").text = f"#style_{category}"

    draw_floor_face = not (floor_is_agl and floor_val_m == 0)
    multi_geo = ET.SubElement(pm, "MultiGeometry")
    for ring, floor_amsls, visual_ceil_amsls in resolved_rings_data:
        add_3d_volume_to_multigeometry(
            multi_geo=multi_geo,
            ring=ring,
            floor_amsls=floor_amsls,
            ceil_amsls=visual_ceil_amsls,
            draw_floor_face=draw_floor_face,
        )


def build_kml(
    airspaces: list[dict[str, Any]],
    center_lat: float,
    center_lon: float,
    location_label: str,
    radius_km: float,
    max_altitude_m: float,
    excluded_classes: set[str],
    elevation_resolver: ElevationResolver,
    output_path: str,
    include_other: bool = False,
    excluded_types: set[str] | None = None,
) -> int:
    """Build and write the 3D KML document; returns count of exported airspaces."""
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = (
        f"Espaces Aeriens 3D - {location_label} ({radius_km:g} km / max {int(max_altitude_m)} m)"
    )

    for cat_key, cat_info in CATEGORY_STYLES.items():
        style = ET.SubElement(doc, "Style", id=f"style_{cat_key}")
        line_style = ET.SubElement(style, "LineStyle")
        ET.SubElement(line_style, "color").text = kml_color(85, cat_info["hex"])
        ET.SubElement(line_style, "width").text = "1.5"
        poly_style = ET.SubElement(style, "PolyStyle")
        ET.SubElement(poly_style, "color").text = kml_color(25, cat_info["hex"])
        ET.SubElement(poly_style, "outline").text = "1"

    center_elev = elevation_resolver.get_single(center_lon, center_lat)
    _build_reference_folder(
        doc,
        center_lat,
        center_lon,
        location_label,
        radius_km,
        max_altitude_m,
        center_elev,
    )

    category_folders: dict[str, ET.Element] = {}
    for cat_key, cat_info in CATEGORY_STYLES.items():
        folder = ET.SubElement(doc, "Folder")
        ET.SubElement(folder, "name").text = cat_info["folder"]
        category_folders[cat_key] = folder

    exported_count = 0
    for asp in airspaces:
        icao_class = ICAO_CLASS_MAP.get(asp.get("icaoClass", 8), "SUA")
        if icao_class in excluded_classes:
            continue
        category = classify_airspace(
            asp, include_other=include_other, excluded_types=excluded_types
        )
        if not category:
            continue

        floor_val_m, floor_label, floor_is_agl = parse_vertical_limit(
            asp.get("lowerLimit")
        )
        ceil_val_m, ceil_label, ceil_is_agl = parse_vertical_limit(
            asp.get("upperLimit")
        )
        raw_rings = extract_polygon_rings(asp.get("geometry", {}))
        if not raw_rings:
            continue

        resolved_rings_data, all_floors_amsl, all_raw_ceils_amsl = (
            _collect_resolved_rings(
                raw_rings,
                floor_val_m,
                floor_is_agl,
                ceil_val_m,
                ceil_is_agl,
                max_altitude_m,
                elevation_resolver,
            )
        )
        if not resolved_rings_data:
            continue

        _build_airspace_placemark(
            folder=category_folders[category],
            asp=asp,
            category=category,
            icao_class=icao_class,
            floor_val_m=floor_val_m,
            floor_label=floor_label,
            floor_is_agl=floor_is_agl,
            ceil_label=ceil_label,
            max_altitude_m=max_altitude_m,
            all_floors_amsl=all_floors_amsl,
            all_raw_ceils_amsl=all_raw_ceils_amsl,
            resolved_rings_data=resolved_rings_data,
        )
        exported_count += 1

    # Remove empty folders so the KML tree stays clean
    for folder in category_folders.values():
        if folder.find("Placemark") is None:
            doc.remove(folder)

    pretty_xml = minidom.parseString(ET.tostring(kml, encoding="utf-8")).toprettyxml(
        indent="  ", encoding="utf-8"
    )
    Path(output_path).write_bytes(pretty_xml)
    return exported_count


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate a 3D KML of OpenAIP airspaces around a location for paragliding."
    )
    parser.add_argument(
        "-l",
        "--location",
        required=True,
        help="City/locality name (e.g. 'Volmerange-les-Mines') or 'lat,lon' coordinates.",
    )
    parser.add_argument(
        "-r",
        "--radius",
        type=float,
        default=25.0,
        help="Search radius in kilometers around the location (default: 25).",
    )
    parser.add_argument(
        "-a",
        "--altitude",
        type=float,
        default=3000.0,
        help="Max altitude in meters AMSL to filter floors and cap 3D ceilings (default: 3000).",
    )
    parser.add_argument(
        "--exclude-classes",
        nargs="*",
        default=[],
        help="ICAO airspace classes to exclude, e.g. '--exclude-classes E F G'.",
    )
    parser.add_argument(
        "--exclude-types",
        nargs="*",
        default=[],
        help="Airspace types to exclude, e.g. '--exclude-types SPORT GLIDING'.",
    )
    parser.add_argument(
        "--include-other",
        action="store_true",
        help="Include airspaces of type 'OTHER' (excluded by default).",
    )
    parser.add_argument(
        "--no-dem",
        action="store_true",
        help="Disable Open-Meteo DEM terrain elevation resolution for AGL limits.",
    )
    parser.add_argument(
        "-c",
        "--countries",
        nargs="*",
        default=[],
        help="ISO-2 country codes to query from OpenAIP (e.g. '-c FR LU'). Defaults to location's country.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="Output KML file path (default: auto-generated from location).",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file containing OPENAIP_API_KEY (default: .env).",
    )
    return parser.parse_args()


def main() -> None:
    """Main CLI entrypoint."""
    args = parse_args()
    load_dotenv_file(args.env_file)

    api_key = os.environ.get("OPENAIP_API_KEY", "").strip()
    if not api_key:
        print(
            "Error: OPENAIP_API_KEY not found. Set it in your .env file or environment.",
            file=sys.stderr,
        )
        sys.exit(1)

    lat, lon, label, detected_country = resolve_location(args.location)
    print(
        f"Target location: {label} ({lat:.5f}, {lon:.5f}) | Radius: {args.radius:g} km"
    )

    countries: list[str] = []
    for c_arg in args.countries:
        for c in c_arg.split(","):
            c = c.strip().upper()
            if c and c not in countries:
                countries.append(c)
    if not countries and detected_country:
        countries = [detected_country]

    print(f"Querying OpenAIP countries: {', '.join(countries)}")
    airspaces = fetch_openaip_airspaces(lat, lon, args.radius, countries, api_key)
    print(f"Found {len(airspaces)} airspaces within {args.radius:g} km radius.")

    elevation_resolver = ElevationResolver(enabled=not args.no_dem)
    excluded_classes = {cls.upper() for cls in args.exclude_classes}
    excluded_types = {t.upper() for t in args.exclude_types}
    slug = re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_-]", "_", label.lower())).strip("_")
    output_path = args.output or f"airspaces_{slug}_{int(args.radius)}km.kml"

    count = build_kml(
        airspaces=airspaces,
        center_lat=lat,
        center_lon=lon,
        location_label=label,
        radius_km=args.radius,
        max_altitude_m=args.altitude,
        excluded_classes=excluded_classes,
        elevation_resolver=elevation_resolver,
        output_path=output_path,
        include_other=args.include_other,
        excluded_types=excluded_types,
    )
    print(f"Generated '{output_path}' with {count} 3D airspace volumes.")


if __name__ == "__main__":
    main()
