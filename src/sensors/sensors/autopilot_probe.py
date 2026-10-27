"""Which autopilot is actually on the other end of MAVROS.

Reads the flight controller's HEARTBEAT off MAVROS's raw stream
(/uas1/mavlink_source) and publishes the answer once a second on
/autopilot/detected (std_msgs/String): "ardupilot", "px4", "other:<n>", or
"" when no heartbeat has arrived for 3 s. One subscriber to the raw stream
(hundreds of messages a second), so the pre-flight and the GUI read a small
topic instead. Decoding: asket_common/autopilot.py.
"""

import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String
from mavros_msgs.msg import Mavlink

from asket_common.autopilot import from_mavlink

STALE_S = 3.0


class AutopilotProbe(Node):
    def __init__(self):
        super().__init__("autopilot_probe")
        self.pub = self.create_publisher(String, "/autopilot/detected", 10)
        self.create_subscription(Mavlink, "/uas1/mavlink_source", self._on_mavlink,
                                 qos_profile_sensor_data)
        self.detected = ""
        self.at = 0.0
        self.create_timer(1.0, self._publish)

    def _on_mavlink(self, msg: Mavlink) -> None:
        name = from_mavlink(msg.msgid, msg.compid, msg.payload64)
        if name is None:
            return
        if name != self.detected:
            self.get_logger().info(f"flight controller heartbeat: {name}")
        self.detected = name
        self.at = time.monotonic()

    def _publish(self) -> None:
        fresh = self.detected and time.monotonic() - self.at < STALE_S
        self.pub.publish(String(data=self.detected if fresh else ""))


def main(args=None):
    rclpy.init(args=args)
    node = AutopilotProbe()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
