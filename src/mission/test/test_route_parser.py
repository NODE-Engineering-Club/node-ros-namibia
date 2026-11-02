"""Tests for mission/route_parser.py — pure logic, no ROS imports, so
(unlike this package's other tests) these actually run on a plain machine
with no ROS2 install at all.
"""
import json
import os
import tempfile

import pytest

from mission.route_parser import RouteParseError, parse_route_file


def _write(tmp_path, filename, content):
    path = os.path.join(tmp_path, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


@pytest.fixture
def tmp_path_str():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_wrapped_yaml_with_list_form_waypoints(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", """
name: "Survey lane 1"
waypoints:
  - [-26.6385, 15.1528]
  - [-26.6390, 15.1532]
""")
    name, waypoints = parse_route_file(path)
    assert name == "Survey lane 1"
    assert waypoints == [(-26.6385, 15.1528), (-26.639, 15.1532)]


def test_wrapped_yaml_with_dict_form_waypoints(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", """
waypoints:
  - {lat: -26.6385, lon: 15.1528}
  - {lat: -26.6390, lon: 15.1532}
""")
    name, waypoints = parse_route_file(path)
    assert name == "route.yaml"  # defaults to filename when "name" is absent
    assert waypoints == [(-26.6385, 15.1528), (-26.639, 15.1532)]


def test_bare_json_list(tmp_path_str):
    path = _write(tmp_path_str, "route.json", json.dumps([[-26.6385, 15.1528], [-26.6390, 15.1532]]))
    name, waypoints = parse_route_file(path)
    assert name == "route.json"
    assert waypoints == [(-26.6385, 15.1528), (-26.639, 15.1532)]


def test_mixed_list_and_dict_waypoints_in_one_file(tmp_path_str):
    path = _write(tmp_path_str, "route.json", json.dumps({
        "name": "mixed",
        "waypoints": [[-26.6385, 15.1528], {"lat": -26.6390, "lon": 15.1532}],
    }))
    name, waypoints = parse_route_file(path)
    assert waypoints == [(-26.6385, 15.1528), (-26.639, 15.1532)]


def test_unsupported_extension_raises(tmp_path_str):
    path = _write(tmp_path_str, "route.txt", "whatever")
    with pytest.raises(RouteParseError, match="unsupported extension"):
        parse_route_file(path)


def test_missing_waypoints_key_raises(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", "name: foo\n")
    with pytest.raises(RouteParseError, match="waypoints"):
        parse_route_file(path)


def test_empty_waypoints_list_raises(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", "waypoints: []\n")
    with pytest.raises(RouteParseError, match="non-empty"):
        parse_route_file(path)


def test_out_of_range_latitude_raises(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", "waypoints:\n  - [200.0, 15.0]\n")
    with pytest.raises(RouteParseError, match="out of range"):
        parse_route_file(path)


def test_out_of_range_longitude_raises(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", "waypoints:\n  - [10.0, 400.0]\n")
    with pytest.raises(RouteParseError, match="out of range"):
        parse_route_file(path)


def test_malformed_entry_type_raises_with_index(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", "waypoints:\n  - \"not a point\"\n  - [1.0, 2.0]\n")
    with pytest.raises(RouteParseError, match=r"waypoint\[0\]"):
        parse_route_file(path)


def test_top_level_scalar_raises(tmp_path_str):
    path = _write(tmp_path_str, "route.yaml", "just a string\n")
    with pytest.raises(RouteParseError, match="must be a list or an object"):
        parse_route_file(path)


def test_nonexistent_file_raises_oserror(tmp_path_str):
    with pytest.raises(OSError):
        parse_route_file(os.path.join(tmp_path_str, "does_not_exist.yaml"))
