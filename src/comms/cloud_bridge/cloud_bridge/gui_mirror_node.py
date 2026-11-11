#!/usr/bin/env python3
"""Mirror the boat's GUI backend to Azure, for the cockpit hosted in the cloud.

Connects to `gui_backend` on the boat (default ws://127.0.0.1:8090/ws) as a
read-only client: it subscribes and answers pings, and it never sends a
command. Every `record_rate_hz` it writes what changed to
`<asv_id>/gui/telemetry/YYYY/MM/DD/HH.jsonl`, the full current state to
`<asv_id>/gui/latest.json`, and camera snapshots to `<asv_id>/gui/images/`,
through the same spool-and-upload sink as cloud_telemetry_node (azure_sink.py).

The browser side is NAMIBIA_REDESIGNED_GUI/src/lib/azureTransport.js. See
cloud/azure/README.md.
"""
import asyncio
import json
import threading
import time

import rclpy
from rclpy.node import Node

from cloud_bridge import azure_sink
from cloud_bridge.gui_mirror import SUBSCRIPTIONS, MirrorState

try:
    import websockets
    _WS_ERR = None
except ImportError as exc:  # noqa: BLE001
    websockets = None
    _WS_ERR = exc


class GuiMirrorNode(Node):
    def __init__(self):
        super().__init__("gui_cloud_mirror")
        p = self.declare_parameter
        p("gui_ws_url", "ws://127.0.0.1:8090/ws")
        p("asv_id", "ASKET_02")
        p("record_rate_hz", 1.0)
        p("image_interval_s", 5.0)
        p("upload_interval_s", 5.0)   # history + images
        p("latest_interval_s", 1.0)   # latest.json, what live viewers poll
        p("track_max_points", 5000)
        p("azure_connection_string_file", "/data/secrets/azure_storage_connection_string")
        p("azure_container", "telemetry")
        p("spool_dir", "/data/cloud_spool_gui")
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        if websockets is None:
            raise RuntimeError(f"gui_cloud_mirror needs the 'websockets' package ({_WS_ERR})")
        conn = azure_sink.load_connection_string(g("azure_connection_string_file"))
        if not conn:
            raise RuntimeError(
                "gui_cloud_mirror: no AZURE_STORAGE_CONNECTION_STRING and no file at "
                f"{g('azure_connection_string_file')}"
            )
        self.url = g("gui_ws_url")
        self.prefix = f"{g('asv_id')}/gui"
        self.image_interval = float(g("image_interval_s"))
        self.sink = azure_sink.make_sink(
            conn, g("azure_container"), spool_dir=g("spool_dir"), asv_id=self.prefix,
            upload_interval_s=float(g("upload_interval_s")),
            latest_interval_s=float(g("latest_interval_s")),
            log=lambda m: self.get_logger().warn(m, throttle_duration_sec=30.0),
        )
        self.sink.start()

        self.state = MirrorState(track_max_points=int(g("track_max_points")))
        self.lock = threading.Lock()
        self.backend_connected = False
        self.seq = 0
        self._last_image = 0.0
        self._img_header = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=lambda: self.loop.run_until_complete(self._ws_worker()), daemon=True).start()
        self.create_timer(1.0 / max(float(g("record_rate_hz")), 0.05), self._record)
        self.get_logger().info(f"Mirroring {self.url} -> Azure '{self.prefix}/' (read-only client)")

    async def _ws_worker(self):
        backoff = 1.0
        while rclpy.ok():
            try:
                async with websockets.connect(self.url, max_size=None, ping_interval=10) as ws:
                    self.backend_connected = True
                    backoff = 1.0
                    self.get_logger().info("Connected to gui_backend")
                    await ws.send(json.dumps({"type": "subscribe", "streams": SUBSCRIPTIONS}))
                    async for raw in ws:
                        now_ms = int(time.time() * 1000)
                        with self.lock:
                            if isinstance(raw, bytes):
                                self.state.handle_video(raw, now_ms)
                                reply, resync = None, []
                            else:
                                try:
                                    reply = self.state.handle(json.loads(raw), now_ms)
                                except json.JSONDecodeError:
                                    continue
                                resync = self.state.take_resyncs()
                        if reply:
                            await ws.send(json.dumps(reply))
                        if resync:
                            await ws.send(json.dumps({"type": "subscribe", "streams": resync}))
            except Exception as exc:  # noqa: BLE001 — keep retrying whatever happened
                if self.backend_connected:
                    self.get_logger().warn(f"gui_backend link lost: {exc}")
                self.backend_connected = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    def _record(self):
        now = time.time()
        with self.lock:
            if self.state.hello is None:
                return  # never reached the backend: nothing true to record
            rec, extra, jpeg = self.state.record(backend_connected=self.backend_connected, now_s=now)
        self.seq += 1
        rec["seq"] = self.seq
        if jpeg is not None and (self.image_interval <= 0 or now - self._last_image < self.image_interval):
            jpeg = None
        if jpeg is not None:
            self._last_image = now
        # The picture's header (its timestamp) must describe the picture that
        # is actually uploaded, never a newer frame we skipped: an old image
        # under a new timestamp is the lie the camera panel exists to prevent.
        cam = rec.pop("camera", None)
        if jpeg is not None:
            rec["camera"] = cam
            self._img_header = cam
        elif cam and cam.get("type") != "camera_frame":
            rec["camera_status"] = cam
        extra["camera_image_header"] = self._img_header
        extra["camera_status"] = cam if cam and cam.get("type") != "camera_frame" else None
        try:
            self.sink.submit(rec, jpeg, latest_extra=extra)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().error(f"spool write failed: {exc}", throttle_duration_sec=10.0)


def main(args=None):
    rclpy.init(args=args)
    node = GuiMirrorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.sink.stop(flush=True)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
