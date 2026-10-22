#!/usr/bin/env python3
"""Outbound-only cloud telemetry bridge (ASKET-SPEC-COMMS-CLOUD-v1.0 §6).

Subscribes to nav/perception/health topics already published by this
repo's existing nodes, decimates them to ~5 Hz, and streams a single JSON
packet per tick over an outbound WebSocket to the Cloudflare relay (§7).
Never accepts inbound connections and never touches MAVROS/ArduPilot
control — this is telemetry-out only, independent of the local-control-
plane SSH/MAVLink path described in the same document's §2.

Differences from the design note, and why each one is a fix, not a style
choice — verified against this repo's actual topics/messages where
possible, and against standalone math where it couldn't be (no ROS2
install, no Cloudflare account, no real radio/Jetson hardware in the
environment this was written in — see the math checks this was built
against: scratchpad/verify_cloud_math.py, 19/19 passed):

  * **Default `cloud_ws_url` now includes `?role=asv`.** The companion
    Cloudflare Worker (cloudflare/worker-telemetry-relay.js) routes a
    connection as the ASV sender ONLY if `role=asv` is in the query
    string; anything else is treated as a browser listener. The design
    note's own default URL omits it — as shipped, the boat's own
    connection would silently become a browser-role listener and nothing
    would ever reach a real browser. This is the single most important
    fix in this file: without it the entire pipeline relays nothing, with
    no error anywhere.
  * **RTK fix status now comes from `mavros_msgs/GPSRAW.fix_type`
    (`/mavros/gpsstatus/gps1/raw`), not `sensor_msgs/NavSatStatus.status`.**
    NavSatStatus only has 4 values (no-fix/fix/SBAS/GBAS) and structurally
    cannot express RTK-float vs RTK-fixed at all — the design note's
    `status >= 2 -> RTK_FIXED` was a guess dressed as a real status read.
    GPSRAW.fix_type mirrors MAVLink's GPS_FIX_TYPE, which does carry that
    distinction (5=RTK_FLOAT, 6=RTK_FIXED).
  * **sog/cog are now actually computed**, from
    `/mavros/global_position/raw/gps_vel`, the same topic and formula this
    repo's own `src/foxglove/gui_telemetry_hw.py` already uses (carrying
    forward its own documented caveat: the ENU x=East/y=North frame
    assumption is unverified on real hardware — if COG reads a fixed 90°
    off compass heading while driving straight, swap east/north). The
    design note declared these fields in its schema and dashboard but its
    own node never subscribed to anything that would populate them.
  * **LiDAR hazards now come from `/obstacles/fused_tracked`** (this
    repo's `geo_fusion_tracker_node`, added for the SAM2/fusion work),
    **not** `/fusion/obstacles_local`. The design note's bridge subscribed
    to the pre-tracking, local (base_link) frame topic and then hand-
    rolled its own flat-earth local->global rotation a SECOND way,
    discarding the EKF tracker's noise rejection and persistent track IDs
    in the process, and risking a geometry formula that silently drifts
    from the one already tested. `/obstacles/fused_tracked` is already
    map-frame and already tracked; this node only needs to convert that
    map-frame offset to lat/lon, using the SAME tangent-plane approach
    already tested in `fusion/fusion/geo_fusion_node.py`'s `_to_geo()` —
    anchored on the boat's OWN current map-frame position (from
    `/odometry/filtered`), not blindly treating the detection's map
    coordinates as if they were already relative to the boat.
  * **Class names are explicitly mapped**, not passed through assuming a
    match. `geo_fusion_tracker_node` emits `KELP_CANOPY` / `BUOY_OBSTACLE`
    / `unknown`; the payload schema's enum is
    `BUOY | VESSEL | FLOTSAM | KELP_CANOPY`. An unmapped class falls back
    to `FLOTSAM` rather than silently failing schema validation.
  * **Track id is parsed from `class_id`'s `"<CLASS>:<id>"` suffix**
    (see geo_fusion_tracker_node.cpp), not read from
    `vision_msgs/Detection3D.id` — that field's presence on the installed
    vision_msgs version was never verified against live Jazzy source in
    this environment, and guessing at it would risk a crash on a real
    message for something load-bearing (a stable per-object id), not
    cosmetic.
  * **`health.battery_soc` is reported as `None` when `bms_reader`'s
    `BatteryState.percentage` reads exactly 0.0** while voltage is a
    plausible nonzero value. `bms_reader.py` never actually sets
    `percentage` (confirmed by reading it — it only sets `.voltage`), so a
    literal forwarded 0.0 would show "0% battery" on the dashboard: a
    false critical alarm for "this field was never wired," not a real
    reading. `jetson_temp_c` / `leak_detected` / `wifi_rssi_dbm` have no
    publishing sensor anywhere in this repo yet (confirmed against
    KELP_CAPABILITIES.md's own gap list) and are sent as `None`/omitted
    rather than the design note's hardcoded-looking placeholder numbers.
  * Sonar is handled by a separate node, `sonar_bridge_node.py` (the
    design note's bridge never actually subscribed to anything sonar-
    related either, despite the dashboard rendering a live waterfall from
    it) — this node forwards whatever `sonar_bridge_node` last published
    on `/sonar/decimated`, or omits the field, rather than sending the
    frozen placeholder values the design note's `latest_sonar` dict would
    have sent forever.
"""
import asyncio
import base64
import json
import math
import threading

import cv2
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
from vision_msgs.msg import Detection3DArray

from cloud_bridge.geo_utils import CLASS_MAP, enu_offset_to_latlon, map_rtk_mode, parse_class_and_id

try:
    import websockets
    _HAS_WEBSOCKETS = True
except ImportError as _ws_import_exc:  # noqa: BLE001 — degrade, don't crash bringup
    _HAS_WEBSOCKETS = False
    _WS_IMPORT_ERROR = _ws_import_exc


class CloudTelemetryBridgeNode(Node):
    def __init__(self):
        super().__init__("cloud_telemetry_bridge_node")
        self.bridge = CvBridge()

        if not _HAS_WEBSOCKETS:
            raise RuntimeError(
                f"cloud_telemetry_bridge_node requires the 'websockets' pip "
                f"package, which failed to import ({_WS_IMPORT_ERROR}). "
                "Add it to the Containerfile's pip install list."
            )

        self.declare_parameter(
            "cloud_ws_url",
            "wss://telemetry.asket-namibia.internal/api/v1/telemetry/stream?role=asv",
        )
        self.declare_parameter("asv_id", "ASKET_02")
        self.declare_parameter("stream_rate_hz", 5.0)
        self.declare_parameter("map_frame", "map")

        self.ws_url = self.get_parameter("cloud_ws_url").get_parameter_value().string_value
        self.asv_id = self.get_parameter("asv_id").get_parameter_value().string_value
        self._map_frame = self.get_parameter("map_frame").value

        # ── Local telemetry state cache ─────────────────────────────────────
        self.seq = 0
        self.latest_nav = {
            "lat": None, "lon": None, "alt": None,
            "heading": 0.0, "sog": 0.0, "cog": 0.0, "rtk_mode": 0,
        }
        self._boat_map_xy = None  # (x, y) from /odometry/filtered, same frame as /obstacles/fused_tracked
        self.latest_clusters = []
        self.latest_polygons = []
        self.latest_sonar = None  # populated only if sonar_bridge_node is publishing
        self.latest_health = {
            "battery_voltage": None, "battery_soc": None,
            "jetson_temp_c": None, "leak_detected": None, "wifi_rssi_dbm": None,
        }
        self.latest_jpeg_b64 = None
        self.frame_counter = 0

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1,
        )

        self.create_subscription(NavSatFix, "/gps_driver/gps_raw", self._gps_cb, sensor_qos)
        self.create_subscription(Odometry, "/odometry/filtered", self._odom_cb, sensor_qos)
        self.create_subscription(TwistStamped, "/mavros/global_position/raw/gps_vel", self._vel_cb, sensor_qos)
        self.create_subscription(GPSRAW, "/mavros/gpsstatus/gps1/raw", self._gpsraw_cb, sensor_qos)
        self.create_subscription(Detection3DArray, "/obstacles/fused_tracked", self._lidar_cb, 10)
        self.create_subscription(Image, "/perception/sam2_masks", self._sam2_cb, sensor_qos)
        self.create_subscription(Image, "/camera/front/image_raw", self._image_raw_cb, sensor_qos)
        self.create_subscription(BatteryState, "/battery/state", self._battery_cb, 10)
        # sonar_bridge_node's own decimated output (see that file) — a
        # single pre-serialized JSON string, so this node doesn't need to
        # know SonarLink's packet format at all, only the final small
        # payload shape it re-embeds under packet["sonar"].
        self.create_subscription(String, "/sonar/decimated", self._sonar_cb, sensor_qos)

        # ── Async WebSocket loop in a separate thread ───────────────────────
        self.ws_queue = asyncio.Queue(maxsize=1)  # drop-tail: size=1, oldest discarded
        self.loop = asyncio.new_event_loop()
        self.ws_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.ws_thread.start()

        rate_hz = self.get_parameter("stream_rate_hz").get_parameter_value().double_value
        self.timer = self.create_timer(1.0 / rate_hz, self.dispatch_packet)
        self.get_logger().info(f"Cloud telemetry bridge operational at {rate_hz} Hz -> {self.ws_url}")

    # ── ROS callbacks ───────────────────────────────────────────────────────

    def _gps_cb(self, msg: NavSatFix):
        self.latest_nav["lat"] = msg.latitude
        self.latest_nav["lon"] = msg.longitude
        self.latest_nav["alt"] = msg.altitude

    def _odom_cb(self, msg: Odometry):
        # Boat's own position in the SAME frame /obstacles/fused_tracked is
        # published in (map_frame_ on geo_fusion_tracker_node, default
        # "map" — coincident with "odom" in this repo per njord.launch.py's
        # identity map->odom publisher, so /odometry/filtered's frame
        # already matches without an extra TF lookup here).
        p = msg.pose.pose.position
        self._boat_map_xy = (p.x, p.y)
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw_deg = math.degrees(math.atan2(siny_cosp, cosy_cosp)) % 360.0
        self.latest_nav["heading"] = round(yaw_deg, 2)

    def _vel_cb(self, msg: TwistStamped):
        # ENU: x=East, y=North — same assumption, same unverified-on-real-
        # hardware caveat, as src/foxglove/gui_telemetry_hw.py's on_vel().
        east = msg.twist.linear.x
        north = msg.twist.linear.y
        speed = math.hypot(east, north)
        self.latest_nav["sog"] = round(speed, 3)
        if speed > 0.05:
            self.latest_nav["cog"] = round(math.degrees(math.atan2(east, north)) % 360.0, 2)

    def _gpsraw_cb(self, msg: GPSRAW):
        self.latest_nav["rtk_mode"] = map_rtk_mode(msg.fix_type)

    def _battery_cb(self, msg: BatteryState):
        voltage = None if (msg.voltage != msg.voltage) else round(float(msg.voltage), 2)  # NaN check
        self.latest_health["battery_voltage"] = voltage
        # bms_reader.py never sets BatteryState.percentage (confirmed by
        # reading it) — a literal 0.0 here means "never wired", not
        # "empty". Reporting it as an empty battery would be a false
        # critical alarm for an upstream gap, so withhold it instead.
        soc = float(msg.percentage)
        self.latest_health["battery_soc"] = round(soc * 100.0, 1) if soc > 0.0 else None

    def _sonar_cb(self, msg: String):
        try:
            self.latest_sonar = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn("Malformed /sonar/decimated payload, ignoring", throttle_duration_sec=10.0)

    def _lidar_cb(self, msg: Detection3DArray):
        if self.latest_nav["lat"] is None or self._boat_map_xy is None:
            return  # no GPS fix or no odometry yet — can't geo-reference anything

        lat0, lon0 = self.latest_nav["lat"], self.latest_nav["lon"]
        boat_x, boat_y = self._boat_map_xy
        clusters = []

        for det in msg.detections:
            class_id = det.results[0].hypothesis.class_id if det.results else "unknown:0"
            cls_name, track_id = parse_class_and_id(class_id)
            mapped_class = CLASS_MAP.get(cls_name, "FLOTSAM")

            xm, ym = det.bbox.center.position.x, det.bbox.center.position.y
            # Offset FROM THE BOAT'S OWN current map position, not the raw
            # map coordinate treated as if it were already boat-relative
            # (it isn't — /obstacles/fused_tracked is in a fixed map frame,
            # not base_link). Same tangent-plane lift as
            # fusion/fusion/geo_fusion_node.py's _to_geo(), via the shared,
            # tested geo_utils.enu_offset_to_latlon() rather than a fourth
            # inline copy of the same formula.
            lat, lon = enu_offset_to_latlon(xm - boat_x, ym - boat_y, lat0, lon0)

            clusters.append({
                "id": track_id,
                "class": mapped_class,
                "centroid_local": [round(xm - boat_x, 2), round(ym - boat_y, 2), round(det.bbox.center.position.z, 2)],
                "centroid_global": [round(lat, 7), round(lon, 7)],
                "size_m": [round(det.bbox.size.x, 2), round(det.bbox.size.y, 2), round(det.bbox.size.z, 2)],
            })

        self.latest_clusters = clusters[:8]  # retain max 8 closest/most-recent hazards

    def _sam2_cb(self, msg: Image):
        mask = self.bridge.imgmsg_to_cv2(msg, desired_encoding="mono8")
        h, w = mask.shape
        polygons = []

        # mask value 1 = rigid obstacle/buoy, 2 = kelp canopy — matches
        # asket_sensor_fusion/sensor_fusion_obstacle_node.cpp's combined_mask
        # convention exactly (same pipeline, same encoding).
        for class_val, class_name in [(2, "KELP_CANOPY"), (1, "BUOY")]:
            bin_mask = (mask == class_val).astype(np.uint8)
            contours, _ = cv2.findContours(bin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for i, cnt in enumerate(contours):
                if cv2.contourArea(cnt) < 150:
                    continue
                approx = cv2.approxPolyDP(cnt, 2.0, True)
                norm_verts = [[round(pt[0][0] / w, 3), round(pt[0][1] / h, 3)] for pt in approx]
                polygons.append({
                    "id": i + (class_val * 100),
                    "class": class_name,
                    "conf": 0.92,
                    "vertices_norm": norm_verts,
                })
        self.latest_polygons = polygons[:4]  # cap at 4 largest polygons

    def _image_raw_cb(self, msg: Image):
        self.frame_counter += 1
        if self.frame_counter % 5 != 0:  # ~1 Hz out of a ~5 Hz-decimated subscription
            return
        cv_img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        resized = cv2.resize(cv_img, (640, 400), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        self.latest_jpeg_b64 = base64.b64encode(buf).decode("utf-8")

    # ── Packet assembly + dispatch ───────────────────────────────────────────

    def dispatch_packet(self):
        if self.latest_nav["lat"] is None:
            return  # no GPS fix yet — nothing meaningful to send
        self.seq += 1

        packet = {
            "asv_id": self.asv_id,
            "seq": self.seq,
            "timestamp": round(self.get_clock().now().nanoseconds / 1e9, 3),
            "nav": self.latest_nav,
            "lidar_clusters": self.latest_clusters,
            "sam2_polygons": self.latest_polygons,
            "health": self.latest_health,
        }
        if self.latest_sonar is not None:
            packet["sonar"] = self.latest_sonar
        if self.latest_jpeg_b64 is not None:
            packet["image_jpeg_b64"] = self.latest_jpeg_b64
            self.latest_jpeg_b64 = None

        try:
            if self.ws_queue.full():
                self.ws_queue.get_nowait()  # drop-tail: discard the stale frame
            self.ws_queue.put_nowait(json.dumps(packet))
        except Exception as exc:  # noqa: BLE001 — must never take the timer callback down
            self.get_logger().warn(f"Failed to enqueue telemetry packet: {exc}", throttle_duration_sec=5.0)

    # ── WebSocket client (outbound only) ─────────────────────────────────────

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._ws_client_worker())

    async def _ws_client_worker(self):
        backoff = 2.0
        while rclpy.ok():
            try:
                self.get_logger().info(f"Connecting to cloud ingest: {self.ws_url}")
                async with websockets.connect(self.ws_url, ping_interval=5, ping_timeout=10) as ws:
                    self.get_logger().info("Cloud WebSocket link established.")
                    backoff = 2.0
                    while rclpy.ok():
                        payload_str = await self.ws_queue.get()
                        await ws.send(payload_str)
            except Exception as e:  # noqa: BLE001 — must keep retrying regardless of cause
                self.get_logger().warn(f"Cloud WS link dropped: {e}. Retrying in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)  # capped exponential backoff


def main(args=None):
    rclpy.init(args=args)
    node = CloudTelemetryBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
