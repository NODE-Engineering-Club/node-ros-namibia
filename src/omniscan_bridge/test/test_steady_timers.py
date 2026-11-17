"""The node's timers must not follow the system clock.

On the default ROS clock, a system clock step stalls them. With the 100 Hz drain
timer stopped, the receive queue overflows and sonar data is lost.
"""

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("asket_interfaces.msg")

from omniscan_bridge.omniscan_bridge_node import OmniscanBridge  # noqa: E402
from rclpy.clock import ClockType  # noqa: E402


@pytest.mark.parametrize("may_command, expected_timers", [("false", 2), ("true", 3)])
def test_every_timer_uses_the_steady_clock(may_command, expected_timers):
    # A closed local port: nothing here can reach a real sonar.
    rclpy.init(args=[
        "--ros-args",
        "-p", "host:=127.0.0.1",
        "-p", "port:=9",
        "-p", "setup_path:=none",
        "-p", "use_vessel_attitude:=false",
        "-p", f"send_ping_parameters:={may_command}",
    ])
    try:
        node = OmniscanBridge()
        timers = list(node.timers)
        node.destroy_node()
    finally:
        rclpy.shutdown()
    assert len(timers) == expected_timers
    assert all(t.clock.clock_type == ClockType.STEADY_TIME for t in timers)
