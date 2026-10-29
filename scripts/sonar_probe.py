#!/usr/bin/env python3
"""Is the sonar there, and is it saying anything we understand?

The first question on a bench is never "does the GUI render a point cloud".
It is "is the box alive, and are its packets the shape we think they are".
This answers exactly that, with no ROS, no container and no GUI — so when
something does not work, it rules out two of the three suspects in ten
seconds.

Four checks, in the order they stop being worth doing:

1. **Is it on the network?**  ARP/ICMP via a ping.
2. **Does it answer discovery?**  The Blue Robotics discovery protocol, a
   broadcast on UDP 30303, which is how SonarView finds devices whose address
   is not on your subnet.
3. **Does TCP 62312 accept a connection, and does anything come down it?**
   This is the one that matters. Connect, listen, report every byte and every
   message id, decoding OS3D_POINT_SET (3104) if it arrives.
4. **Anything on UDP 62312, or TCP 51200?**  Both were what this repository and
   Cerulean's documentation respectively used to assume. Kept as a cross-check,
   and because a changed firmware would show up here first.

Usage::

    # the measured address
    python3 scripts/sonar_probe.py --host 192.168.2.21

    # against the simulator, no hardware at all
    python3 -m asket_sim.core.fake_sonar_server --port 62312 &
    python3 scripts/sonar_probe.py --host 127.0.0.1 --udp

WHAT THE 2026-10-28 BENCH SESSION SETTLED
=========================================

An earlier version of this script defaulted to UDP 62312, warned that the port
was uncorroborated, and also tried TCP 51200 because Cerulean's SonarView
documentation states:

    "Omniscan 450 communicates with tcp, ip address must match the actual
    device settings, and 51200 is the required port number for Omniscan 450."
    -- docs.ceruleansonar.com, Session Configurations / Multiple Omniscans

Neither answered. What answered was **TCP 62312** — our port, Cerulean's
transport, a combination neither source predicted:

* nmap: 62312 open, 51200 closed, 7077 closed; web on 80, SSH on 22.
* SonarView: ``CONNECTED, tcp://192.168.2.21:62312, Product: Omniscan 3D``.

So that is what this script now probes first, and it is why it failed on the
night it was needed. **The format of the bytes on that socket is still
unverified**: the transducer was in air, so nothing was sent. A connection that
opens and stays silent is the expected result until the sonar is in water, and
this script reports it as exactly that rather than as a fault.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time

#: Where the sonar actually is (measured 2026-10-28). Overridable with
#: ASKET_SONAR_HOST, the same variable the launch files read.
DEFAULT_SONAR_HOST = "192.168.2.21"

#: Blue Robotics / Cerulean Ethernet discovery. Broadcast, which is why it does
#: not survive a Wi-Fi bridge — run this on the wired segment.
DISCOVERY_PORT = 30303
DISCOVERY_REQUEST = b"DISCOVER"

#: The data port. Measured open on TCP, 2026-10-28.
DEFAULT_DATA_PORT = 62312

#: What Cerulean's docs state for the Omniscan 450 family. Measured CLOSED on
#: this device, which is the whole reason this script exists.
DOCUMENTED_TCP_PORT = 51200

#: SonarLink. Also measured closed.
SONARLINK_PORT = 7077

MESSAGE_NAMES = {
    0: "NOP", 1: "ACK", 2: "NACK", 3: "ASCII_TEXT",
    4: "DEVICE_INFORMATION", 6: "GENERAL_REQUEST",
    109: "NMEA_WRAPPER", 150: "MAVLINK_WRAPPER",
    504: "ATTITUDE_REPORT", 3010: "END_PING_INFO", 3104: "OS3D_POINT_SET",
}


def _say(ok: bool | None, text: str) -> None:
    mark = "  ok  " if ok else ("  --  " if ok is None else " FAIL ")
    print(f"[{mark}] {text}")


def check_ping(host: str) -> bool | None:
    """ICMP if we can, and an honest shrug if we cannot.

    Returns True/False for a real answer and **None** when ping itself is
    unavailable — a container without iputils, which is not the sonar's fault
    and must not be reported as the sonar being dead. The caller then relies on
    the data checks below, which are the ones that actually matter.
    """
    try:
        done = subprocess.run(
            ["ping", "-c", "2", "-W", "2", host],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        _say(None, "ping is not installed here — skipping, the data checks below decide")
        return None
    except (OSError, subprocess.TimeoutExpired):
        _say(None, f"ping {host}: could not run ping — skipping")
        return None
    ok = done.returncode == 0
    _say(ok, f"ping {host}" + ("" if ok else " — no reply"))
    if not ok:
        print("         Check: cable in, sonar powered, and this machine has an")
        print("         address on the same /24 (ip addr show).")
    return ok


def check_discovery(timeout_s: float, broadcast: str) -> list[tuple[str, bytes]]:
    """Ask every device on the wire to identify itself."""
    found: list[tuple[str, bytes]] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    try:
        sock.sendto(DISCOVERY_REQUEST, (broadcast, DISCOVERY_PORT))
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if data and data != DISCOVERY_REQUEST:
                found.append((addr[0], data))
    finally:
        sock.close()

    if found:
        for ip, blob in found:
            text = blob.decode("ascii", errors="replace").replace("\r", " ").replace("\n", " ")
            _say(True, f"discovery: {ip} says {text.strip()[:90]!r}")
    else:
        _say(False, f"discovery: nothing answered on UDP {DISCOVERY_PORT}")
        print("         This is broadcast, so it does not cross Wi-Fi. If you are")
        print("         on the wired segment and still see nothing, the sonar may")
        print("         simply be on another subnet — try --host directly.")
    return found


def check_tcp(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    try:
        sock.connect((host, port))
    except OSError as exc:
        _say(False, f"TCP {host}:{port} — {exc.strerror or exc}")
        return False
    finally:
        sock.close()
    _say(True, f"TCP {host}:{port} accepted a connection")
    return True


def _parser_or_none():
    """The real parser if the workspace is importable, else None.

    This script has to work from a bare checkout on a laptop with no ROS, so a
    missing parser degrades to counting bytes rather than refusing to run.
    """
    try:
        from omniscan_bridge.core.parser import PingParser
    except ImportError:
        return None
    return PingParser()


def listen_tcp(host: str, port: int, seconds: float) -> tuple[bool, dict[int, int], int]:
    """Connect to the data port and report what comes down it.

    Returns ``(connected, messages_by_id, total_bytes)``. The three are
    reported separately on purpose, because the three interesting outcomes are
    distinct and the operator needs to tell them apart:

    * not connected                -> wrong address, or nothing listening;
    * connected, zero bytes        -> the state on 2026-10-28: a live sonar
                                      with a transducer in air. Also what a
                                      sonar that must be asked to stream would
                                      look like;
    * connected, bytes, no 3104    -> something is being sent and it is not
                                      the point sets we parse. That is the
                                      finding worth having.

    Nothing is sent to the sonar. If this stream needs asking for, that is an
    open question (docs/SONAR_BRINGUP.md) and inventing a command to answer it
    is how a transducer gets driven in air.
    """
    seen: dict[int, int] = {}
    total_bytes = 0
    try:
        sock = socket.create_connection((host, port), timeout=3.0)
    except OSError as exc:
        _say(False, f"TCP {host}:{port} — {exc.strerror or exc}")
        return False, seen, 0
    _say(True, f"TCP {host}:{port} accepted a connection")

    parser = _parser_or_none()
    sock.settimeout(0.5)
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            try:
                data = sock.recv(65535)
            except socket.timeout:
                continue
            if not data:
                _say(False, f"TCP {host}:{port} — the sonar closed the connection")
                break
            total_bytes += len(data)
            if parser is None:
                continue
            for frame in parser.feed(data):
                seen[frame.message_id] = seen.get(frame.message_id, 0) + 1
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if total_bytes == 0:
        _say(False, f"TCP {port}: connected, and the sonar sent nothing in "
                    f"{seconds:.0f} s")
        print("         Expected with the transducer in air. If it IS in water,")
        print("         the stream may have to be asked for — see")
        print("         docs/SONAR_BRINGUP.md.")
    elif not seen:
        _say(False, f"TCP {port}: {total_bytes} bytes arrived and none of them "
                    "parsed as Ping Protocol")
        print("         This is the interesting failure: the sonar is talking and")
        print("         we do not understand it. Capture it:")
        print(f"         sudo tcpdump -i any -n -w sonar.pcap host {host} and port {port}")
    else:
        for mid, count in sorted(seen.items()):
            name = MESSAGE_NAMES.get(mid, "unknown")
            _say(True, f"TCP {port}: message {mid} ({name}) x{count}")
    return True, seen, total_bytes


def listen_udp(
    host: str, port: int, seconds: float, bind_port: int | None = None
) -> dict[int, int]:
    """Listen for Ping Protocol frames and count them by message id."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    # Normally we bind the same port we expect the device to send to. Against a
    # *local* fake sonar that port is already taken by the fake itself, so
    # --bind-port lets the two coexist on one machine.
    sock.bind(("0.0.0.0", bind_port if bind_port is not None else port))

    # Some devices only stream to a client that has spoken first. Knocking is
    # harmless if it does not.
    try:
        sock.sendto(b"\x00", (host, port))
    except OSError:
        pass

    seen: dict[int, int] = {}
    total_bytes = 0
    parser = _parser_or_none()
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            total_bytes += len(data)
            if parser is None:
                seen[-1] = seen.get(-1, 0) + 1
                continue
            for frame in parser.feed(data):
                seen[frame.message_id] = seen.get(frame.message_id, 0) + 1
    finally:
        sock.close()

    if not seen:
        _say(False, f"UDP {port}: nothing arrived in {seconds:.0f} s ({total_bytes} bytes)")
    else:
        for mid, count in sorted(seen.items()):
            name = MESSAGE_NAMES.get(mid, "unknown")
            _say(True, f"UDP {port}: message {mid} ({name}) x{count}")
    return seen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("ASKET_SONAR_HOST", DEFAULT_SONAR_HOST),
                    help=f"sonar address (default {DEFAULT_SONAR_HOST}, measured "
                         "2026-10-28; ASKET_SONAR_HOST overrides it)")
    ap.add_argument("--port", type=int, default=DEFAULT_DATA_PORT,
                    help=f"data port (default {DEFAULT_DATA_PORT}, TCP)")
    ap.add_argument("--broadcast", default="255.255.255.255",
                    help="discovery broadcast address")
    ap.add_argument("--listen", type=float, default=8.0,
                    help="seconds to listen for data (default 8)")
    ap.add_argument("--udp", action="store_true",
                    help="also listen on UDP --port. Off by default: the device "
                         "was measured to speak TCP. Turn it on against "
                         "asket_sim's fake sonar, which is UDP.")
    ap.add_argument("--bind-port", type=int, default=None,
                    help="local UDP port to listen on, if it must differ from "
                         "--port (needed only when a fake sonar on THIS machine "
                         "already holds that port). Implies --udp.")
    ap.add_argument("--probe-old-ports", action="store_true", default=True,
                    help=f"also try TCP {DOCUMENTED_TCP_PORT} and {SONARLINK_PORT}, "
                         "both measured closed")
    ap.add_argument("--no-probe-old-ports", dest="probe_old_ports",
                    action="store_false")
    args = ap.parse_args(argv)

    print(f"Probing sonar at {args.host}\n")

    print("1. On the network?")
    reachable = check_ping(args.host)

    print("\n2. Answers discovery?")
    check_discovery(3.0, args.broadcast)

    print(f"\n3. TCP {args.port} — the measured transport?")
    tcp_up, tcp_seen, tcp_bytes = listen_tcp(args.host, args.port, args.listen)

    udp_seen: dict[int, int] = {}
    if args.udp or args.bind_port is not None:
        print(f"\n4. Anything on UDP {args.port}?")
        udp_seen = listen_udp(args.host, args.port, args.listen, args.bind_port)

    old_ports_open: list[int] = []
    if args.probe_old_ports:
        print("\n5. The ports we used to assume (both measured closed)?")
        for port in (DOCUMENTED_TCP_PORT, SONARLINK_PORT):
            if check_tcp(args.host, port):
                old_ports_open.append(port)

    print("\n--- verdict ---")
    if reachable is False and not tcp_up:
        print("The sonar is not reachable. Nothing below this matters until it is:")
        print("power, cable, and an address on the same /24. Guide step 2.")
        return 1
    if tcp_seen.get(3104) or udp_seen.get(3104):
        where = f"TCP {args.port}" if tcp_seen.get(3104) else f"UDP {args.port}"
        print(f"Sonar alive and sending OS3D_POINT_SET on {where}.")
        print("This is the first time that has been seen. The packet layout is")
        print("now confirmed against real hardware — say so in")
        print("docs/SONAR_BRINGUP.md and docs/HARDWARE_DECISIONS.md.")
        return 0
    if tcp_bytes:
        print(f"TCP {args.port} is up and sent {tcp_bytes} bytes that did not parse")
        print("as Ping Protocol. This is the finding worth having: the transport is")
        print("right and the FORMAT is not what omniscan_bridge expects. Capture it")
        print("with tcpdump and bring the file back.")
        return 2
    if tcp_up:
        print(f"TCP {args.port} accepted a connection and nothing came down it.")
        print("With the transducer in air this is the correct, expected result —")
        print("it is exactly where the 2026-10-28 session stopped. Put the sonar")
        print("in water and run this again; that is the one remaining unknown.")
        return 0
    if udp_seen:
        print(f"Nothing on TCP {args.port}, but frames arrived on UDP {args.port}.")
        print("That contradicts the 2026-10-28 measurement — a firmware change, or")
        print("a different device. Record it; omniscan.yaml's transport: tcp would")
        print("have to go back to udp.")
        return 2
    if old_ports_open:
        ports = ", ".join(str(p) for p in old_ports_open)
        print(f"Nothing on {args.port}, but {ports} is open — which was closed on")
        print("2026-10-28. Something has changed about this device. Record it.")
        return 2
    print("Reachable, and nothing is listening on the data port.")
    print("Check the sonar has finished booting (give it a minute from power-on),")
    print("then that this really is the sonar's address: nmap -p- the host, and")
    print("expect 22, 80 and 62312. See guide step 3.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
