"""KML 3D geometry generation, styling, and file export for airspace volumes."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from xml.dom import minidom

from airspace.classification import classify_airspace, parse_vertical_limit
from airspace.constants import AIRSPACE_TYPE_MAP, CATEGORY_STYLES, ICAO_CLASS_MAP
from airspace.elevation import ElevationResolver
from airspace.geometry import densify_ring, extract_polygon_rings, generate_circle_coords


def kml_color(alpha_percent: int, hex_rgb: str) -> str:
    """Convert opacity percentage and RGB hex string to KML aabbggrr format.

    Args:
        alpha_percent: Opacity percentage between 0 (fully transparent) and 100 (fully opaque).
        hex_rgb: Hexadecimal color string, formatted as '#RRGGBB' or 'RRGGBB'.

    Returns:
        8-character lowercase hex string in KML 'aabbggrr' order (alpha, blue, green, red).
    """
    clean_hex = hex_rgb.lstrip("#")
    r, g, b = clean_hex[0:2], clean_hex[2:4], clean_hex[4:6]
    alpha = f"{int(alpha_percent / 100.0 * 255):02x}"
    return f"{alpha}{b}{g}{r}".lower()


def format_amsl_range(values: list[float]) -> str:
    """Format a collection of altitudes as a single altitude or a min-max range in meters AMSL.

    Args:
        values: List of altitude values in meters AMSL.

    Returns:
        Formatted string, e.g. '1500 m AMSL' if variation is <= 5 m,
        or '~1200-1850 m AMSL' if values vary across terrain.
    """
    if not values:
        return "0 m AMSL"
    lo, hi = round(min(values)), round(max(values))
    return f"{lo} m AMSL" if abs(hi - lo) <= 5 else f"~{lo}-{hi} m AMSL"


def add_3d_volume_to_multigeometry(
    multi_geo: ET.Element,
    ring: list[tuple[float, float]],
    floor_amsls: list[float],
    ceil_amsls: list[float],
    draw_floor_face: bool,
) -> None:
    """Append closed 3D faces (floor, ceiling, and side walls) to a KML MultiGeometry element.

    Constructs 3D polygon elements with absolute altitude mode using per-vertex
    altitudes for floor and ceiling, and vertical 4-point wall polygons connecting
    each segment.

    Args:
        multi_geo: KML MultiGeometry XML element to append faces into.
        ring: Closed list of (lon, lat) coordinate tuples.
        floor_amsls: Floor altitude in meters AMSL for each vertex in ring.
        ceil_amsls: Ceiling altitude in meters AMSL for each vertex in ring.
        draw_floor_face: Whether to generate the bottom floor polygon (omitted
            when floor is at ground surface / SFC).
    """
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


def _build_reference_folder(
    doc: ET.Element,
    center_lat: float,
    center_lon: float,
    location_label: str,
    radius_km: float,
    max_altitude_m: float,
    center_elev: float,
) -> None:
    """Append the central marker Placemark and radius perimeter circle to the KML Document.

    Args:
        doc: KML Document XML element.
        center_lat: Center latitude in decimal degrees.
        center_lon: Center longitude in decimal degrees.
        location_label: Human-readable name or label of the center location.
        radius_km: Search radius in kilometers.
        max_altitude_m: Maximum visual ceiling altitude in meters AMSL.
        center_elev: Terrain elevation at the center point in meters AMSL.
    """
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
    """Resolve per-vertex AMSL altitudes for one polygon ring.

    Optionally densifies the ring geometry when following terrain, resolves DEM
    elevations, computes floor and ceiling AMSL altitudes, and applies visual ceiling caps.

    Args:
        raw_ring: List of (lon, lat) coordinates of the ring.
        floor_val_m: Nominal floor vertical limit in meters.
        floor_is_agl: True if floor is defined relative to ground (AGL).
        ceil_val_m: Nominal ceiling vertical limit in meters.
        ceil_is_agl: True if ceiling is defined relative to ground (AGL).
        max_altitude_m: Maximum visual ceiling altitude cap in meters AMSL.
        elevation_resolver: ElevationResolver instance for DEM lookups.
        needs_terrain: True if terrain elevation lookups are needed for AGL limits.

    Returns:
        A tuple of (densified_ring, floor_amsls, visual_ceil_amsls, raw_ceil_amsls),
        or None if the volume is entirely above max_altitude_m or invalid.
    """
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
    """Resolve terrain and AMSL altitudes for all polygon rings of an airspace.

    Args:
        raw_rings: List of polygon rings, each being a list of (lon, lat) tuples.
        floor_val_m: Nominal floor vertical limit in meters.
        floor_is_agl: True if floor limit is AGL.
        ceil_val_m: Nominal ceiling vertical limit in meters.
        ceil_is_agl: True if ceiling limit is AGL.
        max_altitude_m: Maximum visual ceiling altitude cap in meters AMSL.
        elevation_resolver: ElevationResolver instance for DEM lookups.

    Returns:
        A tuple of:
            - resolved_rings: List of (ring, floor_amsls, visual_ceil_amsls) tuples.
            - all_floors: Aggregated list of floor AMSL altitudes across all vertices.
            - all_ceils: Aggregated list of raw (uncapped) ceiling AMSL altitudes across all vertices.
    """
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
    """Append a KML Placemark with 3D geometry for one airspace into its category folder.

    Generates HTML metadata description and appends 3D volume geometries (floor, ceiling, walls)
    into the Placemark's MultiGeometry element.

    Args:
        folder: Target category KML Folder XML element.
        asp: OpenAIP airspace dictionary.
        category: Color category string ('RED', 'ORANGE', 'YELLOW', 'BLUE').
        icao_class: ICAO airspace class code (e.g. 'CTR', 'D', 'E', 'SUA').
        floor_val_m: Nominal floor vertical limit in meters.
        floor_label: Formatted floor label string (e.g. 'SFC', '1500 ft AGL').
        floor_is_agl: True if floor is relative to ground level.
        ceil_label: Formatted ceiling label string (e.g. 'FL 095', '3500 ft AMSL').
        max_altitude_m: Visual ceiling cap in meters AMSL.
        all_floors_amsl: List of all floor altitudes in meters AMSL for metadata formatting.
        all_raw_ceils_amsl: List of all ceiling altitudes in meters AMSL for metadata formatting.
        resolved_rings_data: List of resolved ring geometries with (ring, floor_amsls, visual_ceil_amsls).
    """
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
    """Build and write the 3D KML document for paragliding airspace visualization.

    Filters, categorizes, and constructs 3D airspace volumes with color styles,
    reference markers, and metadata descriptions, and writes the resulting XML to disk.

    Args:
        airspaces: List of OpenAIP airspace dictionaries.
        center_lat: Center latitude in decimal degrees.
        center_lon: Center longitude in decimal degrees.
        location_label: Human-readable name or label of the center location.
        radius_km: Search radius in kilometers.
        max_altitude_m: Maximum visual altitude cap in meters AMSL.
        excluded_classes: Set of uppercase ICAO class codes to omit (e.g. {'E', 'G'}).
        elevation_resolver: ElevationResolver instance for DEM terrain lookup.
        output_path: File system path where the KML file should be written.
        include_other: Whether to include airspaces with type 'OTHER'. Defaults to False.
        excluded_types: Optional set of uppercase airspace types to omit. Defaults to None.

    Returns:
        Total number of 3D airspace volumes exported into the KML file.
    """
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
