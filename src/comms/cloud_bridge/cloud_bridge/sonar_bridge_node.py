#!/usr/bin/env python3
"""SonarLink WebSocket client + peak-hold decimation (design note §3.4,
KELP_MISSIONS.md §5.1). This did not exist anywhere in the design note's
own code — its `cloud_telemetry_bridge_node` declared a `latest_sonar`
dict and never subscribed to anything that would update it, so the
dashboard's live waterfall would have rendered the same frozen placeholder
forever. This node is the missing piece: it connects to the Omniscan's
SonarLink listen-only WebSocket, decimates each ping, and publishes the
small result on `/sonar/decimated` for cloud_telemetry_node to forward.

The actual frame/payload parsing lives in sonar_protocol.py (kept free of
ROS imports, see that module's own honesty note on what is and isn't
verified about the Cerulean wire format); this file is purely the ROS/
WebSocket I/O around it.
"""
import asyncio
import json
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from cloud_bridge.geo_utils import peak_hold_decimate
from cloud_bridge.sonar_protocol import MSG_ID_OS_MONO_PROFILE, parse_os_mono_profile, parse_ping_protocol_frame

try:
    import websockets
    _HAS_WEBSOCKETS = True
except ImportError as _ws_import_exc:  # noqa: BLE001
    _HAS_WEBSOCKETS = False
    _WS_IMPORT_ERROR = _ws_import_exc


class SonarBridgeNode(Node):
    def __init__(self):
        super().__init__("sonar_bridge_node")

        if not _HAS_WEBSOCKETS:
            raise RuntimeError(
                f"sonar_bridge_node requires the 'websockets' pip package, "
                f"which failed to import ({_WS_IMPORT_ERROR})."
            )

        self.declare_parameter("sonarlink_ws_url", "ws://127.0.0.1:7077/connect_ws?session_id=1")
        self.declare_parameter("n_bins", 100)
        self.declare_parameter("decimate_to_pps", 5.0)

        self._ws_url = self.get_parameter("sonarlink_ws_url").get_parameter_value().string_value
        self._n_bins = self.get_parameter("n_bins").value
        self._min_period = 1.0 / self.get_parameter("decimate_to_pps").get_parameter_value().double_value
        self._last_publish_t = 0.0

        self.pub = self.create_publisher(String, "/sonar/decimated", 10)

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.thread.start()

        self.get_logger().info(f"sonar_bridge_node connecting to {self._ws_url}")

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._worker())

    async def _worker(self):
        backoff = 2.0
        while rclpy.ok():
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self.get_logger().info("SonarLink WebSocket connected.")
                    backoff = 2.0
                    async for message in ws:
                        self._handle_frame(message)
            except Exception as e:  # noqa: BLE001 — keep retrying regardless of cause
                self.get_logger().warn(
                    f"SonarLink connection lost or unavailable: {e}. Retrying in {backoff:.1f}s...",
                    throttle_duration_sec=10.0,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    def _handle_frame(self, message):
        if not isinstance(message, (bytes, bytearray)):
            return  # SonarLink's listen-only feed is raw binary Cerulean packets, not text
        now = self.get_clock().now().nanoseconds / 1e9
        if now - self._last_publish_t < self._min_period:
            return  # decimate ping rate before doing any parsing work

        parsed = parse_ping_protocol_frame(bytes(message))
        if parsed is None:
            return
        message_id, payload = parsed
        if message_id != MSG_ID_OS_MONO_PROFILE:
            return

        profile = parse_os_mono_profile(payload)
        if profile is None:
            return

        decimated = peak_hold_decimate(profile["samples"], n_bins=self._n_bins)
        out = {
            "ping_id": profile["ping_id"],
            "range_m": profile["range_m"],
            "gain_db": profile["gain_db"],
            # Port/starboard are two physically separate transducers in the
            # real BOM (two JST connections, two session_ids) — this node
            # handles one SonarLink session; run a second instance with a
            # different sonarlink_ws_url for the other channel, same as
            # Omniscan 450 SS (Port) / (Stbd) in the IP table.
            "port_intensity_u8": decimated,
            "stbd_intensity_u8": [0] * self._n_bins,
        }
        self.pub.publish(String(data=json.dumps(out)))
        self._last_publish_t = now


def main(args=None):
    rclpy.init(args=args)
    node = SonarBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
