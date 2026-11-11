#!/usr/bin/env python3
"""Mirror the boat's GUI backend to Azure, for the cockpit hosted in the cloud.

Connects to `gui_backend` on the boat (default ws://127.0.0.1:8090/ws) as a
read-only client: it subscribes and answers pings, and it never sends a
command. It is a LIGHT copy for a remote viewer; the boat's full record stays
on the Jetson (mission_recorder, bags, the local spool):

* `<asv_id>/gui/latest.json`: the cockpit's current state, small (track capped
  to its last points), uploaded every `latest_interval_s` (3 s).
* `<asv_id>/gui/telemetry/YYYY/MM/DD/HH.jsonl`: one light line every
  `history_interval_s` (15 s): position, heading, pico, power, link, sonar,
  mission, alarms.
* `<asv_id>/gui/images/...jpg`: one snapshot every `image_interval_s` (30 s),
  at most `image_max_width` (320) pixels wide.

Through the same spool-and-upload sink as cloud_telemetry_node (azure_sink.py).
Nothing is deleted in Azure automatically.

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
        p("record_rate_hz", 1.0)       # how often the live state is refreshed locally
        p("latest_interval_s", 3.0)    # latest.json upload; < 6 s or the cockpit flags link loss
        p("history_interval_s", 15.0)  # one light history line
        p("state_interval_s", 15.0)    # state.json (trails, plan); sooner if hello/plan change
        p("upload_interval_s", 30.0)   # history appends
        p("image_interval_s", 30.0)    # 0 = no snapshots
        p("image_max_width", 320)
        p("image_quality", 60)
        p("track_max_points", 300)
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
        self.history_interval = float(g("history_interval_s"))
        self.image_width = int(g("image_max_width"))
        self.image_quality = int(g("image_quality"))
        self._last_history = 0.0
        self.state_interval = float(g("state_interval_s"))
        self._last_state = 0.0
        self._state_key = None
        self._state_seq = 0
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

    def _shrink(self, jpeg: bytes, header: dict):
        """Re-encode a snapshot to at most image_max_width pixels."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            return jpeg, header
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return jpeg, header
        h, w = img.shape[:2]
        if w > self.image_width:
            img = cv2.resize(img, (self.image_width, max(1, round(h * self.image_width / w))),
                             interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
        if not ok:
            return jpeg, header
        h2, w2 = img.shape[:2]
        return buf.tobytes(), {**header, "format": "jpeg", "width": w2, "height": h2, "size_bytes": len(buf)}

    def _record(self):
        now = time.time()
        with self.lock:
            if self.state.hello is None:
                return  # never reached the backend: nothing true to record
            extra = self.state.latest(backend_connected=self.backend_connected, now_s=now)
            key = self.state.state_key()
            state_due = key != self._state_key or now - self._last_state >= self.state_interval
            slow = self.state.state() if state_due else None
            history_due = now - self._last_history >= self.history_interval
            hist = self.state.history(backend_connected=self.backend_connected, now_s=now) if history_due else None
            jpeg, header = (None, None)
            if self.image_interval > 0 and now - self._last_image >= self.image_interval:
                jpeg, header = self.state.take_image()
        self.seq += 1
        extra["seq"] = self.seq
        if slow is not None:
            self._state_seq += 1
            slow["state_seq"] = self._state_seq
            self.sink.set_aux("state.json", slow)
            self._state_key, self._last_state = key, now
        # The browser fetches state.json only when this number changes.
        extra["state_seq"] = self._state_seq
        if jpeg is not None:
            jpeg, header = self._shrink(jpeg, header)
            self._last_image = now
            # The header (its timestamp) must describe the picture actually
            # uploaded, never a newer frame we skipped: an old image under a
            # new timestamp is the lie the camera panel exists to prevent.
            self._img_header = header
        extra["camera_image_header"] = self._img_header
        try:
            if hist is not None or jpeg is not None:
                rec = hist or {"kind": "gui_mirror_image", "timestamp": round(now, 3)}
                rec["seq"] = self.seq
                if jpeg is not None:
                    rec["camera"] = header
                self.sink.submit(rec, jpeg, latest_extra=extra)
                if hist is not None:
                    self._last_history = now
            else:
                self.sink.set_latest({}, latest_extra=extra)
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
