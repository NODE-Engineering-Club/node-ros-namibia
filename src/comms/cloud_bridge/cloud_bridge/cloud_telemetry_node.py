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
  * `/cloud_bridge/status` (new): the cloud link's connected/disconnected
    state, published locally over ROS (transient_local), not just logged —
    previously the only way to see this was reading log output.
  * `planned_route` (new, not in the original design note's §5.1 schema):
    forwards whatever `mission/waypoint_route_loader.py` last published on
    `/mission/planned_route` — a human-authored waypoint file rendered as a
    reference line on the dashboard for manual piloting. See that node's
    own docstring; this is strictly a display feature, nothing here or in
    it ever calls `/mission/start`.

2026-11-11 additions (Azure + the boat as actually fitted):

  * **Azure Blob Storage sink** (`azure_sink.py`). Every packet is also
    spooled to disk and appended to `<asv_id>/telemetry/YYYY/MM/DD/HH.jsonl`
    in Blob Storage, with camera snapshots as separate JPEG blobs and
    `<asv_id>/latest.json` for the live view. This is what makes history
    retrievable: the WebSocket relay above is live-only. Enabled when a
    connection string is found (env `AZURE_STORAGE_CONNECTION_STRING` or the
    file in `azure_connection_string_file`); recorded at `azure_record_rate_hz`
    whether or not there is a GPS fix, so a bench run still logs sonar and
    health.
  * **WebSocket is optional.** Empty `cloud_ws_url` (or no `websockets`
    package) disables it instead of refusing to start.
  * **Omniscan 3D**: `sonar3d` from `omniscan_bridge`'s `/sonar/status` and
    `/sonar/points` (strongest returns of the latest ping). The old
    `/sonar/decimated` (Omniscan 450 waterfall) is still forwarded as `sonar`
    when something publishes it.
  * **OAK-D camera**: snapshots also from `camera_compressed_topic`
    (default `/oak/rgb/image_raw/compressed`, the GUI's source); the raw
    `/camera/front/image_raw` path is unchanged.
  * **Signal**: `nav.sats` / `nav.eph_m` from GPSRAW, `link` with the Jetson's
    own Wi-Fi RSSI, upload backlog and WebSocket state, and
    `health.jetson_temp_c` from the thermal zones.
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
from njord_msgs.msg import PlannedRoute
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, CompressedImage, Image, NavSatFix, PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import String
from vision_msgs.msg import Detection3DArray

from cloud_bridge.geo_utils import CLASS_MAP, enu_offset_to_latlon, map_rtk_mode, parse_class_and_id
from cloud_bridge.onboard_health import read_max_temp_c, read_wifi, summarize_sonar_points
from cloud_bridge import azure_sink

try:
    from asket_interfaces.msg import SonarStatus
    _HAS_SONAR_STATUS = True
except ImportError:  # workspace built without asket_interfaces
    _HAS_SONAR_STATUS = False

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


        # Empty = live relay off (see cloud_bridge_params.yaml for why).
        self.declare_parameter("cloud_ws_url", "")
        self.declare_parameter("asv_id", "ASKET_02")
        self.declare_parameter("stream_rate_hz", 5.0)
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("camera_compressed_topic", "/oak/rgb/image_raw/compressed")
        self.declare_parameter("wifi_interface", "wlan0")
        self.declare_parameter("sonar_cloud_points", 60)  # strongest returns kept per ping
        # Azure Blob Storage (see azure_sink.py). Disabled when no
        # connection string is found.
        self.declare_parameter("azure_enabled", True)
        self.declare_parameter("azure_connection_string_file", "/data/secrets/azure_storage_connection_string")
        self.declare_parameter("azure_container", "telemetry")
        self.declare_parameter("azure_spool_dir", "/data/cloud_spool")
        self.declare_parameter("azure_record_rate_hz", 1.0)
        self.declare_parameter("azure_image_interval_s", 5.0)
        self.declare_parameter("azure_upload_interval_s", 2.0)
        self.declare_parameter("azure_keep_local_images", False)

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
        self.latest_planned_route = None  # {"name": ..., "waypoints": [[lat,lon], ...]}, or None
        self.latest_sonar3d = None  # omniscan_bridge status + latest ping summary
        self._sonar3d_points = None
        self._latest_jpeg_bytes = None  # newest snapshot, for the Azure sink
        self._last_image_record = 0.0
        self._last_record = 0.0
        self._ws_connected = False

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
        cam_topic = self.get_parameter("camera_compressed_topic").value
        if cam_topic:
            self.create_subscription(CompressedImage, cam_topic, self._image_compressed_cb, sensor_qos)
        # Omniscan 3D via omniscan_bridge.
        self.create_subscription(PointCloud2, "/sonar/points", self._sonar_points_cb, sensor_qos)
        if _HAS_SONAR_STATUS:
            self.create_subscription(SonarStatus, "/sonar/status", self._sonar_status_cb, 10)
        self.create_subscription(BatteryState, "/battery/state", self._battery_cb, 10)
        # sonar_bridge_node's own decimated output (see that file) — a
        # single pre-serialized JSON string, so this node doesn't need to
        # know SonarLink's packet format at all, only the final small
        # payload shape it re-embeds under packet["sonar"].
        self.create_subscription(String, "/sonar/decimated", self._sonar_cb, sensor_qos)

        # Human-planned route for display (mission/waypoint_route_loader.py)
        # — NOT an autonomous mission, see that node and PlannedRoute.msg's
        # own docstrings. transient_local to match that node's publisher
        # QoS: whichever of the two nodes starts second still gets the
        # route immediately, not just future updates.
        route_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL, depth=1,
        )
        self.create_subscription(PlannedRoute, "/mission/planned_route", self._planned_route_cb, route_qos)

        # Local ROS-visible health: until now the only way to see whether
        # the cloud link is actually up was reading this node's log output.
        # transient_local so a dashboard/rqt/Foxglove panel that subscribes
        # after the fact still immediately sees the current state, same
        # convention as this repo's existing /competition/status,
        # /mission/status.
        status_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL, depth=1,
        )
        self.pub_status = self.create_publisher(String, "/cloud_bridge/status", status_qos)
        self._publish_status(connected=False, detail="starting")

        # ── Azure Blob Storage sink ─────────────────────────────────────────
        self.sink = self._make_azure_sink()

        # ── Async WebSocket loop in a separate thread ───────────────────────
        self.ws_enabled = bool(self.ws_url) and _HAS_WEBSOCKETS
        if self.ws_url and not _HAS_WEBSOCKETS:
            self.get_logger().warn(
                f"'websockets' failed to import ({_WS_IMPORT_ERROR}); live WebSocket relay disabled"
            )
        if self.ws_enabled:
            self.ws_queue = asyncio.Queue(maxsize=1)  # drop-tail: size=1, oldest discarded
            self.loop = asyncio.new_event_loop()
            self.ws_thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self.ws_thread.start()

        rate_hz = self.get_parameter("stream_rate_hz").get_parameter_value().double_value
        self.timer = self.create_timer(1.0 / rate_hz, self.dispatch_packet)
        self.create_timer(5.0, self._refresh_onboard_health)
        self._refresh_onboard_health()
        self.get_logger().info(
            f"Cloud telemetry bridge operational at {rate_hz} Hz -> "
            f"ws: {self.ws_url if self.ws_enabled else 'off'}, "
            f"azure: {'on' if self.sink else 'off'}"
        )

    def _make_azure_sink(self):
        if not self.get_parameter("azure_enabled").value:
            return None
        path = self.get_parameter("azure_connection_string_file").value
        conn = azure_sink.load_connection_string(path)
        if not conn:
            self.get_logger().warn(
                f"Azure sink off: no AZURE_STORAGE_CONNECTION_STRING and no file at {path}"
            )
            return None
        try:
            sink = azure_sink.make_sink(
                conn,
                self.get_parameter("azure_container").value,
                spool_dir=self.get_parameter("azure_spool_dir").value,
                asv_id=self.asv_id,
                upload_interval_s=float(self.get_parameter("azure_upload_interval_s").value),
                keep_local_images=bool(self.get_parameter("azure_keep_local_images").value),
                log=lambda m: self.get_logger().warn(m, throttle_duration_sec=30.0),
            )
        except Exception as exc:  # noqa: BLE001 — offline at start-up is normal on a boat
            self.get_logger().error(f"Azure sink could not start ({exc}); retrying is not automatic, restart the node")
            return None
        sink.start()
        self.get_logger().info(
            f"Azure sink on: container '{self.get_parameter('azure_container').value}', "
            f"prefix '{self.asv_id}/', spool {self.get_parameter('azure_spool_dir').value}"
        )
        return sink

    def _refresh_onboard_health(self):
        self.latest_health["jetson_temp_c"] = read_max_temp_c()
        wifi = read_wifi(self.get_parameter("wifi_interface").value)
        self.latest_health["wifi_rssi_dbm"] = wifi["rssi_dbm"] if wifi else None
        self._wifi = wifi
        if self.sink is not None or self.ws_enabled:
            self._publish_status(connected=self._ws_connected, detail="periodic")

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
        self.latest_nav["sats"] = int(msg.satellites_visible) if msg.satellites_visible != 255 else None
        # eph is HDOP*100 in MAVLink GPS_RAW_INT; 65535 = unknown.
        self.latest_nav["hdop"] = round(msg.eph / 100.0, 2) if msg.eph != 65535 else None

    def _sonar_status_cb(self, msg):
        self.latest_sonar3d = {
            "connected": bool(msg.connected),
            "ping_rate_hz": round(float(msg.actual_ping_rate_hz), 2),
            "points_per_ping": int(msg.points_per_ping),
            "range_setting_m": round(float(msg.range_setting_m), 1),
            "gain": int(msg.gain_setting),
            "packet_loss": round(float(msg.packet_loss_ratio), 3),
            "pitch_deg": round(float(msg.pitch_deg), 1),
            "roll_deg": round(float(msg.roll_deg), 1),
            "clock_offset_s": round(msg.clock_offset_ms / 1000.0, 1),
        }

    def _sonar_points_cb(self, msg: PointCloud2):
        pts = point_cloud2.read_points_list(msg, field_names=("x", "y", "z", "intensity"), skip_nans=True)
        # read_points_list yields numpy float32, which json cannot encode.
        self._sonar3d_points = summarize_sonar_points(
            [tuple(float(v) for v in p) for p in pts],
            max_points=int(self.get_parameter("sonar_cloud_points").value),
        )
        self._sonar3d_points["rx_utc"] = round(self.get_clock().now().nanoseconds / 1e9, 3)

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

    def _planned_route_cb(self, msg: PlannedRoute):
        self.latest_planned_route = {
            "name": msg.name,
            "waypoints": [[round(p.latitude, 7), round(p.longitude, 7)] for p in msg.waypoints],
        }
        self.get_logger().info(
            f"Planned route updated: '{msg.name}', {len(msg.waypoints)} waypoint(s)", throttle_duration_sec=1.0,
        )

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
        self._latest_jpeg_bytes = buf.tobytes()

    def _image_compressed_cb(self, msg: CompressedImage):
        self.frame_counter += 1
        if self.frame_counter % 5 != 0:
            return
        arr = np.frombuffer(bytes(msg.data), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            self.get_logger().warn(f"Undecodable CompressedImage ({msg.format})", throttle_duration_sec=10.0)
            return
        h, w = img.shape[:2]
        scale = 640.0 / max(w, 1)
        if scale < 1.0:
            img = cv2.resize(img, (640, int(h * scale)), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        self.latest_jpeg_b64 = base64.b64encode(buf).decode("utf-8")
        self._latest_jpeg_bytes = buf.tobytes()

    # ── Packet assembly + dispatch ───────────────────────────────────────────

    def dispatch_packet(self):
        has_fix = self.latest_nav["lat"] is not None
        now = self.get_clock().now().nanoseconds / 1e9
        record_period = 1.0 / max(float(self.get_parameter("azure_record_rate_hz").value), 1e-3)
        want_record = self.sink is not None and (now - self._last_record) >= record_period
        if not has_fix and not want_record:
            return  # no GPS fix yet — nothing meaningful to stream live
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
        if self.latest_sonar3d is not None or self._sonar3d_points is not None:
            # Points only while they are fresh: once the sonar stops, the last
            # ping must not keep being sent as if it were current.
            pts = self._sonar3d_points
            fresh = pts is not None and now - pts.get("rx_utc", 0.0) <= 3.0
            packet["sonar3d"] = {**(self.latest_sonar3d or {}), **(pts if fresh else {})}
        packet["link"] = {
            "wifi": getattr(self, "_wifi", None),
            "ws_connected": self._ws_connected,
            "azure": self.sink.status.as_dict() if self.sink else None,
        }
        if self.latest_planned_route is not None:
            # NOT in the original design note's schema (§5.1) — a later
            # addition for the manual-piloting reference-line feature, see
            # mission/waypoint_route_loader.py. Sent on every packet once
            # loaded (small payload, no reason to decimate it further).
            packet["planned_route"] = self.latest_planned_route
        if want_record:
            self._last_record = now
            jpeg = None
            img_period = float(self.get_parameter("azure_image_interval_s").value)
            if self._latest_jpeg_bytes and img_period > 0 and now - self._last_image_record >= img_period:
                jpeg = self._latest_jpeg_bytes
                self._latest_jpeg_bytes = None
                self._last_image_record = now
            try:
                self.sink.submit(packet, jpeg)
            except Exception as exc:  # noqa: BLE001 — a full disk must not stop the live stream
                self.get_logger().error(f"Azure spool write failed: {exc}", throttle_duration_sec=10.0)

        if not (has_fix and self.ws_enabled):
            return
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

    def _publish_status(self, connected: bool, detail: str):
        # rclpy Publisher.publish() is safe to call from a non-executor
        # thread (it only serializes and hands off), unlike subscription
        # callbacks — this is called directly from the asyncio worker
        # thread below, not bounced back onto the main executor.
        self._ws_connected = connected
        self.pub_status.publish(String(data=json.dumps({
            "connected": connected,
            "detail": detail,
            "ws_url": self.ws_url,
            "seq": self.seq,
            "azure": self.sink.status.as_dict() if getattr(self, "sink", None) else None,
        })))

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._ws_client_worker())

    async def _ws_client_worker(self):
        backoff = 2.0
        while rclpy.ok():
            try:
                self.get_logger().info(f"Connecting to cloud ingest: {self.ws_url}")
                self._publish_status(connected=False, detail="connecting")
                async with websockets.connect(self.ws_url, ping_interval=5, ping_timeout=10) as ws:
                    self.get_logger().info("Cloud WebSocket link established.")
                    self._publish_status(connected=True, detail="connected")
                    backoff = 2.0
                    while rclpy.ok():
                        payload_str = await self.ws_queue.get()
                        await ws.send(payload_str)
            except Exception as e:  # noqa: BLE001 — must keep retrying regardless of cause
                self.get_logger().warn(f"Cloud WS link dropped: {e}. Retrying in {backoff:.1f}s...")
                self._publish_status(connected=False, detail=str(e))
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
        if node.sink is not None:
            node.sink.stop(flush=True)  # push the last seconds before exiting
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
