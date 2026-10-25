#!/usr/bin/env python3
"""Synthetic ROS2 publisher exercising cloud_telemetry_node's REAL
subscription/decimation/packaging code against fake sensor messages — the
ROS-message-layer equivalent of scratchpad/cloud_test/dummy_asv_publisher.py
(which only exercised the downstream JSON-over-WebSocket protocol).

Publishes on every topic cloud_telemetry_node subscribes to, so that node
can run completely unmodified and produce real output with zero hardware,
zero Jetson, zero MAVROS/Pixhawk connected. This is the first thing to run
when validating cloud_bridge against a real ROS2 install — see
docs/session-notes/2026-10-25-cloud-telemetry-dummy-test.md for the full
walkthrough and what this does and doesn't prove.

Not run here: this repo's dev machine has no ROS2 install (confirmed
repeatedly this project). Syntax-checked (py_compile) and reviewed against
cloud_telemetry_node.py's exact subscriptions, not executed.
"""
import json
import math
import time

import numpy as np
import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import TwistStamped
from mavros_msgs.msg import GPSRAW
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, Image, NavSatFix
from std_msgs.msg import String
from vision_msgs.msg import Detection3D, Detection3DArray, ObjectHypothesisWithPose

try:
    import cv2
except ImportError:
    cv2 = None

LAT0, LON0 = -26.6385, 15.1528  # Shearwater Bay — same anchor as dummy_asv_publisher.py


class FakeSensorsNode(Node):
    def __init__(self):
        super().__init__("fake_sensors_node")
        self.bridge = CvBridge()
        self.t0 = time.time()

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1,
        )

        # Matches cloud_telemetry_node.py's exact subscription list and QoS —
        # BEST_EFFORT for the five "sensor-like" topics, default (RELIABLE)
        # for /obstacles/fused_tracked and /battery/state.
        self.pub_gps = self.create_publisher(NavSatFix, "/gps_driver/gps_raw", sensor_qos)
        self.pub_odom = self.create_publisher(Odometry, "/odometry/filtered", sensor_qos)
        self.pub_vel = self.create_publisher(TwistStamped, "/mavros/global_position/raw/gps_vel", sensor_qos)
        self.pub_gpsraw = self.create_publisher(GPSRAW, "/mavros/gpsstatus/gps1/raw", sensor_qos)
        self.pub_lidar = self.create_publisher(Detection3DArray, "/obstacles/fused_tracked", 10)
        self.pub_mask = self.create_publisher(Image, "/perception/sam2_masks", sensor_qos)
        self.pub_cam = self.create_publisher(Image, "/camera/front/image_raw", sensor_qos)
        self.pub_batt = self.create_publisher(BatteryState, "/battery/state", 10)
        self.pub_sonar = self.create_publisher(String, "/sonar/decimated", sensor_qos)

        # Publish nav fast (this gates cloud_telemetry_node.dispatch_packet
        # entirely — nothing is sent at all until /gps_driver/gps_raw and
        # /odometry/filtered have both arrived at least once), everything
        # else at a believable independent rate, same as the real sensors
        # would.
        self.create_timer(0.2, self._publish_nav)       # 5 Hz: gps, odom, vel
        self.create_timer(1.0, self._publish_gpsraw)     # 1 Hz: RTK status
        self.create_timer(0.5, self._publish_lidar)      # 2 Hz: tracked hazards
        self.create_timer(0.5, self._publish_mask)       # 2 Hz: SAM2 mask
        self.create_timer(0.2, self._publish_camera)     # 5 Hz: camera frame
        self.create_timer(1.0, self._publish_battery)    # 1 Hz: battery
        self.create_timer(0.2, self._publish_sonar)      # 5 Hz: sonar

        self.get_logger().info(
            "fake_sensors_node publishing synthetic data on every topic "
            "cloud_telemetry_node subscribes to. Launch that node "
            "separately: ros2 run cloud_bridge cloud_telemetry_node "
            "--ros-args -p cloud_ws_url:=\"<your relay URL>?role=asv\""
        )

    def _t(self):
        return time.time() - self.t0

    def _publish_nav(self):
        t = self._t()
        angle = (t * 0.05) % (2 * math.pi)
        radius_deg = 0.0015
        lat = LAT0 + radius_deg * math.sin(angle)
        lon = LON0 + radius_deg * math.cos(angle)
        heading_rad = angle + math.pi / 2

        fix = NavSatFix()
        fix.header.stamp = self.get_clock().now().to_msg()
        fix.header.frame_id = "gps"
        fix.latitude, fix.longitude, fix.altitude = lat, lon, 0.4
        self.pub_gps.publish(fix)

        odom = Odometry()
        odom.header.stamp = fix.header.stamp
        odom.header.frame_id = "map"
        # Small map-frame circle so /obstacles/fused_tracked's map-frame
        # offsets have something non-trivial to subtract against — see
        # cloud_telemetry_node._lidar_cb's "offset FROM THE BOAT'S OWN
        # current map position" comment for why this must be populated.
        odom.pose.pose.position.x = 50.0 * math.cos(angle)
        odom.pose.pose.position.y = 50.0 * math.sin(angle)
        odom.pose.pose.orientation.z = math.sin(heading_rad / 2.0)
        odom.pose.pose.orientation.w = math.cos(heading_rad / 2.0)
        self.pub_odom.publish(odom)

        vel = TwistStamped()
        vel.header.stamp = fix.header.stamp
        vel.twist.linear.x = 1.2 * math.cos(heading_rad)  # ENU east
        vel.twist.linear.y = 1.2 * math.sin(heading_rad)  # ENU north
        self.pub_vel.publish(vel)

    def _publish_gpsraw(self):
        msg = GPSRAW()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.fix_type = 6  # RTK_FIXED — see geo_utils.map_rtk_mode()
        msg.satellites_visible = 14
        self.pub_gpsraw.publish(msg)

    def _publish_lidar(self):
        t = self._t()
        wobble = math.sin(t * 0.3) * 2.0
        arr = Detection3DArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        arr.header.frame_id = "map"  # must match cloud_telemetry_node's map_frame param

        for det_id, class_id, x, y, sx, sy in [
            (101, "KELP_CANOPY", 12.4, -2.1 + wobble, 3.2, 2.8),
            (102, "BUOY_OBSTACLE", 24.8, 4.3 - wobble, 0.5, 0.5),
        ]:
            det = Detection3D()
            det.header = arr.header
            det.bbox.center.position.x = x
            det.bbox.center.position.y = y
            det.bbox.size.x = sx
            det.bbox.size.y = sy
            det.bbox.size.z = 0.3
            hyp = ObjectHypothesisWithPose()
            # "<CLASS>:<id>" — geo_fusion_tracker_node.cpp's real track-id
            # convention (see that file's comment); cloud_telemetry_node
            # parses this with geo_utils.parse_class_and_id().
            hyp.hypothesis.class_id = f"{class_id}:{det_id}"
            hyp.hypothesis.score = 0.9
            det.results.append(hyp)
            arr.detections.append(det)

        self.pub_lidar.publish(arr)

    def _publish_mask(self):
        # mono8, values 0=water 1=buoy/rigid 2=kelp — same convention as
        # asket_sensor_fusion/sensor_fusion_obstacle_node.cpp's combined_mask.
        mask = np.zeros((400, 640), dtype=np.uint8)
        mask[150:280, 150:350] = 2  # a kelp-canopy-shaped block
        mask[100:140, 450:490] = 1  # a buoy-shaped block
        msg = self.bridge.cv2_to_imgmsg(mask, encoding="mono8")
        msg.header.stamp = self.get_clock().now().to_msg()
        self.pub_mask.publish(msg)

    def _publish_camera(self):
        if cv2 is None:
            return
        t = self._t()
        img = np.zeros((400, 640, 3), dtype=np.uint8)
        img[:, :] = (120, 70, 20)
        cv2.rectangle(img, (150, 150), (350, 280), (40, 160, 90), -1)
        cv2.circle(img, (470, 120), 15, (0, 140, 255), -1)
        cv2.putText(img, f"FAKE SENSOR t={t:.1f}s", (10, 390), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        msg = self.bridge.cv2_to_imgmsg(img, encoding="bgr8")
        msg.header.stamp = self.get_clock().now().to_msg()
        self.pub_cam.publish(msg)

    def _publish_battery(self):
        t = self._t()
        msg = BatteryState()
        msg.voltage = 16.4 - (t % 600) * 0.001
        msg.percentage = max(0.2, 0.9 - (t % 600) * 0.0005)  # fraction, 0..1
        self.pub_batt.publish(msg)

    def _publish_sonar(self):
        t = self._t()
        n_bins = 100
        center = int((n_bins / 2) * (1 + math.sin(t * 0.4)))
        bins = [max(0, 255 - abs(i - center) * 28) if abs(i - center) < 8 else 0 for i in range(n_bins)]
        payload = {
            "ping_id": int(t * 5), "range_m": 30.0, "gain_db": 22.5,
            "port_intensity_u8": bins, "stbd_intensity_u8": bins[::-1],
        }
        self.pub_sonar.publish(String(data=json.dumps(payload)))


def main(args=None):
    rclpy.init(args=args)
    node = FakeSensorsNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
