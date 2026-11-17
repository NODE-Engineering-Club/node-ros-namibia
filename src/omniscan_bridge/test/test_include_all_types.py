"""The bench view's point filter, and that the boat's default did not change."""

from omniscan_bridge.core.geometry import SonarMounting, point_set_to_vessel_frame
from omniscan_bridge.core.ping_protocol import (
    PT_TYPE_BOTTOM,
    PT_TYPE_UNCLASSIFIED,
    PT_TYPE_WATER_COLUMN,
    Point,
    PointSet,
)

PING = PointSet(
    1,
    speed_of_sound=1500.0,
    utc_msec=0,
    points=[
        Point(0.0, 0.02, 1.0, PT_TYPE_BOTTOM),
        Point(0.0, 0.01, 2.0, PT_TYPE_WATER_COLUMN),
        Point(0.0, 0.03, 3.0, PT_TYPE_UNCLASSIFIED),
        Point(0.0, 0.0, 4.0, PT_TYPE_UNCLASSIFIED),  # no detection
    ],
)


def powers(**kwargs):
    return [p[3] for p in point_set_to_vessel_frame(PING, SonarMounting(), **kwargs)]


def test_the_default_keeps_seabed_detections_only():
    assert powers() == [1.0]


def test_all_types_keeps_everything_with_a_detection():
    assert powers(include_all_types=True) == [1.0, 2.0, 3.0]
