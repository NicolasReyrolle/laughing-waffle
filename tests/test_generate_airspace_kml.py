"""Unit tests for generate_airspace_kml.py."""

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import generate_airspace_kml as gak

# ---------------------------------------------------------------------------
# 1. Math and Geometry Tests
# ---------------------------------------------------------------------------


class TestGeometry:
    def test_haversine_same_point(self) -> None:
        assert gak.haversine_km(6.0, 49.0, 6.0, 49.0) == 0.0

    def test_haversine_symmetry(self) -> None:
        d1 = gak.haversine_km(2.3522, 48.8566, 6.0817, 49.4422)
        d2 = gak.haversine_km(6.0817, 49.4422, 2.3522, 48.8566)
        assert pytest.approx(d1, rel=1e-6) == d2

    def test_haversine_known_distance(self) -> None:
        # 1 degree of latitude is roughly 111.19 km
        dist = gak.haversine_km(0.0, 0.0, 0.0, 1.0)
        assert pytest.approx(dist, rel=1e-2) == 111.19

    def test_point_in_polygon_inside(self) -> None:
        # Unit square: (0,0) to (10,10)
        ring = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
        assert gak.point_in_polygon(5.0, 5.0, ring) is True

    def test_point_in_polygon_outside(self) -> None:
        ring = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
        assert gak.point_in_polygon(15.0, 5.0, ring) is False
        assert gak.point_in_polygon(-1.0, 5.0, ring) is False

    def test_densify_ring_short_segment(self) -> None:
        # Segment shorter than max_segment_km should not add intermediate points
        ring = [(0.0, 0.0), (0.005, 0.0), (0.005, 0.005), (0.0, 0.0)]
        densified = gak.densify_ring(ring, max_segment_km=5.0)
        assert len(densified) == len(ring)

    def test_densify_ring_long_segment(self) -> None:
        # 1 degree of latitude ~ 111 km, with max_segment_km=10 -> ~12 points per segment
        ring = [(0.0, 0.0), (0.0, 1.0), (0.0, 0.0)]
        densified = gak.densify_ring(ring, max_segment_km=10.0)
        assert len(densified) > 10
        assert densified[0] == densified[-1]  # Must remain closed

    def test_densify_ring_small_input(self) -> None:
        assert gak.densify_ring([]) == []
        assert gak.densify_ring([(1.0, 2.0)]) == [(1.0, 2.0)]

    def test_generate_circle_coords(self) -> None:
        coords = gak.generate_circle_coords(49.0, 6.0, radius_km=10.0, num_points=36)
        assert len(coords) == 37  # 36 steps + 1 to close loop
        assert coords[0] == coords[-1]
        for lon, lat in coords:
            dist = gak.haversine_km(6.0, 49.0, lon, lat)
            assert pytest.approx(dist, rel=1e-2) == 10.0

    def test_extract_polygon_rings(self) -> None:
        # Test Polygon
        geom_poly = {
            "type": "Polygon",
            "coordinates": [[[1.0, 2.0], [3.0, 4.0], [1.0, 2.0]]],
        }
        rings = gak.extract_polygon_rings(geom_poly)
        assert len(rings) == 1
        assert rings[0] == [(1.0, 2.0), (3.0, 4.0), (1.0, 2.0)]

        # Test MultiPolygon
        geom_multi = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[1.0, 1.0], [2.0, 2.0], [1.0, 1.0]]],
                [[[3.0, 3.0], [4.0, 4.0], [3.0, 3.0]]],
            ],
        }
        rings_multi = gak.extract_polygon_rings(geom_multi)
        assert len(rings_multi) == 2

        # Test Unknown
        assert gak.extract_polygon_rings({"type": "Point", "coordinates": [0, 0]}) == []

    def test_is_airspace_in_radius_vertex_inside(self) -> None:
        asp = {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[6.05, 49.45], [6.10, 49.45], [6.05, 49.45]]],
            }
        }
        # Point is within ~2 km of (49.4422, 6.0816)
        assert gak.is_airspace_in_radius(asp, 49.4422, 6.0816, radius_km=10.0) is True

    def test_is_airspace_in_radius_center_inside_large_poly(self) -> None:
        # Center is inside square [0,0] to [10,10], even though vertices are > 100 km away
        asp = {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
                ],
            }
        }
        assert gak.is_airspace_in_radius(asp, 5.0, 5.0, radius_km=5.0) is True

    def test_is_airspace_in_radius_outside(self) -> None:
        asp = {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[10.0, 10.0], [11.0, 10.0], [10.0, 10.0]]],
            }
        }
        assert gak.is_airspace_in_radius(asp, 49.4422, 6.0816, radius_km=25.0) is False


# ---------------------------------------------------------------------------
# 2. Parsing and Formatting Tests
# ---------------------------------------------------------------------------


class TestParsingAndFormatting:
    def test_kml_color(self) -> None:
        # Full opacity (100% -> ff), hex #E53935 -> KML aabbggrr: ff3539e5
        assert gak.kml_color(100, "#E53935") == "ff3539e5"
        # Zero opacity (0% -> 00)
        assert gak.kml_color(0, "#FFFFFF") == "00ffffff"
        # Half opacity (50% -> 7f)
        assert gak.kml_color(50, "#1E88E5") == "7fe5881e"

    def test_format_amsl_range_single_value(self) -> None:
        assert gak.format_amsl_range([1000.0, 1002.0, 1004.0]) == "1000 m AMSL"

    def test_format_amsl_range_spread_values(self) -> None:
        assert gak.format_amsl_range([500.0, 800.0, 1200.0]) == "~500-1200 m AMSL"

    def test_format_amsl_range_empty(self) -> None:
        assert gak.format_amsl_range([]) == "0 m AMSL"

    def test_parse_vertical_limit_none(self) -> None:
        val, label, is_agl = gak.parse_vertical_limit(None)
        assert val == 0.0
        assert label == "SFC"
        assert is_agl is True

    def test_parse_vertical_limit_feet_msl(self) -> None:
        limit = {"value": 2500, "unit": 1, "referenceDatum": 1}
        val, label, is_agl = gak.parse_vertical_limit(limit)
        assert pytest.approx(val, rel=1e-3) == 762.0
        assert label == "2500 ft AMSL"
        assert is_agl is False

    def test_parse_vertical_limit_flight_level(self) -> None:
        limit = {"value": 165, "unit": 6, "referenceDatum": 2}
        val, label, is_agl = gak.parse_vertical_limit(limit)
        assert pytest.approx(val, rel=1e-3) == 5029.2
        assert label == "FL 165"
        assert is_agl is False

    def test_parse_vertical_limit_ground_sfc(self) -> None:
        limit = {"value": 0, "unit": 1, "referenceDatum": 0}
        val, label, is_agl = gak.parse_vertical_limit(limit)
        assert val == 0.0
        assert label == "SFC"
        assert is_agl is True


# ---------------------------------------------------------------------------
# 3. Airspace Classification & H24 Logic Tests
# ---------------------------------------------------------------------------


class TestClassification:
    def test_is_activatable_permanent_h24(self) -> None:
        # Full 7-day 00:00-00:00 schedule is permanent H24 -> NOT activatable
        hours = {
            "operatingHours": [
                {
                    "dayOfWeek": i,
                    "startTime": "00:00",
                    "endTime": "00:00",
                    "byNotam": False,
                }
                for i in range(7)
            ]
        }
        asp = {
            "hoursOfOperation": hours,
            "byNotam": False,
            "onDemand": False,
            "onRequest": False,
        }
        assert gak.is_activatable_airspace(asp) is False

    def test_is_activatable_restricted_days(self) -> None:
        # Operating MON-FRI only (e.g. 5 days) -> activatable
        hours = {
            "operatingHours": [
                {
                    "dayOfWeek": i,
                    "startTime": "00:00",
                    "endTime": "00:00",
                    "byNotam": False,
                }
                for i in range(5)
            ]
        }
        asp = {"hoursOfOperation": hours}
        assert gak.is_activatable_airspace(asp) is True

    def test_is_activatable_by_notam_field(self) -> None:
        asp = {"byNotam": True}
        assert gak.is_activatable_airspace(asp) is True

    def test_is_activatable_on_demand(self) -> None:
        asp = {"onDemand": True}
        assert gak.is_activatable_airspace(asp) is True

    def test_is_activatable_keywords_in_name(self) -> None:
        assert gak.is_activatable_airspace({"name": "TMA HX 1"}) is True
        assert gak.is_activatable_airspace({"remarks": "ACTIVATION BY NOTAM"}) is True
        assert gak.is_activatable_airspace({"name": "ZONE AZBA"}) is True

    def test_classify_airspace_excluded_top_level(self) -> None:
        # FIR, UIR, ACC, ADIZ should always be ignored
        assert gak.classify_airspace({"type": 10}) is None  # FIR
        assert gak.classify_airspace({"type": 11}) is None  # UIR
        assert gak.classify_airspace({"type": 27}) is None  # ACC
        assert gak.classify_airspace({"type": 12}) is None  # ADIZ

    def test_classify_airspace_other_default_excluded(self) -> None:
        # Type 0 (OTHER) is excluded by default
        asp = {"type": 0, "icaoClass": 3, "name": "SOME OTHER AIRSPACE"}
        assert gak.classify_airspace(asp, include_other=False) is None

    def test_classify_airspace_other_included_when_flagged(self) -> None:
        asp = {"type": 0, "icaoClass": 3, "name": "SOME OTHER AIRSPACE"}
        assert gak.classify_airspace(asp, include_other=True) == "RED"

    def test_classify_airspace_class_e(self) -> None:
        asp = {"type": 7, "icaoClass": 4}  # TMA Class E
        assert gak.classify_airspace(asp) == "BLUE"

    def test_classify_airspace_yellow_types(self) -> None:
        for t_code in (5, 6, 13, 21, 28):  # TMZ, RMZ, ATZ, GLIDING, SPORT
            asp = {"type": t_code, "icaoClass": 6}
            assert gak.classify_airspace(asp) == "YELLOW"

    def test_classify_airspace_orange_types(self) -> None:
        for t_code in (1, 2, 8, 9):  # R, D, TRA, TSA
            asp = {"type": t_code, "icaoClass": 8}
            assert gak.classify_airspace(asp) == "ORANGE"

    def test_classify_tma_permanent_is_red(self) -> None:
        # Permanent H24 Class D TMA (like TMA LUXEMBOURG 3)
        h24_hours = {
            "operatingHours": [
                {
                    "dayOfWeek": i,
                    "startTime": "00:00",
                    "endTime": "00:00",
                    "byNotam": False,
                }
                for i in range(7)
            ]
        }
        asp = {
            "type": 7,  # TMA
            "icaoClass": 3,  # Class D
            "hoursOfOperation": h24_hours,
            "name": "TMA LUXEMBOURG 3",
        }
        assert gak.classify_airspace(asp) == "RED"

    def test_classify_tma_activatable_is_orange(self) -> None:
        # Activatable Class D TMA
        asp = {
            "type": 7,  # TMA
            "icaoClass": 3,  # Class D
            "byNotam": True,
            "name": "TMA DEAUVILLE 1",
        }
        assert gak.classify_airspace(asp) == "ORANGE"

    def test_classify_ctr_permanent_is_red(self) -> None:
        # Permanent H24 Class D CTR (like LUXEMBOURG CTR)
        h24_hours = {
            "operatingHours": [
                {
                    "dayOfWeek": i,
                    "startTime": "00:00",
                    "endTime": "00:00",
                    "byNotam": False,
                }
                for i in range(7)
            ]
        }
        asp = {
            "type": 4,  # CTR
            "icaoClass": 3,  # Class D
            "hoursOfOperation": h24_hours,
            "name": "LUXEMBOURG CTR",
        }
        assert gak.classify_airspace(asp) == "RED"

    def test_classify_excluded_types(self) -> None:
        asp = {"type": 28, "icaoClass": 6, "name": "SPORT ZONE"}
        assert gak.classify_airspace(asp, excluded_types={"SPORT"}) is None


# ---------------------------------------------------------------------------
# 4. Dotenv & Location Parsing Tests
# ---------------------------------------------------------------------------


class TestDotenvAndLocation:
    def test_load_dotenv_file(self) -> None:
        with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as tmp:
            tmp.write("# Comment line\n")
            tmp.write("TEST_KML_KEY=secret_12345\n")
            tmp.write('TEST_QUOTED_KEY="hello_world"\n')
            tmp.write("INVALID_LINE_NO_EQUALS\n")
            tmp_path = tmp.name

        try:
            gak.load_dotenv_file(tmp_path)
            assert os.environ.get("TEST_KML_KEY") == "secret_12345"
            assert os.environ.get("TEST_QUOTED_KEY") == "hello_world"
        finally:
            os.environ.pop("TEST_KML_KEY", None)
            os.environ.pop("TEST_QUOTED_KEY", None)
            Path(tmp_path).unlink(missing_ok=True)

    def test_resolve_location_coordinates_string(self) -> None:
        lat, lon, label, _country = gak.resolve_location("49.4422, 6.0816")
        assert pytest.approx(lat, abs=1e-4) == 49.4422
        assert pytest.approx(lon, abs=1e-4) == 6.0816
        assert "49.4422" in label

    @patch("generate_airspace_kml.requests.get")
    def test_resolve_location_geocoding(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "lat": "48.8566",
                "lon": "2.3522",
                "display_name": "Paris, Ile-de-France, France",
                "address": {"country_code": "fr"},
            }
        ]
        mock_get.return_value = mock_response

        lat, lon, label, country = gak.resolve_location("Paris")
        assert pytest.approx(lat, abs=1e-3) == 48.8566
        assert pytest.approx(lon, abs=1e-3) == 2.3522
        assert label == "Paris"
        assert country == "FR"


# ---------------------------------------------------------------------------
# 5. ElevationResolver Tests
# ---------------------------------------------------------------------------


class TestElevationResolver:
    def test_disabled_resolver_returns_zero(self) -> None:
        resolver = gak.ElevationResolver(enabled=False)
        elevs = resolver.get_elevations([(6.0, 49.0), (6.1, 49.1)])
        assert elevs == [0.0, 0.0]
        assert resolver.get_single(6.0, 49.0) == 0.0

    @patch("generate_airspace_kml.requests.get")
    def test_enabled_resolver_batches_and_caches(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"elevation": [250.0, 310.0]}
        mock_get.return_value = mock_response

        resolver = gak.ElevationResolver(enabled=True)
        elevs = resolver.get_elevations([(6.0, 49.0), (6.1, 49.1)])
        assert elevs == [250.0, 310.0]
        assert mock_get.call_count == 1

        # Second call with same coordinates must hit memory cache (no HTTP call)
        cached_elevs = resolver.get_elevations([(6.0, 49.0), (6.1, 49.1)])
        assert cached_elevs == [250.0, 310.0]
        assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# 6. Build KML Integration Tests
# ---------------------------------------------------------------------------


class TestBuildKml:
    def test_build_kml_creates_valid_xml(self) -> None:
        h24_hours = {
            "operatingHours": [
                {
                    "dayOfWeek": i,
                    "startTime": "00:00",
                    "endTime": "00:00",
                    "byNotam": False,
                }
                for i in range(7)
            ]
        }
        sample_airspace = {
            "name": "TEST CTR",
            "type": 4,  # CTR
            "icaoClass": 3,  # D
            "hoursOfOperation": h24_hours,
            "lowerLimit": {"value": 0, "unit": 1, "referenceDatum": 0},
            "upperLimit": {"value": 2500, "unit": 1, "referenceDatum": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [6.0, 49.4],
                        [6.1, 49.4],
                        [6.1, 49.5],
                        [6.0, 49.5],
                        [6.0, 49.4],
                    ]
                ],
            },
        }

        with tempfile.NamedTemporaryFile("w+", suffix=".kml", delete=False) as tmp:
            output_kml = tmp.name

        try:
            resolver = gak.ElevationResolver(enabled=False)
            count = gak.build_kml(
                airspaces=[sample_airspace],
                center_lat=49.44,
                center_lon=6.08,
                location_label="Test Location",
                radius_km=25.0,
                max_altitude_m=2500.0,
                excluded_classes=set(),
                elevation_resolver=resolver,
                output_path=output_kml,
                include_other=False,
            )

            assert count == 1
            assert Path(output_kml).exists()

            # Parse generated KML to verify valid XML
            tree = ET.parse(output_kml)
            root = tree.getroot()
            assert root.tag.endswith("kml")

            # Check placemark exists and has style_RED
            xml_text = Path(output_kml).read_text(encoding="utf-8")
            assert "TEST CTR" in xml_text
            assert "#style_RED" in xml_text
            assert "Cercle Rayon (25 km)" in xml_text
        finally:
            Path(output_kml).unlink(missing_ok=True)

    def test_build_kml_filters_above_max_altitude(self) -> None:
        sample_high_airspace = {
            "name": "HIGH AIRSPACE",
            "type": 7,  # TMA
            "icaoClass": 3,
            "lowerLimit": {
                "value": 15000,
                "unit": 1,
                "referenceDatum": 1,
            },  # ~4572 m AMSL
            "upperLimit": {"value": 19500, "unit": 1, "referenceDatum": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[6.0, 49.4], [6.1, 49.4], [6.0, 49.4]]],
            },
        }

        with tempfile.NamedTemporaryFile("w+", suffix=".kml", delete=False) as tmp:
            output_kml = tmp.name

        try:
            resolver = gak.ElevationResolver(enabled=False)
            count = gak.build_kml(
                airspaces=[sample_high_airspace],
                center_lat=49.44,
                center_lon=6.08,
                location_label="Test Location",
                radius_km=25.0,
                max_altitude_m=3000.0,  # Below lower limit
                excluded_classes=set(),
                elevation_resolver=resolver,
                output_path=output_kml,
                include_other=False,
            )

            assert count == 0
            xml_text = Path(output_kml).read_text(encoding="utf-8")
            assert "HIGH AIRSPACE" not in xml_text
        finally:
            Path(output_kml).unlink(missing_ok=True)
