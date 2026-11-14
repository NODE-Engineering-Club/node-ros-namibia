import io
import os
import time

from mission_recorder.blackbox_upload import (
    ThrottledReader, load_ledger, mark_uploaded, pending_files,
)


def _touch(p, data=b"x", age=100.0):
    p.write_bytes(data)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_active_session_skips_chunk_being_written_and_metadata(tmp_path):
    s = tmp_path / "20261114T140000Z"
    s.mkdir()
    for i in (0, 1, 2, 10):
        _touch(s / f"{s.name}_{i}.mcap")
    _touch(s / "metadata.yaml")
    names = [p.path.name for p in pending_files(tmp_path, active_session=s.name)]
    # numeric order, not text order: _10 is the newest and is still open
    assert names == [f"{s.name}_0.mcap", f"{s.name}_1.mcap", f"{s.name}_2.mcap"]


def test_closed_session_uploads_everything_incl_metadata(tmp_path):
    s = tmp_path / "20261114T130000Z"
    s.mkdir()
    _touch(s / f"{s.name}_0.mcap")
    _touch(s / "metadata.yaml")
    _touch(s / "notes.log")
    names = [p.path.name for p in pending_files(tmp_path, active_session="other")]
    assert names == [f"{s.name}_0.mcap", "metadata.yaml"]


def test_recent_files_wait_to_settle(tmp_path):
    s = tmp_path / "a"
    s.mkdir()
    _touch(s / "a_0.mcap", age=2.0)
    assert pending_files(tmp_path, None, settle_s=15.0) == []
    assert len(pending_files(tmp_path, None, settle_s=1.0)) == 1


def test_ledger_skips_uploaded_until_size_changes(tmp_path):
    s = tmp_path / "a"
    s.mkdir()
    _touch(s / "metadata.yaml", b"v1")
    mark_uploaded(s, "metadata.yaml", 2)
    assert pending_files(tmp_path, None) == []
    assert load_ledger(s)["metadata.yaml"]["size"] == 2
    _touch(s / "metadata.yaml", b"v2-longer")
    assert [p.path.name for p in pending_files(tmp_path, None)] == ["metadata.yaml"]


def test_upload_one_names_blob_and_records_it(tmp_path):
    from mission_recorder.blackbox_upload import upload_one
    s = tmp_path / "S"
    s.mkdir()
    _touch(s / "S_0.mcap", b"abc")
    (item,) = pending_files(tmp_path, None)

    class FakeCC:
        def upload_blob(self, name, data, length, **kw):
            self.got = (name, data.read(), length)

    cc = FakeCC()
    upload_one(cc, "ASKET_02/blackbox", item)
    assert cc.got == ("ASKET_02/blackbox/S/S_0.mcap", b"abc", 3)
    assert pending_files(tmp_path, None) == []


def test_throttled_reader_caps_rate():
    r = ThrottledReader(io.BytesIO(b"x" * 20000), max_bytes_per_s=100000)
    t0 = time.monotonic()
    assert b"".join(iter(r)) == b"x" * 20000
    assert time.monotonic() - t0 >= 0.15


def test_tree_uploads_recordings_not_settings_and_resends_grown(tmp_path):
    from mission_recorder.blackbox_upload import pending_tree, upload_one
    sv = tmp_path / "sonarview"
    (sv / "Omniscan 3D").mkdir(parents=True)
    _touch(sv / "config.json")
    _touch(sv / "errlog.jsonl")
    rec = _touch(sv / "Omniscan 3D" / "2026-11-14-15-10.svlz", b"ab")
    _touch(sv / "Omniscan 3D" / "new.svlz", age=5)   # still being written
    (item,) = pending_tree(sv)
    assert item.blob_suffix == "Omniscan 3D/2026-11-14-15-10.svlz"

    class FakeCC:
        def upload_blob(self, name, data, length, **kw):
            self.name = name

    cc = FakeCC()
    upload_one(cc, "ASKET_02/sonarview", item)
    assert cc.name == "ASKET_02/sonarview/Omniscan 3D/2026-11-14-15-10.svlz"
    assert pending_tree(sv) == []
    _touch(rec, b"abcd")
    assert [p.rel for p in pending_tree(sv)] == ["Omniscan 3D/2026-11-14-15-10.svlz"]
