import json
import struct

from cloud_bridge.gui_mirror import MirrorState


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


def test_track_increments_spliced_and_history_omits_it():
    m = MirrorState()
    m.handle({"type": "hello"}, 0)
    m.handle(data("track", {"from": 0, "total": 2, "points": [[1, 1], [2, 2]]}), 1)
    m.handle(data("track", {"from": 2, "total": 3, "points": [[3, 3]]}), 2)
    m.handle(data("vessel", {"lat": 1}), 3)
    rec, extra, _ = m.record(backend_connected=True, now_s=10)
    assert "track" not in rec["frames"] and "vessel" in rec["frames"]
    assert extra["frames"]["track"]["payload"]["points"] == [[1, 1], [2, 2], [3, 3]]
    assert extra["frames"]["track"]["payload"]["from"] == 0


def test_track_gap_requests_resync_instead_of_splicing():
    m = MirrorState()
    m.handle(data("track", {"from": 0, "total": 1, "points": [[1, 1]]}), 1)
    m.handle(data("track", {"from": 5, "total": 6, "points": [[6, 6]]}), 2)
    assert m.frames["track"]["payload"]["points"] == [[1, 1]]
    assert [s["name"] for s in m.take_resyncs()] == ["track"]


def test_history_only_carries_changes():
    m = MirrorState()
    m.handle(data("vessel", {"lat": 1}), 1)
    m.handle({"type": "alarms", "active": []}, 1)
    rec1, _, _ = m.record(backend_connected=True, now_s=1)
    rec2, extra2, _ = m.record(backend_connected=True, now_s=2)
    assert "vessel" in rec1["frames"] and "alarms" in rec1
    assert rec2["frames"] == {} and "alarms" not in rec2
    assert extra2["frames"]["vessel"]["payload"] == {"lat": 1} and extra2["alarms"]["active"] == []


def test_camera_frame_then_status_keeps_picture():
    m = MirrorState()
    m.handle_video(video({"type": "camera_frame", "seq": 1, "source_utc_ms": 5}, b"JPG"), 1)
    rec, _, jpeg = m.record(backend_connected=True, now_s=1)
    assert jpeg == b"JPG" and rec["camera"]["seq"] == 1
    m.handle_video(video({"type": "camera_status", "reason": "camera_frozen"}), 2)
    rec, _, jpeg = m.record(backend_connected=True, now_s=2)
    assert jpeg is None and rec["camera"]["reason"] == "camera_frozen"


def test_track_truncated_in_latest():
    m = MirrorState(track_max_points=3)
    m.handle(data("track", {"from": 0, "total": 5, "points": [[i, i] for i in range(5)]}), 1)
    _, extra, _ = m.record(backend_connected=True, now_s=1)
    p = extra["frames"]["track"]["payload"]
    assert p["points"] == [[2, 2], [3, 3], [4, 4]] and p["truncated"]


def test_malformed_video_ignored():
    m = MirrorState()
    m.handle_video(b"\x00\x00\xff\xffgarbage", 1)
    assert m.camera_header is None
