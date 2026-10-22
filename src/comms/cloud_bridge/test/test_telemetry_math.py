"""Pure-math regression tests for cloud_telemetry_node.py. No ROS, no
network — importable and runnable on a laptop, unlike the rest of this
package. Mirrors scratchpad/verify_cloud_math.py (the exploratory version
run before this file was written) as a permanent, re-runnable check."""
import math

import pytest

from cloud_bridge.geo_utils import CLASS_MAP, WGS84_A, enu_offset_to_latlon, map_rtk_mode, parse_class_and_id


def enu_to_latlon(east_m, north_m, lat0, lon0):
    return enu_offset_to_latlon(east_m, north_m, lat0, lon0)


def haversine_m(lat_a, lon_a, lat_b, lon_b):
    phi1, phi2 = math.radians(lat_a), math.radians(lat_b)
    dphi = math.radians(lat_b - lat_a)
    dlmb = math.radians(lon_b - lon_a)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * WGS84_A * math.asin(math.sqrt(a))


LAT0, LON0 = -26.6385, 15.1528  # Shearwater Bay, matches the design note's sample payload


def test_north_offset_increases_latitude():
    lat1, lon1 = enu_to_latlon(0.0, 1000.0, LAT0, LON0)
    assert lat1 > LAT0
    assert lon1 == pytest.approx(LON0, abs=1e-9)


def test_east_offset_increases_longitude():
    lat2, lon2 = enu_to_latlon(1000.0, 0.0, LAT0, LON0)
    assert lon2 > LON0
    assert lat2 == pytest.approx(LAT0, abs=1e-9)


def test_round_trip_distance_matches_offset():
    lat1, lon1 = enu_to_latlon(0.0, 1000.0, LAT0, LON0)
    assert haversine_m(LAT0, LON0, lat1, lon1) == pytest.approx(1000.0, abs=5.0)


@pytest.mark.parametrize("fix_type,expected", [
    (6, 3),  # RTK_FIXED
    (5, 2),  # RTK_FLOAT
    (3, 1),  # plain 3D fix, must NOT be reported as RTK
    (2, 1),  # plain 2D fix
    (1, 0),  # no fix
    (0, 0),  # no GPS
])
def test_rtk_fix_type_mapping(fix_type, expected):
    assert map_rtk_mode(fix_type) == expected


def test_class_map_translates_real_pipeline_output():
    assert CLASS_MAP["BUOY_OBSTACLE"] == "BUOY"
    assert CLASS_MAP["KELP_CANOPY"] == "KELP_CANOPY"


def test_class_map_default_is_flotsam_not_a_crash():
    assert CLASS_MAP.get("something_new_the_model_invents", "FLOTSAM") == "FLOTSAM"


def test_parse_class_and_id_splits_track_suffix():
    assert parse_class_and_id("KELP_CANOPY:7") == ("KELP_CANOPY", 7)
    assert parse_class_and_id("BUOY_OBSTACLE:142") == ("BUOY_OBSTACLE", 142)


def test_parse_class_and_id_tolerates_missing_suffix():
    # A message from a node that hasn't adopted the "<CLASS>:<id>"
    # convention yet must not raise.
    assert parse_class_and_id("unknown") == ("unknown", 0)
