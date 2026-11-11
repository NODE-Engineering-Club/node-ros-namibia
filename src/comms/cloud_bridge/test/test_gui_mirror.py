import json
import struct

from cloud_bridge.gui_mirror import HISTORY_STREAMS, MirrorState


def data(stream, payload, t=1000):
    return {"type": "data", "stream": stream, "source_utc_ms": t, "server_utc_ms": t,
            "detail": "full", "payload": payload}


def video(header, image=b""):
    blob = json.dumps(header).encode()
    return struct.pack(">I", len(blob)) + blob + image


def test_ping_answered_and_nothing_else_sent():
    m = MirrorState()
    assert m.handle({"type": "ping", "id": 7}, 1) == {"type": "pong", "id": 7}
    assert m.handle(data("vessel", {"lat": 1}), 2) is None


def test_track_increments_spliced_into_latest():
    m = MirrorState()
    m.handle({"type": "hello"}, 0)
    m.handle(data("track", {"from": 0, "total": 2, "points": [[1, 1], [2, 2]]}), 1)
    m.handle(data("track", {"from": 2, "total": 3, "points": [[3, 3]]}), 2)
    assert "track" not in m.latest(backend_connected=True, now_s=10)["frames"]
    p = m.state()["frames"]["track"]["payload"]
    assert p["points"] == [[1, 1], [2, 2], [3, 3]] and p["from"] == 0


def test_track_gap_requests_resync_instead_of_splicing():
    m = MirrorState()
    m.handle(data("track", {"from": 0, "total": 1, "points": [[1, 1]]}), 1)
    m.handle(data("track", {"from": 5, "total": 6, "points": [[6, 6]]}), 2)
    assert m.frames["track"]["payload"]["points"] == [[1, 1]]
    assert [s["name"] for s in m.take_resyncs()] == ["track"]


def test_latest_is_light_track_tail_only():
    m = MirrorState(track_max_points=3)
    m.handle(data("track", {"from": 0, "total": 5, "points": [[i, i] for i in range(5)]}), 1)
    p = m.state()["frames"]["track"]["payload"]
    assert p["points"] == [[2, 2], [3, 3], [4, 4]] and p["truncated"]
    assert len(m.frames["track"]["payload"]["points"]) == 5  # nothing lost locally


def test_history_keeps_small_streams_and_alarms_only():
    m = MirrorState()
    for name in ("vessel", "pico", "track", "lidar", "plan", "diagnostics"):
        m.handle(data(name, {"x": 1} if name != "track" else {"from": 0, "points": []}), 1)
    m.handle({"type": "alarms", "active": []}, 1)
    h = m.history(backend_connected=True, now_s=1)
    assert set(h["frames"]) == {"vessel", "pico"} and set(h["frames"]) <= set(HISTORY_STREAMS)
    assert h["alarms"]["active"] == []


def test_image_taken_once_and_status_kept():
    m = MirrorState()
    m.handle_video(video({"type": "camera_frame", "seq": 1, "source_utc_ms": 5}, b"JPG"), 1)
    jpeg, header = m.take_image()
    assert jpeg == b"JPG" and header["seq"] == 1
    assert m.take_image() == (None, None)
    m.handle_video(video({"type": "camera_status", "reason": "camera_frozen"}), 2)
    assert m.latest(backend_connected=True, now_s=2)["camera_status"]["reason"] == "camera_frozen"


def test_malformed_video_ignored():
    m = MirrorState()
    m.handle_video(b"\x00\x00\xff\xffgarbage", 1)
    assert m.camera_header is None


def test_state_key_changes_with_hello_and_plan_not_with_position():
    m = MirrorState()
    m.handle({"type": "hello", "client_id": "a"}, 0)
    k1 = m.state_key()
    m.handle(data("vessel", {"lat": 2}, t=5), 1)
    assert m.state_key() == k1
    m.handle(data("plan", {"lines": []}, t=6), 2)
    assert m.state_key() != k1
