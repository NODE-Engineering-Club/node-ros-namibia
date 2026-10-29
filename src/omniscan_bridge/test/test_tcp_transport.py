"""The TCP transport, against a socket that behaves badly on purpose.

On 2026-10-28 the sonar was found to answer on **TCP 62312** and nothing else.
That settled the transport and left the payload open: the transducer was in
air, so not one byte of point data has ever been seen. These tests are written
to that division.

What they prove:

* a connection is made, and bytes on it reach the consumer;
* a frame split across two reads survives — TCP has no frame boundaries, so
  this is the normal case rather than an edge one;
* garbage, truncation and a connection cut mid-frame are counted and
  resynchronised from, never crashed on;
* a connection that is up and silent is reported as up and silent, which is
  precisely the state the real hardware was in all evening;
* a refused connection backs off instead of hammering;
* **the transport sends nothing unless something outside it explicitly asks.**
  A transducer told to ping in air can damage itself.

What they cannot prove is that a real Omniscan 3D puts Ping Protocol frames on
this socket. Nothing here touches hardware, and nothing here may: see
docs/SONAR_BRINGUP.md for the one test that can settle it, which needs water.
"""

from __future__ import annotations

import socket
import threading
import time

from omniscan_bridge.core import ping_protocol as pp
from omniscan_bridge.core.parser import PingParser
from omniscan_bridge.core.transport import DEFAULT_HOST, TcpTransport


def _point_set_frame(ping_number: int = 1, n_points: int = 8) -> bytes:
    """A real, checksummed OS3D_POINT_SET — the frame we hope to see one day."""
    points = [
        pp.Point(angle_rad=0.01 * i, tof_s=0.004 + 0.0001 * i, power=40.0, pt_type=1)
        for i in range(n_points)
    ]
    return pp.encode_point_set(
        pp.PointSet(
            ping_number=ping_number,
            speed_of_sound=1500.0,
            utc_msec=1_700_000_000_000 + ping_number,
            points=points,
        )
    )


class ScriptedServer:
    """A TCP listener that writes a script of byte chunks and then waits.

    Deliberately not a sonar simulator. It is a thing that puts exactly the
    bytes a test asks for onto a real socket, including bytes no sonar would
    ever send, because what this transport has to survive is a stream nobody
    has characterised.
    """

    def __init__(self, chunks: list[bytes], close_after: bool = False,
                 gap_s: float = 0.0) -> None:
        self.chunks = chunks
        self.close_after = close_after
        self.gap_s = gap_s
        self.bytes_from_client = bytearray()
        self.connections = 0
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self._listener.settimeout(0.3)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> ScriptedServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        try:
            self._listener.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except (socket.timeout, OSError):
                continue
            self.connections += 1
            conn.settimeout(0.2)
            try:
                for chunk in self.chunks:
                    conn.sendall(chunk)
                    if self.gap_s:
                        time.sleep(self.gap_s)
                if self.close_after:
                    conn.close()
                    continue
                # Stay open and silent — the real sonar's observed behaviour.
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


def _drain_until(transport, predicate, timeout_s: float = 4.0) -> bytes:
    """Collect from the transport until ``predicate(buffer)`` or we give up."""
    buf = bytearray()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for chunk in transport.read():
            buf += chunk
        if predicate(bytes(buf)):
            break
        time.sleep(0.02)
    return bytes(buf)


def _wait(predicate, timeout_s: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- the happy path ------------------------------------------------------


def test_a_whole_frame_arrives_and_parses():
    frame = _point_set_frame()
    server = ScriptedServer([frame]).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        data = _drain_until(transport, lambda b: len(b) >= len(frame))
    finally:
        transport.stop()
        server.stop()

    parsed = list(PingParser().feed(data))
    assert [f.message_id for f in parsed] == [pp.MSG_OS3D_POINT_SET]


def test_a_frame_split_across_two_reads_survives():
    """TCP gives no frame boundaries. A frame cut in half is the normal case."""
    frame = _point_set_frame(n_points=32)
    cut = len(frame) // 3
    server = ScriptedServer([frame[:cut], frame[cut:]], gap_s=0.15).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        data = _drain_until(transport, lambda b: len(b) >= len(frame))
    finally:
        transport.stop()
        server.stop()

    parsed = list(PingParser().feed(data))
    assert [f.message_id for f in parsed] == [pp.MSG_OS3D_POINT_SET]


def test_two_frames_in_one_read_both_arrive():
    one, two = _point_set_frame(1), _point_set_frame(2)
    server = ScriptedServer([one + two]).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        data = _drain_until(transport, lambda b: len(b) >= len(one) + len(two))
    finally:
        transport.stop()
        server.stop()

    assert len(list(PingParser().feed(data))) == 2


# -- the stream is not what we think it is ------------------------------


def test_noise_is_counted_and_the_next_frame_still_parses():
    """The one outcome nobody has ruled out: TCP 62312 is not Ping Protocol.

    The required behaviour is a number going up, not an exception. A bridge
    that dies on an unexpected stream takes the whole sonar panel with it, and
    the panel is the only thing that would have said what was on the wire.
    """
    noise = bytes(range(256)) * 4
    frame = _point_set_frame()
    server = ScriptedServer([noise, frame], gap_s=0.1).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        data = _drain_until(transport, lambda b: len(b) >= len(noise) + len(frame))
    finally:
        transport.stop()
        server.stop()

    parser = PingParser()
    parsed = list(parser.feed(data))
    assert [f.message_id for f in parsed] == [pp.MSG_OS3D_POINT_SET]
    assert parser.stats.bytes_discarded > 0


def test_a_truncated_frame_does_not_eat_the_one_after_it():
    frame = _point_set_frame(1)
    server = ScriptedServer(
        [frame[: len(frame) // 2], _point_set_frame(2)], gap_s=0.1
    ).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        data = _drain_until(transport, lambda b: len(b) >= len(frame))
    finally:
        transport.stop()
        server.stop()

    parser = PingParser()
    parsed = list(parser.feed(data))
    # The complete frame of the two is recovered; the half frame is not
    # silently turned into one.
    assert len(parsed) == 1
    assert parser.stats.frames_parsed == 1


def test_a_connection_cut_mid_frame_is_seen_as_a_disconnect():
    frame = _point_set_frame(n_points=64)
    server = ScriptedServer([frame[: len(frame) // 2]], close_after=True).start()
    transport = TcpTransport(
        host="127.0.0.1", port=server.port, reconnect_interval_s=0.1
    )
    transport.start()
    try:
        assert _wait(lambda: transport.stats.disconnects >= 1), (
            f"peer close not noticed: {transport.stats}"
        )
        # And it comes back, rather than sitting on a dead socket.
        assert _wait(lambda: transport.stats.reconnects >= 1), (
            f"did not reconnect: {transport.stats}"
        )
    finally:
        transport.stop()
        server.stop()


# -- a link with nothing on it ------------------------------------------


def test_a_silent_connection_is_up_but_not_connected():
    """The state the real sonar was in all evening, and the whole reason
    ``link_up`` exists separately from ``connected``: the transducer was in
    air. Reporting that as connected would be a green light over no data."""
    server = ScriptedServer([]).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        assert _wait(lambda: transport.link_up), "never connected"
        assert transport.connected is False
        assert transport.stats.bytes_received == 0
        assert transport.seconds_connected >= 0.0
    finally:
        transport.stop()
        server.stop()


def test_nothing_is_sent_to_the_sonar_unless_asked():
    """A transducer commanded to ping in air can damage itself, and no human
    is beside the boat when a launch file runs. The transport must be a
    listener: no handshake, no subscribe, no keepalive."""
    server = ScriptedServer([]).start()
    transport = TcpTransport(host="127.0.0.1", port=server.port)
    transport.start()
    try:
        assert _wait(lambda: transport.link_up)
        time.sleep(1.0)
        assert bytes(server.bytes_from_client) == b"", (
            f"the transport sent {bytes(server.bytes_from_client)!r} on its own"
        )
        # An explicit send still works — that is a person deciding.
        assert transport.send(b"\x00\x01") is True
        assert _wait(lambda: len(server.bytes_from_client) == 2)
    finally:
        transport.stop()
        server.stop()


# -- nothing there at all -----------------------------------------------


def test_a_refused_connection_backs_off_instead_of_hammering():
    # A port nobody is listening on: bind it, read the number, release it.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    transport = TcpTransport(
        host="127.0.0.1", port=dead_port,
        reconnect_interval_s=0.1, backoff_max_s=0.8,
    )
    transport.start()
    try:
        assert _wait(lambda: transport.stats.connect_failures >= 3), (
            f"no connect attempts recorded: {transport.stats}"
        )
        time.sleep(1.0)
        # 0.1 + 0.2 + 0.4 + 0.8 ... — far fewer attempts than a fixed 0.1 s
        # retry would make in the same two seconds.
        assert transport.stats.connect_failures < 15, (
            f"retrying too fast: {transport.stats.connect_failures} attempts"
        )
        assert transport.link_up is False
        assert transport.connected is False
        assert transport.stats.last_connect_error, "the OS error was not recorded"
    finally:
        transport.stop()


def test_a_connect_that_never_answers_gives_up_on_its_own():
    """A TCP connect to an address with nothing on it blocks until the kernel
    gives up, which is minutes. The node must not look hung for minutes."""
    transport = TcpTransport(
        host="192.0.2.1",  # RFC 5737 TEST-NET-1: routed nowhere
        port=62312, connect_timeout_s=0.3, reconnect_interval_s=0.1,
        backoff_max_s=0.2,
    )
    assert transport.connect_timeout_s == 0.3
    transport.start()
    try:
        assert _wait(lambda: transport.stats.connect_failures >= 1, timeout_s=6.0), (
            "a connect to a black hole never timed out"
        )
    finally:
        transport.stop()


# -- the measured defaults ----------------------------------------------


def test_the_default_host_is_the_one_the_sonar_was_measured_on():
    assert DEFAULT_HOST == "192.168.2.21"
    assert pp.DEFAULT_PORT == 62312
