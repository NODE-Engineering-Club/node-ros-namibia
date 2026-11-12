"""An idle sonar is not a format problem, and /diagnostics must not say it is.

The real unit sends status frames and no point data until it is told to ping
(measured 2026-11-06).
"""

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("asket_interfaces.msg")

from omniscan_bridge.omniscan_bridge_node import OmniscanBridge  # noqa: E402


@pytest.fixture
def node():
    # A closed local port: nothing here can reach a real sonar.
    rclpy.init(args=[
        "--ros-args",
        "-p", "host:=127.0.0.1",
        "-p", "port:=9",
        "-p", "setup_path:=none",
        "-p", "use_vessel_attitude:=false",
    ])
    bridge = OmniscanBridge()
    bridge.transport._sock = object()  # link_up: a socket is open
    try:
        yield bridge
    finally:
        bridge.transport._sock = None
        bridge.destroy_node()
        rclpy.shutdown()


def message(bridge):
    return bridge._diagnostics(bridge._current_health()).status[0].message


def test_status_frames_with_no_point_data_read_as_idle(node):
    node.transport.stats.bytes_received = 30
    node.parser.stats.frames_parsed = 3
    text = message(node)
    assert "idle" in text
    assert "start_pinging" in text
    assert "none parsed" not in text


def test_bytes_that_do_not_parse_are_still_a_different_message(node):
    node.transport.stats.bytes_received = 500
    text = message(node)
    assert "none parsed" in text
    assert "idle" not in text
