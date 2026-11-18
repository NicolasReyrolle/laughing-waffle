"""Airspace package for OpenAIP fetching, classification, and 3D KML generation."""

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
    "ElevationResolver",
    "HAVE_CURL_CFFI",
    "ICAO_CLASS_MAP",
    "NOMINATIM_SEARCH_URL",
    "OPENAIP_AIRSPACES_URL",
    "OPEN_METEO_ELEVATION_URL",
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
    "parse_vertical_limit",
    "point_in_polygon",
    "requests",
    "resolve_location",
]
