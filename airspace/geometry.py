"""Geometric and mathematical operations on WGS84 coordinates and polygons."""

import math
from typing import Any

__all__ = [
    "densify_ring",
    "extract_polygon_rings",
    "generate_circle_coords",
    "haversine_km",
    "is_airspace_in_radius",
    "point_in_polygon",
]


def point_in_polygon(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """Test if a coordinate point is inside a polygon ring using ray-casting.

    Args:
        lon: Longitude of the target point in decimal degrees.
        lat: Latitude of the target point in decimal degrees.
        ring: List of (lon, lat) coordinates forming the polygon perimeter.

    Returns:
        True if the point lies inside the polygon ring, False otherwise.
    """
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
    """Check if an airspace intersects a circle defined by center and radius.

    Evaluates whether any polygon vertex falls within the radius distance,
    or if the center point itself is located inside any polygon ring.

    Args:
        asp: OpenAIP airspace dictionary containing a GeoJSON geometry.
        center_lat: Center latitude in decimal degrees.
        center_lon: Center longitude in decimal degrees.
        radius_km: Search radius in kilometers.

    Returns:
        True if the airspace is within the specified radius or encloses the center,
        False otherwise.
    """
    rings = extract_polygon_rings(asp.get("geometry", {}))
    for ring in rings:
        for lon, lat in ring:
            if haversine_km(center_lon, center_lat, lon, lat) <= radius_km:
                return True
        if point_in_polygon(center_lon, center_lat, ring):
            return True
    return False


def extract_polygon_rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Extract outer coordinate rings from a GeoJSON Polygon or MultiPolygon.

    Args:
        geometry: GeoJSON geometry dictionary containing 'type' and 'coordinates'.

    Returns:
        List of rings, where each ring is a list of (lon, lat) coordinate tuples.
        Returns an empty list if geometry is empty or unsupported.
    """
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
    """Calculate great-circle distance in kilometers between two WGS84 coordinates.

    Args:
        lon1: Longitude of the first point in decimal degrees.
        lat1: Latitude of the first point in decimal degrees.
        lon2: Longitude of the second point in decimal degrees.
        lat2: Latitude of the second point in decimal degrees.

    Returns:
        Great-circle distance between the two points in kilometers.
    """
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
    """Subdivide long polygon segments so terrain-following AGL surfaces hug relief.

    Args:
        ring: List of (lon, lat) coordinate tuples representing a polygon ring.
        max_segment_km: Maximum allowed distance in kilometers between consecutive
            vertices before interpolation. Defaults to 1.5 km.

    Returns:
        Densified list of (lon, lat) coordinate tuples with closed start and end points.
    """
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
    """Generate a closed geographic circle ring around a center coordinate.

    Args:
        lat: Center latitude in decimal degrees.
        lon: Center longitude in decimal degrees.
        radius_km: Radius of the circle in kilometers.
        num_points: Number of perimeter segments to generate. Defaults to 72.

    Returns:
        List of (lon, lat) coordinate tuples forming a closed ring (num_points + 1 points).
    """
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
