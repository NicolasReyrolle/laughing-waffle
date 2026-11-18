"""Terrain elevation resolution via Open-Meteo DEM API with batching and caching."""

import sys
from typing import Any

from airspace.constants import OPEN_METEO_ELEVATION_URL, requests

__all__ = ["ElevationResolver"]


class ElevationResolver:
    """Batch terrain elevation resolver using Open-Meteo DEM API with in-memory caching."""

    BATCH_SIZE = 100

    def __init__(self, enabled: bool = True) -> None:
        """Initialize the elevation resolver.

        Args:
            enabled: Whether DEM elevation lookups are enabled. If False,
                elevation lookups will default to 0.0 m without network requests.
        """
        self.enabled = enabled
        self._cache: dict[tuple[float, float], float] = {}
        self._warned = False

    def _fetch_elevation_chunk(self, chunk: list[tuple[float, float]]) -> None:
        """Fetch elevations for one batch from Open-Meteo DEM API and populate the cache.

        Args:
            chunk: List of (lat, lon) coordinate tuples to query.
        """
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
            data: Any = resp.json()
            elevations = data.get("elevation", []) if isinstance(data, dict) else []
            if not isinstance(elevations, list) or len(elevations) != len(chunk):
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
        """Return terrain elevations in meters AMSL for a list of (lon, lat) coordinates.

        Args:
            coords: List of (lon, lat) coordinate pairs in decimal degrees.

        Returns:
            List of terrain elevations in meters above mean sea level (AMSL) corresponding
            to each coordinate pair. Returns 0.0 for all coordinates if resolver is disabled
            or if elevation lookup fails.
        """
        if not coords or not self.enabled:
            return [0.0] * len(coords)

        # Round to 4 decimals (~11 m grid) to maximize cache hits
        keys = [(round(lat, 4), round(lon, 4)) for lon, lat in coords]
        missing = list(dict.fromkeys(k for k in keys if k not in self._cache))

        for start in range(0, len(missing), self.BATCH_SIZE):
            self._fetch_elevation_chunk(missing[start : start + self.BATCH_SIZE])

        return [self._cache[k] for k in keys]

    def get_single(self, lon: float, lat: float) -> float:
        """Return terrain elevation in meters AMSL for a single (lon, lat) coordinate.

        Args:
            lon: Longitude in decimal degrees.
            lat: Latitude in decimal degrees.

        Returns:
            Terrain elevation in meters above mean sea level (AMSL).
        """
        return self.get_elevations([(lon, lat)])[0]
