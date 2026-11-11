"""The real boat draws the geofence Nav2 is fenced in by.

`RosSource` answers the `plan` stream from the same lat/lon file the keep-out
mask is built from (`/data/setup/geofence.yaml`), so the line on the map is the
line the planner respects. A missing or broken file must give an empty fence
and a reason, never an exception: the map then has no fence, and the
pre-flight's `nav.geofence` check says why.
"""

import os

from gui_backend.core.ros_source import _GeofenceFile

FENCE = """\
geofence:
  name: "Block A"
  polygon:
    - [-22.9500, 14.4800]
    - [-22.9500, 14.4900]
    - [-22.9580, 14.4900]
    - [-22.9580, 14.4800]
"""


def _fresh(fence):
    fence._checked = float("-inf")
    return fence.payload()


def test_polygon_is_sent_as_lon_lat(tmp_path):
    path = tmp_path / "geofence.yaml"
    path.write_text(FENCE)
    payload = _fresh(_GeofenceFile(str(path)))
    assert payload["geofence"][0] == [14.48, -22.95]
    assert len(payload["geofence"]) == 4
    assert payload["geofence_name"] == "Block A"
    assert payload["geofence_error"] == ""
    # The real boat has no survey lines: none are invented.
    assert payload["lines"] == []


def test_missing_file_is_an_empty_fence_with_a_reason(tmp_path):
    payload = _fresh(_GeofenceFile(str(tmp_path / "nope.yaml")))
    assert payload["geofence"] == []
    assert "does not exist" in payload["geofence_error"]


def test_no_path_configured():
    payload = _GeofenceFile("").payload()
    assert payload["geofence"] == []
    assert "no geofence file" in payload["geofence_error"]


def test_broken_file_says_why(tmp_path):
    path = tmp_path / "geofence.yaml"
    path.write_text("geofence:\n  polygon: [[1, 2]]\n")
    payload = _fresh(_GeofenceFile(str(path)))
    assert payload["geofence"] == []
    assert "three" in payload["geofence_error"]


def test_edited_file_is_picked_up(tmp_path):
    path = tmp_path / "geofence.yaml"
    path.write_text(FENCE)
    fence = _GeofenceFile(str(path))
    assert len(_fresh(fence)["geofence"]) == 4
    path.write_text(FENCE.replace('    - [-22.9580, 14.4800]\n', ''))
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert len(_fresh(fence)["geofence"]) == 3


def test_payload_is_stable_between_reads(tmp_path):
    """`plan` is sent on change only, by hashing the payload: a payload that
    changed on every read would be re-sent on every tick."""
    path = tmp_path / "geofence.yaml"
    path.write_text(FENCE)
    fence = _GeofenceFile(str(path))
    assert repr(_fresh(fence)) == repr(_fresh(fence))


def test_signed_distance_is_positive_inside_and_negative_outside():
    from asket_common.geofence import signed_distance_m

    # About 890 m east-west by 890 m north-south.
    square = [(-22.950, 14.480), (-22.950, 14.4887), (-22.958, 14.4887), (-22.958, 14.480)]
    centre = signed_distance_m(square, -22.954, 14.48435)
    assert 400 < centre < 460
    # 0.0002 deg of latitude is about 22 m.
    assert 15 < signed_distance_m(square, -22.9502, 14.48435) < 30
    assert -30 < signed_distance_m(square, -22.9498, 14.48435) < -15


def test_ros_source_feeds_the_fence_and_the_disk_into_the_alarms(tmp_path):
    from gui_backend.core.ros_source import RosSource

    path = tmp_path / "geofence.yaml"
    path.write_text(FENCE)
    source = object.__new__(RosSource)
    source.config = {}
    source.latest = {}
    source._geofence = _GeofenceFile(str(path))
    source._geofence._checked = float("-inf")
    assert source._geofence_distance_m({"lat": -22.954, "lon": 14.485}) > 0
    assert source._geofence_distance_m({"lat": -22.940, "lon": 14.485}) < 0
    assert source._geofence_distance_m({}) is None

    source.state = lambda: {
        "power": {}, "mission": {"state": "RECORDING", "disk_free_bytes": 1024},
        "sonar": {}, "heading": {}, "vessel": {"lat": -22.954, "lon": 14.485}, "pico": {},
    }
    source._msg = lambda name: None
    source._pico_state_facts = lambda: {}
    state = source.alarm_state()
    assert state["disk_free_bytes"] == 1024
    assert state["geofence_distance_m"] > 0
