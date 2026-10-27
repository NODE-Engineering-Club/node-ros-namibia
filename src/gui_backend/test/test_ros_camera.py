"""The real camera path: a CompressedImage becomes the same frames SimSource
sends, and an absent or stopped camera says why instead of showing a picture.
"""

from types import SimpleNamespace

import pytest

from gui_backend.core import video_frame
from gui_backend.core.ros_camera import CompressedCamera, jpeg_size

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def jpeg(width=640, height=480):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, : width // 2] = (0, 120, 255)
    ok, data = cv2.imencode(".jpg", image)
    assert ok
    return data.tobytes()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def msg(data):
    return SimpleNamespace(format="rgb8; jpeg compressed bgr8", data=data)


def test_the_size_is_read_from_the_jpeg_header():
    assert jpeg_size(jpeg(640, 480)) == (640, 480)
    assert jpeg_size(b"not a jpeg") is None


def test_no_message_yet_is_a_status_not_a_picture():
    utc, payload = CompressedCamera().payload("full")
    assert payload["image"] == b""
    assert payload["reason"] == video_frame.REASON_NOT_STARTED


def test_full_detail_sends_the_drivers_jpeg_untouched():
    camera = CompressedCamera()
    data = jpeg()
    camera.on_message(msg(data), 1234)
    utc, payload = camera.payload("full")
    assert utc == 1234
    assert payload["image"] == data
    assert (payload["width"], payload["height"]) == (640, 480)
    assert payload["format"] == "jpeg"
    assert payload["seq"] == 1


@pytest.mark.parametrize("detail,width", [("reduced", 320), ("minimal", 160)])
def test_narrower_links_get_a_smaller_picture(detail, width):
    camera = CompressedCamera()
    data = jpeg()
    camera.on_message(msg(data), 1)
    _, payload = camera.payload(detail)
    assert payload["width"] == width
    assert payload["height"] == width * 3 // 4
    assert len(payload["image"]) < len(data)
    assert jpeg_size(payload["image"]) == (payload["width"], payload["height"])


def test_a_stopped_camera_is_reported_frozen_and_its_picture_not_resent():
    clock = Clock()
    camera = CompressedCamera(clock)
    camera.on_message(msg(jpeg()), 5000)
    clock.t += 3.5
    utc, payload = camera.payload("full")
    assert payload["image"] == b""
    assert payload["reason"] == video_frame.REASON_FROZEN
    assert payload["last_frame_utc_ms"] == 5000


def test_the_encode_is_done_once_per_frame_and_detail():
    camera = CompressedCamera()
    camera.on_message(msg(jpeg()), 1)
    first = camera.payload("minimal")[1]["image"]
    assert camera.payload("minimal")[1]["image"] is first
