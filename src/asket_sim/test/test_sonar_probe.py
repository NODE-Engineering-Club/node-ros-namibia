"""The sonar probe, against a fake sonar.

``scripts/sonar_probe.py`` is the first thing anybody runs on a bench, and it
is the one tool that has to work when nothing else does — so it is the one that
must not need hardware to be trusted.

Two kinds of fake are used, for the two transports the probe now knows about:

* ``FakeSonarServer`` (UDP), which puts real Ping Protocol frames on a real
  socket — the probe's ``--udp`` path, and what ``asket_sim`` serves;
* a scripted TCP listener, for the path that matters. On 2026-10-28 the sonar
  was measured to answer on **TCP 62312 only**: 51200 and 7077 were closed.

What these prove is that the probe reads a live sonar correctly, reports an
absent one honestly, and — the case that actually happened — reports a
connection that opens and stays silent as exactly that. What they cannot prove
is what a real Omniscan 3D puts on TCP 62312, because the transducer was in air
and nothing was sent. See docs/SONAR_BRINGUP.md §3.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from asket_sim.core.fake_sonar_server import FakeSonarServer  # noqa: E402
from asket_sim.core.world import SimWorld, WorldConfig  # noqa: E402

sonar_probe = pytest.importorskip("sonar_probe")


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def fake_sonar():
    port = _free_port()
    server = FakeSonarServer(world=SimWorld(WorldConfig()), host="127.0.0.1", port=port)
    server.start()
    time.sleep(0.2)
    try:
        yield server, port
    finally:
        server.stop()


def test_the_probe_sees_a_live_sonars_point_sets(fake_sonar):
    """The whole point: 3104 arriving means the data path is real."""
    _server, port = fake_sonar
    seen = sonar_probe.listen_udp("127.0.0.1", port, seconds=4.0, bind_port=_free_port())
    assert seen.get(3104), f"no OS3D_POINT_SET, saw {seen}"


def test_the_probe_names_the_messages_it_saw(fake_sonar):
    """A bare count is useless at night; the operator needs the names."""
    _server, port = fake_sonar
    seen = sonar_probe.listen_udp("127.0.0.1", port, seconds=4.0, bind_port=_free_port())
    for message_id in seen:
        assert message_id in sonar_probe.MESSAGE_NAMES, (
            f"message {message_id} would print as 'unknown'"
        )
    assert sonar_probe.MESSAGE_NAMES[3104] == "OS3D_POINT_SET"


def test_silence_is_reported_as_silence_not_as_a_crash():
    """A sonar that is not there is the expected case on a bench, not an error.

    The probe must come back with an empty result and a usable exit code rather
    than a traceback — somebody reading a stack trace at 22:00 concludes the
    tool is broken, and stops using the one thing that would have told them the
    cable was out.
    """
    seen = sonar_probe.listen_udp("127.0.0.1", _free_port(), seconds=1.0,
                                  bind_port=_free_port())
    assert seen == {}


def test_a_missing_ping_binary_does_not_condemn_the_sonar(monkeypatch):
    """Containers often have no iputils. That is not the sonar's fault.

    check_ping returns None — "I could not tell" — and main() must then fall
    through to the data checks instead of stopping at step 1. Reporting "not
    reachable" because ping is absent would send somebody to check a cable that
    is fine.
    """
    def _no_ping(*args, **kwargs):
        raise FileNotFoundError("ping")

    monkeypatch.setattr(sonar_probe.subprocess, "run", _no_ping)
    assert sonar_probe.check_ping("127.0.0.1") is None


def test_the_ports_the_probe_knows_about():
    """62312 is where the sonar answered; the other two are what we and
    Cerulean respectively used to assume, both measured closed. The probe keeps
    checking them, because a firmware change would show up there first."""
    assert sonar_probe.DEFAULT_DATA_PORT == 62312
    assert sonar_probe.DOCUMENTED_TCP_PORT == 51200
    assert sonar_probe.SONARLINK_PORT == 7077


def test_the_default_host_is_the_measured_one():
    assert sonar_probe.DEFAULT_SONAR_HOST == "192.168.2.21"


def test_a_closed_tcp_port_is_a_clean_false(fake_sonar):
    _server, port = fake_sonar
    assert sonar_probe.check_tcp("127.0.0.1", port) is False


# -- the TCP path, which is the one the hardware actually uses -----------


class _TcpFake:
    """A TCP listener that sends a fixed script and then stays open, silent."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(2)
        self._listener.settimeout(0.3)
        self.port = self._listener.getsockname()[1]
        self.bytes_from_client = bytearray()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(0.2)
            try:
                for chunk in self.chunks:
                    conn.sendall(chunk)
                while not self._stop.is_set():
                    try:
                        data = conn.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    self.bytes_from_client += data
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        try:
            self._listener.close()
        except OSError:
            pass


def _a_point_set() -> bytes:
    from omniscan_bridge.core import ping_protocol as pp

    return pp.encode_point_set(
        pp.PointSet(
            ping_number=1,
            speed_of_sound=1500.0,
            utc_msec=1_700_000_000_000,
            points=[pp.Point(angle_rad=0.0, tof_s=0.004, power=40.0, pt_type=1)],
        )
    )


def test_tcp_point_sets_are_seen_and_named():
    fake = _TcpFake([_a_point_set()])
    try:
        up, seen, total = sonar_probe.listen_tcp("127.0.0.1", fake.port, seconds=2.0)
    finally:
        fake.stop()
    assert up is True
    assert total > 0
    assert seen.get(3104), f"no OS3D_POINT_SET, saw {seen}"


def test_a_connection_that_opens_and_says_nothing_is_reported_as_such():
    """The actual 2026-10-28 result. It must not read as a failure to connect,
    and it must not read as a working sonar: it is a live device with a dry
    transducer, and the operator's next move is water, not a cable."""
    fake = _TcpFake([])
    try:
        up, seen, total = sonar_probe.listen_tcp("127.0.0.1", fake.port, seconds=1.5)
    finally:
        fake.stop()
    assert up is True
    assert total == 0
    assert seen == {}


def test_bytes_that_are_not_ping_protocol_are_reported_not_swallowed():
    """The outcome nobody has ruled out. "Connected, bytes, nothing parsed" is
    a finding, and the probe has to be able to say it."""
    fake = _TcpFake([bytes(range(256)) * 4])
    try:
        up, seen, total = sonar_probe.listen_tcp("127.0.0.1", fake.port, seconds=1.5)
    finally:
        fake.stop()
    assert up is True
    assert total > 0
    assert seen == {}


def test_the_probe_sends_nothing_to_the_sonar():
    """It is a probe. A sonar that needs asking before it streams is an open
    question, and inventing the request to find out is how a transducer gets
    driven in air."""
    fake = _TcpFake([])
    try:
        sonar_probe.listen_tcp("127.0.0.1", fake.port, seconds=1.0)
        assert bytes(fake.bytes_from_client) == b""
    finally:
        fake.stop()


def test_a_dead_tcp_port_is_a_clean_false_not_a_traceback():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    dead = probe.getsockname()[1]
    probe.close()
    up, seen, total = sonar_probe.listen_tcp("127.0.0.1", dead, seconds=1.0)
    assert (up, seen, total) == (False, {}, 0)
