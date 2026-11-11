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
    def __init__(self, track_max_points: int = 5000):
        self.track_max = track_max_points
        self.hello: Optional[dict] = None
        self.meta: dict[str, dict] = {}       # subscribed / profile / alarms / notice
        self.unavailable: dict[str, dict] = {}  # stream_unavailable, by stream
        self.frames: dict[str, dict] = {}     # latest data message per stream
        self.changed: set[str] = set()        # streams/meta updated since last record
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
            self.changed.add("hello")
        elif kind in ("subscribed", "profile", "alarms", "notice"):
            self.meta[kind] = msg
            self.changed.add(kind)
        elif kind == "stream_unavailable":
            self.unavailable[msg.get("stream", "?")] = msg
            self.changed.add("stream_unavailable")
        elif kind == "data":
            name = msg.get("stream")
            if not name:
                return None
            if name in APPEND_ONLY:
                msg = self._accumulate(name, msg)
                if msg is None:
                    return None
            self.frames[name] = msg
            self.changed.add(name)
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

    def record(self, *, backend_connected: bool, now_s: float) -> tuple[dict, dict, Optional[bytes]]:
        """One history record, the latest.json extras, and a snapshot if new.

        History carries only what changed since the previous record, without
        the append-only streams (the vessel positions already are the track);
        latest.json carries everything needed to draw "now".
        """
        changed, self.changed = self.changed, set()
        rec: dict = {
            "kind": "gui_mirror",
            "timestamp": round(now_s, 3),
            "mirror": {"backend_connected": backend_connected, "last_rx_utc_ms": self.last_rx_utc_ms},
            "frames": {k: v for k, v in self.frames.items() if k in changed and k not in APPEND_ONLY},
        }
        for k in ("subscribed", "profile", "alarms", "notice"):
            if k in changed and k in self.meta:
                rec[k] = self.meta[k]
        if "stream_unavailable" in changed:
            rec["stream_unavailable"] = self.unavailable
        jpeg = None
        if self.camera_new and self.camera_image:
            jpeg, self.camera_new = self.camera_image, False
        if self.camera_header:
            rec["camera"] = self.camera_header

        frames_now = dict(self.frames)
        tr = frames_now.get("track")
        if tr and len(tr["payload"].get("points", [])) > self.track_max:
            pts = tr["payload"]["points"][-self.track_max:]
            frames_now["track"] = {**tr, "payload": {**tr["payload"], "points": pts, "truncated": True}}
        extra = {
            "hello": self.hello,
            "frames": frames_now,
            "stream_unavailable": self.unavailable,
            **{k: v for k, v in self.meta.items()},
        }
        return rec, extra, jpeg
