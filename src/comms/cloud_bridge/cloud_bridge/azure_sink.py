"""Store-and-forward of telemetry packets to Azure Blob Storage.

No ROS in this file, so it can be tested against Azurite or a fake client
without a running graph.

Layout in the container (default ``telemetry``), one prefix per boat::

    <asv_id>/latest.json                         newest packet + "last_image_blob", overwritten
    <asv_id>/telemetry/YYYY/MM/DD/HH.jsonl       one JSON packet per line (append blob)
    <asv_id>/images/YYYY/MM/DD/HH/<ms>.jpg       camera snapshots, referenced
                                                 from a packet's "image_blob"

Hours are UTC, taken from the packet's own ``timestamp``.

Why a disk spool and not a direct upload per packet:

* The boat's internet goes over a 1.3 km Wi-Fi hop and then the shore
  uplink. Either can drop for minutes. ``submit()`` only writes to local
  disk, so the ROS timer never waits on the network, and nothing is lost
  while the link is down: the uploader appends whatever the blob does not
  have yet once it comes back.
* The local JSONL files are the source of truth. On the first touch of a
  file in a process, the uploader asks Azure how many bytes the blob already
  holds and continues from there. A restart, or a lost upload response,
  therefore neither duplicates nor skips lines, and there is no offsets file
  to get out of step.
* One append per file per cycle (default every 2 s) instead of one write
  per packet keeps the transaction count, which is what Azure bills, low.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

#: Azure's limit for a single append block.
MAX_APPEND_BYTES = 4 * 1024 * 1024


def hour_path(ts: float) -> str:
    """``YYYY/MM/DD/HH`` in UTC for a unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y/%m/%d/%H")


@dataclass
class SinkStatus:
    enabled: bool = False
    connected: bool = False
    last_upload_utc: Optional[float] = None
    last_error: str = ""
    backlog_bytes: int = 0
    pending_images: int = 0
    packets_spooled: int = 0
    bytes_uploaded: int = 0

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "last_upload_utc": self.last_upload_utc,
            "last_error": self.last_error,
            "backlog_kb": round(self.backlog_bytes / 1024.0, 1),
            "pending_images": self.pending_images,
            "packets_spooled": self.packets_spooled,
            "kb_uploaded": round(self.bytes_uploaded / 1024.0, 1),
        }


@dataclass
class _FileState:
    uploaded: int  # bytes of the local file already in the blob
    verified: bool = True  # False after an error: re-read the blob length first
    remote: str = ""  # blob path (under the asv prefix) this local file goes to


class AzureBlobSink:
    """Spool packets to disk; a background thread appends them to Azure."""

    def __init__(
        self,
        container_client,
        spool_dir: str,
        asv_id: str,
        upload_interval_s: float = 2.0,
        keep_local_images: bool = False,
        log: Optional[Callable[[str], None]] = None,
        latest_interval_s: Optional[float] = None,
    ):
        self._cc = container_client
        self._spool = Path(spool_dir)
        self._asv = asv_id.strip("/")
        self._interval = upload_interval_s
        # latest.json is what a live viewer waits on; history is not. They
        # can run on different clocks so "live" costs one write a second
        # without paying for history appends at the same rate.
        self._latest_interval = latest_interval_s or upload_interval_s
        self._last_history = 0.0
        self._keep_images = keep_local_images
        self._log = log or (lambda _m: None)

        self._tele_dir = self._spool / "telemetry"
        self._img_pending = self._spool / "images_pending"
        self._img_done = self._spool / "images"
        for d in (self._tele_dir, self._img_pending):
            d.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._latest: Optional[bytes] = None
        self._latest_dirty = False
        self._last_image_blob: Optional[str] = None
        self._aux: dict[str, bytes] = {}       # name -> body, waiting to upload
        self._files: dict[str, _FileState] = {}
        self._finished: dict[str, int] = {}  # past hours fully uploaded -> size then
        self.status = SinkStatus(enabled=True)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- producer side (ROS timer thread) ---------------------------------

    def submit(self, packet: dict, jpeg: Optional[bytes] = None,
               latest_extra: Optional[dict] = None) -> None:
        """Write one packet (and optionally its snapshot) to the local spool.

        Never touches the network. ``packet`` is not modified; a copy carries
        the ``image_blob`` reference when a snapshot is given.
        ``latest_extra`` is merged into latest.json only, not the history:
        for state that is large and rarely changes (a full track, the
        server's hello) and is only needed to draw "now".
        """
        ts = float(packet.get("timestamp") or time.time())
        hp = hour_path(ts)
        rec = dict(packet)
        rec.pop("image_jpeg_b64", None)  # images live in their own blobs
        rel = f"{hp}/{int(ts * 1000)}.jpg" if jpeg else None
        if rel:
            rec["image_blob"] = f"{self._asv}/images/{rel}"
        # Serialise first: a packet that cannot be encoded must not leave an
        # image behind that no packet points at.
        line = (json.dumps(rec, separators=(",", ":")) + "\n").encode()
        if rel:
            dst = self._img_pending / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".part")
            tmp.write_bytes(jpeg)
            os.replace(tmp, dst)  # the uploader never sees half a file

        f = self._tele_dir / f"{hp}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(f, "ab") as fh:
                fh.write(line)
            if rel:
                self._last_image_blob = rec["image_blob"]
            # latest.json also names the newest snapshot, so the live view
            # has an image even when this packet did not carry one.
            self._latest = json.dumps(
                {**rec, **(latest_extra or {}), "last_image_blob": self._last_image_blob},
                separators=(",", ":")).encode()
            self._latest_dirty = True
            self.status.packets_spooled += 1

    def set_latest(self, packet: dict, latest_extra: Optional[dict] = None) -> None:
        """Replace what latest.json will say, without adding a history line.

        For a live view that refreshes more often than history is worth
        keeping: the boat's full record stays on the Jetson, the cloud keeps
        only a light history (see gui_mirror_node).
        """
        body = json.dumps(
            {**packet, **(latest_extra or {}), "last_image_blob": self._last_image_blob},
            separators=(",", ":")).encode()
        with self._lock:
            self._latest = body
            self._latest_dirty = True

    def set_aux(self, name: str, doc: dict) -> None:
        """Another small JSON blob next to latest.json (e.g. state.json).

        Uploaded, gzip-encoded, before latest.json on the next pass, so
        latest.json never refers to a version not uploaded yet.
        """
        body = json.dumps(doc, separators=(",", ":")).encode()
        with self._lock:
            self._aux[name] = body

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="azure_sink", daemon=True)
        self._thread.start()

    def stop(self, flush: bool = True) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10.0)
        if flush:
            self.upload_once()

    def _run(self) -> None:
        period = min(self._interval, self._latest_interval)
        backoff = period
        while not self._stop.wait(backoff):
            history_due = time.monotonic() - self._last_history >= self._interval - 1e-3
            ok = self.upload_once(history=history_due)
            if ok and history_due:
                self._last_history = time.monotonic()
            backoff = period if ok else min(max(backoff * 2, 5.0), 60.0)

    # -- uploader ---------------------------------------------------------

    def upload_once(self, history: bool = True) -> bool:
        """One pass: images, latest.json, then (if ``history``) the appends.

        Images go before latest.json on every pass, because latest.json names
        the newest snapshot and must never point at a blob not uploaded yet.
        """
        try:
            self._upload_images()
            self._upload_aux()
            self._upload_latest()
        except Exception as exc:  # noqa: BLE001 — network, auth, throttling: retry later
            self.status.connected = False
            self.status.last_error = f"{type(exc).__name__}: {exc}"[:300]
            self._log(f"Azure upload failed, will retry: {self.status.last_error}")
            # Doubt what we believe each blob holds: if an append landed but
            # its response was lost, the next pass re-reads the true length
            # instead of failing the position check forever.
            for st in self._files.values():
                st.verified = False
            self._refresh_backlog()
            return False
        self.status.connected = True
        self.status.last_error = ""
        self.status.last_upload_utc = round(time.time(), 3)
        if history:
            # Separate from the live files on purpose: a problem with the
            # history (seen 2026-11-11: a wiped spool) must not hold back
            # latest.json, which is what a viewer is waiting for.
            try:
                self._upload_telemetry()
            except Exception as exc:  # noqa: BLE001
                self.status.last_error = f"history: {type(exc).__name__}: {exc}"[:300]
                self._log(f"Azure history upload failed, will retry: {self.status.last_error}")
                for st in self._files.values():
                    st.verified = False
        self._refresh_backlog()
        return True

    def _blob(self, rel: str) -> str:
        return f"{self._asv}/{rel}"

    def _upload_telemetry(self) -> None:
        current = hour_path(time.time())
        for f in sorted(self._tele_dir.rglob("*.jsonl")):
            rel = f.relative_to(self._tele_dir).as_posix()
            size = f.stat().st_size
            if self._finished.get(rel) == size:
                continue
            # A finished file that grew (a packet stamped 12:59:59 written
            # after 13:00) goes back to normal handling.
            self._finished.pop(rel, None)
            st = self._files.get(rel)
            if st is None or not st.verified:
                st = self._open_remote(st.remote if st and st.remote else f"telemetry/{rel}")
                self._files[rel] = st
            if st.uploaded > size:
                # The blob is longer than the local file: the spool was wiped
                # or replaced. Never append on top of unrelated bytes, and
                # never stop recording either: keep what is in Azure as it is
                # and continue this hour in a new blob next to it.
                stem = f"telemetry/{rel[:-len('.jsonl')]}"
                new = f"{stem}.{int(time.time())}.jsonl"
                self._log(f"{self._blob('telemetry/' + rel)} has {st.uploaded} bytes, local spool "
                          f"only {size}: continuing in {self._blob(new)}")
                st = self._open_remote(new)
                self._files[rel] = st
            if st.uploaded < size:
                self._append_lines(f, st.remote, st, size)
            if st.uploaded == size and not rel.startswith(current):
                self._finished[rel] = size
                self._files.pop(rel, None)

    def _open_remote(self, rel: str) -> _FileState:
        bc = self._cc.get_blob_client(self._blob(rel))
        try:
            props = bc.get_blob_properties()
            return _FileState(uploaded=int(props.size), remote=rel)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "ResourceNotFoundError":
                raise
        bc.create_append_blob(content_settings=_content("application/x-ndjson"))
        return _FileState(uploaded=0, remote=rel)

    def _append_lines(self, f: Path, rel: str, st: _FileState, size: int) -> None:
        bc = self._cc.get_blob_client(self._blob(rel))
        with open(f, "rb") as fh:
            fh.seek(st.uploaded)
            data = fh.read(size - st.uploaded)
        # Only whole lines: submit() may be mid-write on the last one.
        end = data.rfind(b"\n") + 1
        data = data[:end]
        pos = 0
        while pos < len(data):
            chunk = data[pos:pos + MAX_APPEND_BYTES]
            if len(chunk) == MAX_APPEND_BYTES:
                chunk = chunk[: chunk.rfind(b"\n") + 1] or chunk
            # appendpos_condition: Azure rejects the append if the blob is
            # not exactly as long as we think, so a retried request after a
            # lost response can never write the same lines twice.
            bc.append_block(chunk, appendpos_condition=st.uploaded)
            st.uploaded += len(chunk)
            pos += len(chunk)
            self.status.bytes_uploaded += len(chunk)

    def _upload_images(self) -> None:
        for img in sorted(self._img_pending.rglob("*.jpg")):
            rel = img.relative_to(self._img_pending).as_posix()
            data = img.read_bytes()
            self._cc.get_blob_client(self._blob(f"images/{rel}")).upload_blob(
                data, overwrite=True, content_settings=_content("image/jpeg"),
            )
            self.status.bytes_uploaded += len(data)
            if self._keep_images:
                dst = self._img_done / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(img, dst)
            else:
                img.unlink()

    def _upload_aux(self) -> None:
        import gzip
        with self._lock:
            pending, self._aux = self._aux, {}
        try:
            for name, body in pending.items():
                self._cc.get_blob_client(self._blob(name)).upload_blob(
                    gzip.compress(body, compresslevel=6), overwrite=True,
                    content_settings=_content("application/json", cache="no-cache, max-age=0",
                                              encoding="gzip"),
                )
        except Exception:
            with self._lock:
                for name, body in pending.items():
                    self._aux.setdefault(name, body)  # a newer version wins
            raise

    def _upload_latest(self) -> None:
        with self._lock:
            if not self._latest_dirty or self._latest is None:
                return
            body = self._latest
            self._latest_dirty = False
        # Stored gzip-encoded: JSON shrinks 5-8x, which is most of the boat's
        # uplink use, and every browser decodes Content-Encoding: gzip on its
        # own. (History stays plain: appended blocks would make one gzip file
        # of many members, which not every reader accepts.)
        import gzip
        try:
            self._cc.get_blob_client(self._blob("latest.json")).upload_blob(
                gzip.compress(body, compresslevel=6), overwrite=True,
                content_settings=_content("application/json", cache="no-cache, max-age=0",
                                          encoding="gzip"),
            )
        except Exception:
            with self._lock:
                self._latest_dirty = True
            raise

    def _refresh_backlog(self) -> None:
        backlog = 0
        for f in self._tele_dir.rglob("*.jsonl"):
            rel = f.relative_to(self._tele_dir).as_posix()
            size = f.stat().st_size
            if self._finished.get(rel) == size:
                continue
            st = self._files.get(rel)
            backlog += size - (st.uploaded if st else self._finished.get(rel, 0))
        self.status.backlog_bytes = max(backlog, 0)
        self.status.pending_images = sum(1 for _ in self._img_pending.rglob("*.jpg"))


def _content(ctype: str, cache: Optional[str] = None, encoding: Optional[str] = None):
    from azure.storage.blob import ContentSettings
    return ContentSettings(content_type=ctype, cache_control=cache, content_encoding=encoding)


def load_connection_string(path: str) -> Optional[str]:
    """``AZURE_STORAGE_CONNECTION_STRING`` wins; else the first line of ``path``."""
    env = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if env:
        return env
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
        return text or None
    return None


def make_sink(conn_str: str, container: str, **kwargs) -> AzureBlobSink:
    from azure.storage.blob import BlobServiceClient
    # Fail fast: the SDK's default retry policy keeps one call alive for
    # minutes, which froze the status at "connected" through a whole outage.
    # The sink's own loop already retries with backoff.
    svc = BlobServiceClient.from_connection_string(
        conn_str, retry_total=1, retry_backoff_factor=1, connection_timeout=5, read_timeout=30,
    )
    cc = svc.get_container_client(container)
    try:
        cc.create_container()
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "ResourceExistsError":
            raise
    return AzureBlobSink(cc, **kwargs)
