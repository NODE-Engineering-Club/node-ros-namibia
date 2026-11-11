"""What the boat's GUI backend says, kept so it can be replayed from Azure.

`gui_mirror_node` connects to `gui_backend`'s own WebSocket on the boat as
an ordinary client, subscribes to every stream the cockpit uses, and feeds
each message to :class:`MirrorState`. Once a second the state becomes one
record for `azure_sink`; the browser side
(`NAMIBIA_REDESIGNED_GUI/src/lib/azureTransport.js`) turns `latest.json` back
into the same WebSocket messages, so the cockpit runs unchanged: same
readiness verdict, same ages, same alarms.

Why mirror the backend instead of building cloud packets from ROS topics:
the backend already turns topics into the streams the cockpit reads (pico
state, pre-flight report, alarms, link profile, mission recorder). A second
translation in the cloud path would drift from it, and anything it missed
would show as "no data" on a screen that is fine on the boat.

No ROS in this file, so it is tested with plain dicts.
"""
from __future__ import annotations

import json
import struct
from typing import Optional

#: What the redesigned cockpit subscribes to (NAMIBIA_REDESIGNED_GUI/src/lib/vessel.js),
#: at rates that fit a once-a-second cloud record.
SUBSCRIPTIONS = [
    {"name": "vessel", "rate_hz": 2},
    {"name": "pico", "rate_hz": 1},
    {"name": "heading", "rate_hz": 1},
    {"name": "link", "rate_hz": 1},
    {"name": "track", "rate_hz": 1},
    {"name": "coverage", "rate_hz": 1},
    {"name": "lidar", "rate_hz": 1},
    {"name": "power", "rate_hz": 1},
    {"name": "sonar", "rate_hz": 1},
    {"name": "mission", "rate_hz": 1},
    {"name": "diagnostics", "rate_hz": 0.2},
    {"name": "plan"},
    {"name": "camera", "rate_hz": 1},
]

#: What a light history line keeps: small, and enough to replay a route
#: and the boat's condition. Track, coverage, lidar, plan and the pre-flight
#: report are left to the Jetson's own recording.
HISTORY_STREAMS = ("vessel", "heading", "pico", "power", "link", "sonar", "mission")

#: Large, slow-changing parts kept out of latest.json and sent as state.json.
SLOW_STREAMS = ("track", "coverage", "plan")

#: Streams that arrive as increments ({"from", "total", points|segments}).
APPEND_ONLY = ("track", "coverage")


def decode_video(frame: bytes) -> tuple[dict, bytes]:
    """`[4-byte BE header length][header JSON][image]`, as gui_backend/core/video_frame.py."""
    if len(frame) < 4:
        raise ValueError("short frame")
    (length,) = struct.unpack(">I", frame[:4])
    if length > 8192 or len(frame) < 4 + length:
        raise ValueError("bad header length")
    return json.loads(frame[4:4 + length].decode("utf-8")), frame[4 + length:]


class MirrorState:
    def __init__(self, track_max_points: int = 300):
        self.track_max = track_max_points
        self.hello: Optional[dict] = None
        self.meta: dict[str, dict] = {}       # subscribed / profile / alarms / notice
        self.unavailable: dict[str, dict] = {}  # stream_unavailable, by stream
        self.frames: dict[str, dict] = {}     # latest data message per stream
        self.camera_header: Optional[dict] = None
        self.camera_image: Optional[bytes] = None
        self.camera_new = False
        self.resync_needed: set[str] = set()
        self.last_rx_utc_ms: Optional[int] = None

    # -- inbound ----------------------------------------------------------

    def handle(self, msg: dict, now_ms: int) -> Optional[dict]:
        """Take one JSON message. Returns a reply to send, if any (pong)."""
        self.last_rx_utc_ms = now_ms
        kind = msg.get("type")
        if kind == "ping":
            # Answered straight away: the backend picks the link profile from
            # this round trip, and a slow mirror would drag itself to "minimal".
            return {"type": "pong", "id": msg.get("id")}
        if kind == "hello":
            self.hello = msg
        elif kind in ("subscribed", "profile", "alarms", "notice"):
            self.meta[kind] = msg
        elif kind == "stream_unavailable":
            self.unavailable[msg.get("stream", "?")] = msg
        elif kind == "data":
            name = msg.get("stream")
            if not name:
                return None
            if name in APPEND_ONLY:
                msg = self._accumulate(name, msg)
                if msg is None:
                    return None
            self.frames[name] = msg
        return None

    def handle_video(self, frame: bytes, now_ms: int) -> None:
        self.last_rx_utc_ms = now_ms
        try:
            header, image = decode_video(frame)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if header.get("type") == "camera_frame" and image:
            self.camera_header, self.camera_image, self.camera_new = header, image, True
        else:
            # Status frame: keep the last picture, but record why it stopped.
            self.camera_header = {**(self.camera_header or {}), **header}

    def _accumulate(self, name: str, msg: dict) -> Optional[dict]:
        """Same splice as asket_gui's Connection.#accumulate."""
        inc = msg.get("payload") or {}
        if inc.get("from") is None:
            return msg
        key = "segments" if "segments" in inc else "points"
        held = ((self.frames.get(name) or {}).get("payload") or {}).get(key, [])
        start = inc["from"]
        if start == 0:
            full = list(inc.get(key, []))
        elif start > len(held):
            # A gap: never splice a hole. Ask for the whole history again.
            self.resync_needed.add(name)
            return None
        else:
            full = held[:start] + list(inc.get(key, []))
        return {**msg, "payload": {**inc, "from": 0, key: full}}

    # -- outbound ---------------------------------------------------------

    def take_resyncs(self) -> list[dict]:
        names, self.resync_needed = self.resync_needed, set()
        return [s for s in SUBSCRIPTIONS if s["name"] in names]

    def latest(self, *, backend_connected: bool, now_s: float) -> dict:
        """The fast part of the cockpit's state: small, uploaded every few s.

        The slow part (trails, plan, hello, subscriptions) is `state()`.
        """
        cam = self.camera_header
        status = cam if cam and cam.get("type") != "camera_frame" else None
        return {
            "kind": "gui_mirror",
            "timestamp": round(now_s, 3),
            "mirror": {"backend_connected": backend_connected, "last_rx_utc_ms": self.last_rx_utc_ms},
            "frames": {k: v for k, v in self.frames.items() if k not in SLOW_STREAMS},
            "camera_status": status,
            **{k: v for k, v in self.meta.items() if k != "subscribed"},
        }

    def state_key(self) -> str:
        """Changes when hello, subscriptions, plan or unavailable streams change."""
        plan = (self.frames.get("plan") or {}).get("server_utc_ms")
        sub = json.dumps(self.meta.get("subscribed"), sort_keys=True)
        return json.dumps([bool(self.hello), (self.hello or {}).get("client_id"), sub, plan,
                           sorted(self.unavailable)], sort_keys=True)

    def state(self) -> dict:
        """The slow part: trails capped to their last `track_max` points (the
        whole track is in the Jetson's own recording), plan, hello,
        subscriptions, unavailable streams."""
        frames = {k: v for k, v in self.frames.items() if k in SLOW_STREAMS}
        for name, key in (("track", "points"), ("coverage", "segments")):
            f = frames.get(name)
            if f and len(f["payload"].get(key, [])) > self.track_max:
                kept = f["payload"][key][-self.track_max:]
                frames[name] = {**f, "payload": {**f["payload"], key: kept, "truncated": True}}
        return {
            "hello": self.hello,
            "subscribed": self.meta.get("subscribed"),
            "stream_unavailable": self.unavailable,
            "frames": frames,
        }

    def history(self, *, backend_connected: bool, now_s: float) -> dict:
        """One light history line: the current value of the small streams.

        A sample every history interval, not every change: enough to see
        where the boat went and how it was doing. The full record is on the
        Jetson.
        """
        rec: dict = {
            "kind": "gui_mirror_history",
            "timestamp": round(now_s, 3),
            "mirror": {"backend_connected": backend_connected},
            "frames": {k: self.frames[k] for k in HISTORY_STREAMS if k in self.frames},
        }
        if "alarms" in self.meta:
            rec["alarms"] = self.meta["alarms"]
        return rec

    def take_image(self) -> tuple[Optional[bytes], Optional[dict]]:
        """The newest picture, once."""
        if self.camera_new and self.camera_image:
            self.camera_new = False
            return self.camera_image, self.camera_header
        return None, None
