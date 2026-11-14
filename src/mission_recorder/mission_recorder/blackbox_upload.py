"""Which black-box files are finished, and copying them to Azure.

No ROS in here, so it can be tested without a graph.

Layout on the Jetson (one session per recorder start)::

    /data/blackbox/<UTC start, e.g. 20261114T143000Z>/
        <session>_0.mcap        5-minute chunks from `ros2 bag record -d 300`
        <session>_1.mcap
        metadata.yaml           written by rosbag2 when the recording closes
        .uploaded.json          what has reached Azure, and at which size

and in Azure (container `asket-missions`)::

    <asv>/blackbox/<session>/<file>

Rules, because the cost of getting them wrong is lost data:

* The chunk being written is never uploaded: in the active session that is
  the highest-numbered .mcap. Everything else waits ``settle_s`` after its
  last write, so a file being closed is not caught half-way.
* metadata.yaml is uploaded only once its session is no longer active, and
  again if it changes (rosbag2 rewrites it on close).
* Nothing is ever deleted, on either side. Uploaded files stay on the Jetson.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

LEDGER = ".uploaded.json"
_CHUNK = re.compile(r"_(\d+)\.mcap$")


@dataclass(frozen=True)
class Pending:
    session: str
    path: Path
    size: int
    # For files outside a black-box session (pending_tree): where the ledger
    # lives and the blob name below the prefix.
    ledger_dir: Path | None = None
    rel: str | None = None

    @property
    def blob_suffix(self) -> str:
        return self.rel if self.rel is not None else f"{self.session}/{self.path.name}"


def load_ledger(session_dir: Path) -> dict:
    try:
        return json.loads((session_dir / LEDGER).read_text())
    except (OSError, ValueError):
        return {}


def mark_uploaded(session_dir: Path, name: str, size: int) -> None:
    ledger = load_ledger(session_dir)
    ledger[name] = {"size": size, "at": time.time()}
    tmp = session_dir / (LEDGER + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=1))
    os.replace(tmp, session_dir / LEDGER)


def _chunk_index(p: Path) -> int:
    m = _CHUNK.search(p.name)
    return int(m.group(1)) if m else -1


def pending_files(root: Path, active_session: str | None, settle_s: float = 15.0,
                  now: float | None = None) -> list[Pending]:
    """Finished files not yet in Azure at their current size, oldest first."""
    now = time.time() if now is None else now
    out: list[Pending] = []
    if not root.is_dir():
        return out
    for sdir in sorted(p for p in root.iterdir() if p.is_dir()):
        active = sdir.name == active_session
        ledger = load_ledger(sdir)
        chunks = sorted(sdir.glob("*.mcap"), key=_chunk_index)
        candidates = chunks[:-1] if active else chunks
        if not active and (sdir / "metadata.yaml").is_file():
            candidates = candidates + [sdir / "metadata.yaml"]
        for f in candidates:
            try:
                st = f.stat()
            except OSError:
                continue
            if now - st.st_mtime < settle_s:
                continue
            if ledger.get(f.name, {}).get("size") == st.st_size:
                continue
            out.append(Pending(sdir.name, f, st.st_size))
    return out


def pending_tree(root: Path, settle_s: float = 60.0, skip_suffixes=(".json", ".jsonl"),
                 now: float | None = None) -> list[Pending]:
    """Every file under ``root`` (e.g. SonarView's /userdata) not yet uploaded
    at its current size and untouched for ``settle_s``. Settings and logs
    (.json/.jsonl) are skipped. A file that grows after upload is sent again."""
    now = time.time() if now is None else now
    if not root.is_dir():
        return []
    ledger = load_ledger(root)
    out = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = f.relative_to(root).as_posix()
        if f.name.startswith(".uploaded") or f.suffix in skip_suffixes:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if now - st.st_mtime < settle_s or ledger.get(rel, {}).get("size") == st.st_size:
            continue
        out.append(Pending("", f, st.st_size, ledger_dir=root, rel=rel))
    return out


class ThrottledReader:
    """File-like wrapper that caps the read rate, so a backlog upload does not
    starve the live cockpit mirror (latest.json every 3 s) on a thin link."""

    def __init__(self, f, max_bytes_per_s: float):
        self._f = f
        self._rate = max_bytes_per_s
        self._t0 = time.monotonic()
        self._n = 0

    def read(self, size: int = -1) -> bytes:
        # Always return what was asked for (the SDK signs the request with the
        # length it expects); pace by sleeping afterwards instead.
        data = self._f.read(-1 if size is None else size)
        self._n += len(data)
        if self._rate > 0:
            ahead = self._n / self._rate - (time.monotonic() - self._t0)
            if ahead > 0:
                time.sleep(ahead)
        return data

    # The Azure SDK probes these on stream inputs.
    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._n

    def __iter__(self):
        while True:
            chunk = self.read(256 * 1024)
            if not chunk:
                return
            yield chunk


def upload_one(container_client, prefix: str, item: Pending, max_kbps: float = 0.0) -> None:
    """Upload one file; raises on any failure (caller retries later)."""
    from azure.storage.blob import ContentSettings

    ctype = "text/yaml" if item.path.suffix == ".yaml" else "application/octet-stream"
    with open(item.path, "rb") as f:
        data = ThrottledReader(f, max_kbps * 1000 / 8) if max_kbps > 0 else f
        container_client.upload_blob(
            f"{prefix.rstrip('/')}/{item.blob_suffix}", data, length=item.size,
            overwrite=True, max_concurrency=1,
            content_settings=ContentSettings(content_type=ctype),
        )
    if item.ledger_dir is not None:
        mark_uploaded(item.ledger_dir, item.rel, item.size)
    else:
        mark_uploaded(item.path.parent, item.path.name, item.size)
