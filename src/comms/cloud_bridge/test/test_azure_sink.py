"""azure_sink against an in-memory stand-in for the Blob container client.

The fake enforces the two Azure behaviours the sink relies on: an append
with a wrong ``appendpos_condition`` is rejected, and properties of a
missing blob raise ``ResourceNotFoundError``.
"""
import json
import sys
import types

import pytest

# azure-storage-blob may be absent where these run; the sink only needs
# ContentSettings from it, so provide a stand-in if it is missing.
try:
    import azure.storage.blob  # noqa: F401
except ImportError:  # pragma: no cover
    blob = types.ModuleType("azure.storage.blob")
    blob.ContentSettings = lambda **kw: kw
    sys.modules.setdefault("azure", types.ModuleType("azure"))
    sys.modules.setdefault("azure.storage", types.ModuleType("azure.storage"))
    sys.modules["azure.storage.blob"] = blob

from cloud_bridge.azure_sink import AzureBlobSink, hour_path  # noqa: E402


class ResourceNotFoundError(Exception):
    pass


class AppendPositionConditionNotMet(Exception):
    pass


class FakeBlob:
    def __init__(self, store, name, fail):
        self.store, self.name, self.fail = store, name, fail

    def get_blob_properties(self):
        if self.fail["on"]:
            raise ConnectionError("offline")
        if self.name not in self.store:
            raise ResourceNotFoundError(self.name)
        return types.SimpleNamespace(size=len(self.store[self.name]))

    def create_append_blob(self, content_settings=None):
        self.store[self.name] = b""

    def append_block(self, data, appendpos_condition=None):
        if self.fail["on"]:
            raise ConnectionError("offline")
        cur = self.store[self.name]
        if appendpos_condition is not None and appendpos_condition != len(cur):
            raise AppendPositionConditionNotMet()
        self.store[self.name] = cur + bytes(data)
        if self.fail.get("lose_response"):
            self.fail["lose_response"] = False
            raise ConnectionError("response lost after the write landed")

    def upload_blob(self, data, overwrite=False, content_settings=None):
        if self.fail["on"]:
            raise ConnectionError("offline")
        self.store[self.name] = bytes(data)


class FakeContainer:
    def __init__(self):
        self.store = {}
        self.fail = {"on": False}

    def get_blob_client(self, name):
        return FakeBlob(self.store, name, self.fail)


def _pkt(seq, ts):
    return {"asv_id": "T", "seq": seq, "timestamp": ts, "nav": {"lat": -26.64, "lon": 15.16}}


def _lines(cc, name):
    return [json.loads(x) for x in cc.store[name].decode().splitlines()]


TS = 1794400000.0  # fixed hour, so file names are predictable
HP = hour_path(TS)


def test_spool_then_upload(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    for i in range(3):
        s.submit(_pkt(i, TS + i))
    assert s.upload_once()
    assert [p["seq"] for p in _lines(cc, f"T/telemetry/{HP}.jsonl")] == [0, 1, 2]
    assert json.loads(cc.store["T/latest.json"])["seq"] == 2
    assert s.status.backlog_bytes == 0


def test_offline_buffers_then_catches_up_without_duplicates(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS))
    assert s.upload_once()
    cc.fail["on"] = True
    for i in range(1, 5):
        s.submit(_pkt(i, TS + i))
    assert not s.upload_once()
    assert s.status.backlog_bytes > 0 and not s.status.connected
    cc.fail["on"] = False
    assert s.upload_once()
    assert [p["seq"] for p in _lines(cc, f"T/telemetry/{HP}.jsonl")] == [0, 1, 2, 3, 4]


def test_lost_response_does_not_duplicate(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS))
    cc.fail["lose_response"] = True
    assert not s.upload_once()  # write landed, client saw an error
    s.submit(_pkt(1, TS + 1))
    assert s.upload_once()
    assert [p["seq"] for p in _lines(cc, f"T/telemetry/{HP}.jsonl")] == [0, 1]


def test_restart_resumes_from_blob_length(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS))
    s.upload_once()
    s.submit(_pkt(1, TS + 1))  # spooled, process "dies" before upload
    s2 = AzureBlobSink(cc, str(tmp_path), "T")
    assert s2.upload_once()
    assert [p["seq"] for p in _lines(cc, f"T/telemetry/{HP}.jsonl")] == [0, 1]


def test_partial_last_line_waits(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS))
    with open(tmp_path / "telemetry" / f"{HP}.jsonl", "ab") as fh:
        fh.write(b'{"seq": 1, "half')  # writer mid-line
    s.upload_once()
    assert [p["seq"] for p in _lines(cc, f"T/telemetry/{HP}.jsonl")] == [0]


def test_image_goes_to_own_blob_and_packet_references_it(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit({**_pkt(0, TS), "image_jpeg_b64": "xxx"}, jpeg=b"\xff\xd8jpeg")
    s.upload_once()
    rec = _lines(cc, f"T/telemetry/{HP}.jsonl")[0]
    assert "image_jpeg_b64" not in rec
    assert cc.store[rec["image_blob"]] == b"\xff\xd8jpeg"
    assert s.status.pending_images == 0
    assert not list((tmp_path / "images_pending").rglob("*.jpg"))


def test_refuses_to_append_onto_longer_blob(tmp_path):
    cc = FakeContainer()
    cc.store[f"T/telemetry/{HP}.jsonl"] = b"x" * 10_000  # unrelated, longer
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS))
    assert not s.upload_once()
    assert "refusing to append" in s.status.last_error
    assert cc.store[f"T/telemetry/{HP}.jsonl"] == b"x" * 10_000


@pytest.mark.parametrize("ts,expected", [(0.0, "1970/01/01/00"), (1794400000.0, HP)])
def test_hour_path_is_utc(ts, expected):
    assert hour_path(ts) == expected


def test_past_hour_file_that_grows_is_uploaded_again(tmp_path):
    # TS is a past hour, so after the first pass its file counts as finished.
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS))
    assert s.upload_once()
    s.submit(_pkt(1, TS + 1))  # late packet for the same hour
    assert s.status.backlog_bytes == 0  # not yet refreshed
    assert s.upload_once()
    assert [p["seq"] for p in _lines(cc, f"T/telemetry/{HP}.jsonl")] == [0, 1]


def test_unencodable_packet_leaves_no_orphan_image(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    with pytest.raises(TypeError):
        s.submit({**_pkt(0, TS), "bad": object()}, jpeg=b"\xff\xd8")
    assert not list((tmp_path / "images_pending").rglob("*.jpg"))


def test_latest_names_newest_snapshot_even_without_one(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS), jpeg=b"\xff\xd8")
    s.submit(_pkt(1, TS + 1))
    s.upload_once()
    latest = json.loads(cc.store["T/latest.json"])
    assert latest["seq"] == 1 and "image_blob" not in latest
    assert latest["last_image_blob"] == f"T/images/{HP}/{int(TS * 1000)}.jpg"


def test_latest_extra_only_in_latest(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS), latest_extra={"hello": {"big": True}})
    s.upload_once()
    assert json.loads(cc.store["T/latest.json"])["hello"] == {"big": True}
    assert "hello" not in _lines(cc, f"T/telemetry/{HP}.jsonl")[0]


def test_latest_only_pass_leaves_history_for_later(tmp_path):
    cc = FakeContainer()
    s = AzureBlobSink(cc, str(tmp_path), "T")
    s.submit(_pkt(0, TS), jpeg=b"\xff\xd8")
    assert s.upload_once(history=False)
    latest = json.loads(cc.store["T/latest.json"])
    assert latest["last_image_blob"] in cc.store  # never names a missing image
    assert f"T/telemetry/{HP}.jsonl" not in cc.store
    assert s.upload_once(history=True)
    assert f"T/telemetry/{HP}.jsonl" in cc.store
