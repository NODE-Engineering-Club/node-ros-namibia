"""The sonar probe, against a fake sonar.

``scripts/sonar_probe.py`` is the first thing anybody runs on a bench, and it
is the one tool that has to work when nothing else does — so it is the one that
must not need hardware to be trusted.

These tests run the real probe against ``FakeSonarServer``, which puts real
Ping Protocol frames on a real UDP socket. What they prove is that the probe
reads a live sonar correctly and reports an absent one honestly. What they
cannot prove is that a real Omniscan 3D speaks UDP 62312 at all — see the
module docstring of the probe, and docs/SONAR_BRINGUP.md §3.
"""

from __future__ import annotations

import socket
import sys
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


def test_the_documented_tcp_port_is_probed_too():
    """Cerulean's docs say the 450 family speaks TCP 51200, and the bridge is
    built around UDP 62312. The probe checks both so the evening produces an
    answer either way, rather than a shrug."""
    assert sonar_probe.DOCUMENTED_TCP_PORT == 51200
    assert sonar_probe.DEFAULT_DATA_PORT == 62312


def test_a_closed_tcp_port_is_a_clean_false(fake_sonar):
    _server, port = fake_sonar
    assert sonar_probe.check_tcp("127.0.0.1", port) is False
