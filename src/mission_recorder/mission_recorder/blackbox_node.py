"""Always-on black box: record GPS, sonar, camera and telemetry on the Jetson,
copy each finished chunk to Azure whenever there is internet.

Separate from mission_recorder (which writes the survey files for a mission
the operator starts): this one runs from boot to shutdown, so nothing that
happened is missing because nobody pressed "record".

* Recording is `ros2 bag record` (MCAP, zstd) in its own process, split every
  ``split_s`` seconds, into ``root/<UTC start>/``. A crash of this node or of
  the recorder costs at most the chunk being written.
* The camera is recorded at ``camera_hz`` (default 1 frame/s) through a
  throttled copy on ``/blackbox/camera/compressed``; at 10 fps the Jetson's
  disk would fill in hours.
* Sonar is recorded as /sonar/raw (the bytes as received, the canonical
  record; /sonar/points is derived from it and can be regenerated).
* Disk guard: recording stops below ``min_free_bytes`` and resumes when space
  is back. A full disk takes the whole Jetson down.
* Upload: a background thread copies finished chunks to the
  ``container`` container under ``<asv>/blackbox/``, rate-capped by
  ``max_upload_kbps``, retrying with back-off while there is no internet.
  Nothing is deleted anywhere.
* Also uploads ``extra_upload_dirs`` (default: SonarView's recordings in
  /data/sonarview -> <asv>/sonarview/), since with SonarView holding the
  sonar its own logs are the sonar record.
* Status as JSON on /blackbox/status every 2 s.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from mission_recorder.blackbox_upload import pending_files, pending_tree, upload_one

DEFAULT_TOPICS = [
    # GPS / autopilot / navigation
    "/mavros/global_position/raw/fix", "/mavros/global_position/global",
    "/mavros/global_position/raw/gps_vel", "/mavros/global_position/raw/satellites",
    "/mavros/global_position/compass_hdg", "/mavros/gpsstatus/gps1/raw",
    "/mavros/home_position/home", "/mavros/state", "/mavros/imu/data",
    "/mavros/local_position/odom", "/mavros/battery", "/mavros/statustext/recv",
    "/mavros/rc/out", "/asket/heading", "/gps/filtered", "/odometry/filtered",
    "/battery/state", "/mission/state", "/gui/link_status", "/diagnostics",
    "/system_test/report", "/tf", "/tf_static",
    # Sonar
    "/sonar/raw", "/sonar/status", "/sonar/attitude",
    # Camera (throttled copy) and what was seen in it
    "/blackbox/camera/compressed", "/oak/detections", "/oak/water_obstacles",
    "/yolo/detections",
]


def _die_with_parent():
    """Child side of Popen: get SIGINT (a clean close of the .mcap) when this
    node dies, even by SIGKILL, so no orphan recorder keeps writing."""
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGINT)  # PR_SET_PDEATHSIG


class BlackBox(Node):
    def __init__(self):
        super().__init__("blackbox")
        p = self.declare_parameter
        self.root = Path(p("root", "/data/blackbox").value)
        self.topics = list(p("topics", DEFAULT_TOPICS).value)
        self.split_s = int(p("split_s", 300).value)
        self.camera_topic = p("camera_topic", "/oak/rgb/image_raw/compressed").value
        self.camera_hz = float(p("camera_hz", 1.0).value)
        self.min_free = int(p("min_free_bytes", 1 << 30).value)
        self.resume_free = int(p("resume_free_bytes", 2 << 30).value)
        self.upload = bool(p("upload", True).value)
        self.conn_file = Path(p("connection_string_file", "/data/secrets/azure_storage_connection_string").value)
        self.container = p("container", "asket-missions").value
        self.asv = p("asv_id", "ASKET_02").value
        self.max_kbps = float(p("max_upload_kbps", 4000.0).value)
        # Other folders to copy to Azure as they fill: "local_dir:blob_subprefix",
        # e.g. SonarView's recordings when it, not omniscan_bridge, has the sonar.
        self.extra = []
        for spec in p("extra_upload_dirs", ["/data/sonarview:sonarview"]).value:
            local, _, sub = spec.partition(":")
            if local:
                self.extra.append((Path(local), sub or Path(local).name))

        self.root.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen | None = None
        self.session: str | None = None
        self.state = "starting"
        self.up = {"pending_files": 0, "pending_bytes": 0, "uploaded_files": 0,
                   "last_ok": None, "last_error": None, "online": None}
        self._stop = threading.Event()

        self._last_cam = 0.0
        self.pub_cam = self.create_publisher(CompressedImage, "/blackbox/camera/compressed", 5)
        self.create_subscription(CompressedImage, self.camera_topic, self._on_cam, qos_profile_sensor_data)
        self.pub_status = self.create_publisher(String, "/blackbox/status", 5)

        self.create_timer(5.0, self._supervise)
        self.create_timer(2.0, self._publish_status)
        if self.upload:
            threading.Thread(target=self._upload_loop, daemon=True).start()
        self._supervise()

    # -- camera throttle ----------------------------------------------------
    def _on_cam(self, msg: CompressedImage):
        now = time.monotonic()
        if self.camera_hz > 0 and now - self._last_cam >= 1.0 / self.camera_hz:
            self._last_cam = now
            self.pub_cam.publish(msg)

    # -- recorder process ---------------------------------------------------
    def _start(self):
        self.session = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        cmd = ["ros2", "bag", "record", "-s", "mcap", "--storage-preset-profile", "zstd_fast",
               "-d", str(self.split_s), "-o", str(self.root / self.session),
               "--node-name", "blackbox_bag", "--topics", *self.topics]
        log = open(self.root / f"{self.session}.log", "ab")
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                     preexec_fn=_die_with_parent)
        self.state = "recording"
        self.get_logger().info(f"recording {len(self.topics)} topics to {self.root / self.session}")

    def _stop_rec(self, why: str):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGINT)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None
        self.session = None
        self.state = why

    def _supervise(self):
        free = shutil.disk_usage(self.root).free
        running = self.proc is not None and self.proc.poll() is None
        if running and free < self.min_free:
            self.get_logger().error(f"disk nearly full ({free >> 20} MiB free): recording stopped")
            self._stop_rec("disk_full")
        elif not running:
            if self.proc is not None:
                self.get_logger().warn(f"ros2 bag record exited ({self.proc.returncode}); restarting")
                self.proc, self.session = None, None
            if self.state != "disk_full" or free > self.resume_free:
                self._start()

    # -- upload -------------------------------------------------------------
    def _client(self):
        from azure.storage.blob import BlobServiceClient
        conn = self.conn_file.read_text().strip()
        cc = BlobServiceClient.from_connection_string(conn).get_container_client(self.container)
        try:
            cc.create_container()
        except Exception:  # exists (or no network: the upload will say so)
            pass
        return cc

    def _upload_loop(self):
        cc, backoff = None, 30.0
        while not self._stop.is_set():
            items = [(f"{self.asv}/blackbox", i) for i in pending_files(self.root, self.session)]
            for local, sub in self.extra:
                items += [(f"{self.asv}/{sub}", i) for i in pending_tree(local)]
            self.up["pending_files"] = len(items)
            self.up["pending_bytes"] = sum(i.size for _, i in items)
            if not items:
                self._stop.wait(20.0)
                continue
            try:
                cc = cc or self._client()
                for prefix, it in items:
                    if self._stop.is_set():
                        return
                    upload_one(cc, prefix, it, self.max_kbps)
                    self.up["uploaded_files"] += 1
                    self.up["pending_files"] -= 1
                    self.up["pending_bytes"] -= it.size
                    self.up["last_ok"] = time.time()
                self.up["online"], self.up["last_error"], backoff = True, None, 30.0
            except Exception as exc:  # no internet, DNS, auth, ...
                cc = None
                self.up["online"] = False
                self.up["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 300.0)

    # -- status -------------------------------------------------------------
    def _publish_status(self):
        du = shutil.disk_usage(self.root)
        self.pub_status.publish(String(data=json.dumps({
            "state": self.state, "session": self.session, "root": str(self.root),
            "free_bytes": du.free, "camera_hz": self.camera_hz, "upload": self.upload, **self.up,
        })))

    def destroy_node(self):
        self._stop.set()
        self._stop_rec("stopped")
        return super().destroy_node()


def main():
    rclpy.init()
    node = BlackBox()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
