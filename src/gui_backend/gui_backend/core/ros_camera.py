"""The real camera stream: ``sensor_msgs/CompressedImage`` into camera frames.

ROS-free, so it is tested without ROS. ``RosSource`` keeps the latest message
and hands it here; this decides what the ``camera`` stream sends for a detail
level — a picture, or a status saying why there is none — in exactly the
shape ``SimSource`` sends, so the hub and the panel cannot tell them apart.

Size by detail
--------------
``full`` sends the driver's JPEG as it came (no re-encode on the Jetson's
CPU). ``reduced`` and ``minimal`` are scaled down to 320x240 / 160x120 and
re-encoded at a lower quality, when OpenCV is importable (it is in the image,
``opencv-python`` in the Containerfile). Without OpenCV the original is sent:
correct, only larger. Each re-encode is cached per message, so several
browsers on the same profile cost one encode.

Topic
-----
Configured in ``topics.yaml`` (``sources.camera_image``). For the OAK-D-LR
through ``depthai_ros_driver`` that is ``/oak/rgb/image_raw/compressed`` —
check with ``ros2 topic list`` on the boat, the driver's names depend on its
version and parameters.
"""

from __future__ import annotations

import time

from . import video_frame
from .streams import DETAIL_FULL, DETAIL_MINIMAL, DETAIL_REDUCED

#: Longest side, per detail level, matching SimSource's CAMERA_SIZES.
TARGET_WIDTH = {DETAIL_FULL: None, DETAIL_REDUCED: 320, DETAIL_MINIMAL: 160}
JPEG_QUALITY = {DETAIL_REDUCED: 60, DETAIL_MINIMAL: 50}

#: A camera whose last frame is older than this is reported frozen, and its
#: picture is not sent again (the panel must not present it as current).
FROZEN_AFTER_S = 3.0


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's SOF marker, without decoding it."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0..SOF15, less DHT (C4), JPG (C8) and DAC (CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        i += 2 + length
    return None


def _format_of(msg_format: str) -> str:
    text = (msg_format or "").lower()
    return "png" if "png" in text else "jpeg"


class CompressedCamera:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._msg = None
        self._received = None
        self._utc_ms = None
        self._seq = 0
        self._cache: dict[str, dict] = {}

    def on_message(self, msg, utc_ms: int) -> None:
        self._msg = msg
        self._received = self._clock()
        self._utc_ms = utc_ms
        self._seq += 1
        self._cache = {}

    @property
    def frames_produced(self) -> int:
        return self._seq

    def payload(self, detail: str) -> tuple[int | None, dict]:
        """(source_utc_ms, payload) for the camera stream."""
        if self._msg is None:
            return None, {
                "image": b"",
                "reason": video_frame.REASON_NOT_STARTED,
                "frames_produced": 0,
                "last_frame_utc_ms": None,
            }
        if self._clock() - self._received > FROZEN_AFTER_S:
            return self._utc_ms, {
                "image": b"",
                "reason": video_frame.REASON_FROZEN,
                "frames_produced": self._seq,
                "last_frame_utc_ms": self._utc_ms,
            }
        if detail not in self._cache:
            self._cache[detail] = self._encode(detail)
        return self._utc_ms, dict(self._cache[detail], seq=self._seq)

    def _encode(self, detail: str) -> dict:
        data = bytes(self._msg.data)
        image_format = _format_of(getattr(self._msg, "format", ""))
        size = jpeg_size(data) if image_format == "jpeg" else None
        width, height = size or (0, 0)
        target = TARGET_WIDTH.get(detail)
        if target and (not width or width > target):
            smaller = _reencode(data, target, JPEG_QUALITY[detail])
            if smaller is not None:
                data, width, height = smaller
                image_format = "jpeg"
        return {"image": data, "width": width, "height": height, "format": image_format}


def _reencode(data: bytes, target_width: int, quality: int):
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    scale = target_width / float(width)
    if scale < 1.0:
        image = cv2.resize(
            image, (target_width, max(1, round(height * scale))), interpolation=cv2.INTER_AREA
        )
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return None
    h, w = image.shape[:2]
    return encoded.tobytes(), w, h
