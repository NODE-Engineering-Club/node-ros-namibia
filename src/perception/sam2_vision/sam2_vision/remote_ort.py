#!/usr/bin/env python3
"""ONNX Runtime on the Jetson's GPU, from the CPU-only ROS container.

Why this exists
---------------
The Jetson runs JetPack 5.1.2 (CUDA 11.4, Ubuntu 20.04); the ROS stack runs
in a Jazzy container (Ubuntu 24.04, Python 3.12). No GPU build of ONNX Runtime
exists for that pair, so the ROS container can only use the CPU (SAM 2: ~30 s
a frame under load). NVIDIA's own JetPack 5 image (dustynv/onnxruntime:r35.4.1)
does have CUDA/TensorRT ONNX Runtime, but no ROS 2 Jazzy.

So the heavy ``session.run()`` calls are sent to a small server in that image,
over a local TCP socket. Nothing else moves: topics, nodes and the GUI stay in
the ROS container.

Two halves, one file (no ROS, numpy + onnxruntime only, Python 3.8+):

* ``python3 remote_ort.py serve [--port 7090]`` — in the GPU container.
* ``HybridSession`` — in the ROS container, a drop-in for
  ``ort.InferenceSession.run``. It uses the GPU server when it answers and
  falls back to a local CPU session when it does not (not started, crashed,
  restarting), retrying the server every few seconds. Losing the GPU makes
  segmentation slow, never absent.

Wire format: ``[4-byte BE header length][header JSON]`` then, per array,
``[8-byte BE length][.npy bytes]`` (``allow_pickle=False``: the server never
unpickles anything).
"""

from __future__ import annotations

import io
import json
import os
import socket
import socketserver
import struct
import sys
import threading
import time

import numpy as np

DEFAULT_PORT = 7090


# -- framing ----------------------------------------------------------------

def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return bytes(buf)


def send_msg(sock, header, arrays=()):
    header = dict(header, n_arrays=len(arrays))
    hb = json.dumps(header).encode()
    parts = [struct.pack(">I", len(hb)), hb]
    for a in arrays:
        bio = io.BytesIO()
        np.lib.format.write_array(bio, np.ascontiguousarray(a), allow_pickle=False)
        b = bio.getvalue()
        parts += [struct.pack(">Q", len(b)), b]
    sock.sendall(b"".join(parts))


def recv_msg(sock):
    (hl,) = struct.unpack(">I", _recv_exact(sock, 4))
    if hl > 1 << 20:
        raise ValueError("header too large")
    header = json.loads(_recv_exact(sock, hl))
    arrays = []
    for _ in range(int(header.get("n_arrays", 0))):
        (al,) = struct.unpack(">Q", _recv_exact(sock, 8))
        if al > 1 << 31:
            raise ValueError("array too large")
        arrays.append(np.lib.format.read_array(io.BytesIO(_recv_exact(sock, al)), allow_pickle=False))
    return header, arrays


# -- server (GPU container) -------------------------------------------------

def _providers():
    import onnxruntime as ort

    avail = ort.get_available_providers()
    prov = []
    if os.environ.get("REMOTE_ORT_TRT") == "1" and "TensorrtExecutionProvider" in avail:
        cache = os.environ.get("REMOTE_ORT_TRT_CACHE", "/ws/models/trt_cache")
        os.makedirs(cache, exist_ok=True)
        prov.append(("TensorrtExecutionProvider", {
            "trt_fp16_enable": True, "trt_engine_cache_enable": True, "trt_engine_cache_path": cache,
        }))
    if "CUDAExecutionProvider" in avail:
        prov.append("CUDAExecutionProvider")
    prov.append("CPUExecutionProvider")
    return prov


class _Sessions:
    def __init__(self):
        self._lock = threading.Lock()
        self._s = {}

    def get(self, path):
        with self._lock:
            if path not in self._s:
                import onnxruntime as ort

                if not os.path.isfile(path):
                    raise FileNotFoundError(path)
                so = ort.SessionOptions()
                so.log_severity_level = 3
                # Explicit: ORT's default pins threads, which fails in a container cpuset.
                so.intra_op_num_threads = int(os.environ.get("REMOTE_ORT_THREADS", "4"))
                so.inter_op_num_threads = 1
                t0 = time.monotonic()
                s = ort.InferenceSession(path, so, providers=_providers())
                print(f"loaded {path} on {s.get_providers()[0]} in {time.monotonic() - t0:.1f} s", flush=True)
                self._s[path] = (s, threading.Lock())
            return self._s[path]


SESSIONS = _Sessions()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        while True:
            try:
                header, arrays = recv_msg(sock)
            except (ConnectionError, OSError):
                return
            try:
                op = header.get("op")
                if op == "ping":
                    import onnxruntime as ort

                    send_msg(sock, {"ok": True, "providers": ort.get_available_providers()})
                elif op == "load":
                    s, _ = SESSIONS.get(header["model"])
                    send_msg(sock, {"ok": True, "provider": s.get_providers()[0]})
                elif op == "run":
                    s, lock = SESSIONS.get(header["model"])
                    feeds = dict(zip(header["inputs"], arrays))
                    t0 = time.monotonic()
                    with lock:
                        out = s.run(header.get("outputs"), feeds)
                    send_msg(sock, {"ok": True, "run_s": time.monotonic() - t0}, out)
                else:
                    send_msg(sock, {"ok": False, "error": f"unknown op {op!r}"})
            except Exception as exc:  # noqa: BLE001 — report, keep serving
                send_msg(sock, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port=DEFAULT_PORT, host="127.0.0.1"):
    import onnxruntime as ort

    print(f"remote_ort: onnxruntime {ort.__version__}, providers {ort.get_available_providers()}, "
          f"listening on {host}:{port}", flush=True)
    with _Server((host, port), _Handler) as srv:
        srv.serve_forever()


# -- client (ROS container) -------------------------------------------------

class HybridSession:
    """``run()`` on the GPU server if it answers, else on a local CPU session.

    ``gpu_model`` and ``cpu_model`` may differ: the int8 SAM 2 encoder is the
    right choice on the CPU and a poor one on CUDA, where the fp32 one wins.
    """

    RETRY_S = 5.0

    def __init__(self, cpu_model, gpu_model=None, threads=6, addr=None, log=print, timeout_s=30.0):
        self.cpu_model = cpu_model
        self.gpu_model = gpu_model or cpu_model
        self.threads = int(threads)
        self.addr = addr  # "host:port" or None/"" = CPU only
        self.log = log
        self.timeout_s = timeout_s
        self._sock = None
        self._local = None
        self._next_try = 0.0
        self.backend = "CPU"
        self.last_run_s = 0.0
        if self.addr:
            self._connect(quiet=False)

    # The local session is built only when it is first needed, so a healthy
    # GPU path does not also hold every model in CPU memory.
    def _local_session(self):
        if self._local is None:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = self.threads
            so.inter_op_num_threads = 1
            so.log_severity_level = 3
            self._local = ort.InferenceSession(self.cpu_model, so, providers=["CPUExecutionProvider"])
        return self._local

    def _connect(self, quiet=True):
        host, _, port = self.addr.rpartition(":")
        try:
            sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=2.0)
            sock.settimeout(300.0)  # first load can build CUDA/TensorRT kernels
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            send_msg(sock, {"op": "load", "model": self.gpu_model})
            h, _ = recv_msg(sock)
            if not h.get("ok"):
                raise RuntimeError(h.get("error"))
            sock.settimeout(self.timeout_s)
            self._sock = sock
            self.backend = "GPU" if "CUDA" in h.get("provider", "") or "Tensorrt" in h.get("provider", "") \
                else "remote CPU"
            self.log(f"{os.path.basename(self.gpu_model)}: using {h.get('provider')} via {self.addr}")
            return True
        except Exception as exc:  # noqa: BLE001
            self._drop()
            if not quiet:
                self.log(f"{os.path.basename(self.gpu_model)}: GPU server {self.addr} not available ({exc}); CPU")
            self._next_try = time.monotonic() + self.RETRY_S
            return False

    def _drop(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.backend = "CPU"

    def run(self, output_names, feeds):
        t0 = time.monotonic()
        if self.addr and self._sock is None and time.monotonic() >= self._next_try:
            self._connect()
        if self._sock is not None:
            try:
                names = list(feeds)
                send_msg(self._sock, {"op": "run", "model": self.gpu_model, "inputs": names,
                                      "outputs": output_names}, [feeds[n] for n in names])
                h, out = recv_msg(self._sock)
                if not h.get("ok"):
                    raise RuntimeError(h.get("error"))
                self.last_run_s = time.monotonic() - t0
                return out
            except Exception as exc:  # noqa: BLE001 — server gone: fall back, retry later
                self.log(f"{os.path.basename(self.gpu_model)}: GPU server lost ({exc}); CPU until it is back")
                self._drop()
                self._next_try = time.monotonic() + self.RETRY_S
        out = self._local_session().run(output_names, feeds)
        self.last_run_s = time.monotonic() - t0
        return out


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else DEFAULT_PORT
        serve(port)
    else:
        sys.exit("usage: remote_ort.py serve [--port 7090]")
