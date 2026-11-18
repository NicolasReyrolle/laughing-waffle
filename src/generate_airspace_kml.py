#!/usr/bin/env python3
"""Generate a 3D KML file of surrounding airspaces from OpenAIP for paragliding,
with automatic DEM terrain elevation resolution for AGL/GND vertical limits.
"""

import argparse
import os
import re
import sys

from airspace.api import (
    fetch_openaip_airspaces,
    load_dotenv_file,
    resolve_location,
)
from airspace.classification import (
    _classify_controlled,
    _is_h24_schedule,
    classify_airspace,
    is_activatable_airspace,
    parse_vertical_limit,
)
from airspace.constants import (
    AIRSPACE_TYPE_MAP,
    CATEGORY_STYLES,
    HAVE_CURL_CFFI,
    ICAO_CLASS_MAP,
    NOMINATIM_SEARCH_URL,
    OPEN_METEO_ELEVATION_URL,
    OPENAIP_AIRSPACES_URL,
    requests,
)
from airspace.elevation import ElevationResolver
from airspace.geometry import (
    densify_ring,
    extract_polygon_rings,
    generate_circle_coords,
    haversine_km,
    is_airspace_in_radius,
    point_in_polygon,
)
from airspace.kml import (
    _build_airspace_placemark,
    _build_reference_folder,
    _collect_resolved_rings,
    _resolve_ring_altitudes,
    add_3d_volume_to_multigeometry,
    build_kml,
    format_amsl_range,
    kml_color,
)

__all__ = [
    "AIRSPACE_TYPE_MAP",
    "CATEGORY_STYLES",
    "HAVE_CURL_CFFI",
    "ICAO_CLASS_MAP",
    "NOMINATIM_SEARCH_URL",
    "OPENAIP_AIRSPACES_URL",
    "OPEN_METEO_ELEVATION_URL",
    "ElevationResolver",
    "_build_airspace_placemark",
    "_build_reference_folder",
    "_classify_controlled",
    "_collect_resolved_rings",
    "_is_h24_schedule",
    "_resolve_ring_altitudes",
    "add_3d_volume_to_multigeometry",
    "build_kml",
    "classify_airspace",
    "densify_ring",
    "extract_polygon_rings",
    "fetch_openaip_airspaces",
    "format_amsl_range",
    "generate_circle_coords",
    "haversine_km",
    "is_activatable_airspace",
    "is_airspace_in_radius",
    "kml_color",
    "load_dotenv_file",
    "main",
    "parse_args",
    "parse_vertical_limit",
    "point_in_polygon",
    "requests",
    "resolve_location",
]


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the airspace KML generator script.

    Returns:
        Parsed CLI arguments namespace containing location, radius, altitude,
        class/type exclusions, country filters, output path, and DEM options.
    """
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
    """Main CLI entrypoint to orchestrate airspace fetching, DEM resolution, and KML generation."""
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
