#!/usr/bin/env python3
"""Is the sonar there, and is it saying anything we understand?

The first question on a bench is never "does the GUI render a point cloud".
It is "is the box alive, and are its packets the shape we think they are".
This answers exactly that, with no ROS, no container and no GUI — so when
something does not work, it rules out two of the three suspects in ten
seconds.

Three checks, in the order they stop being worth doing:

1. **Is it on the network?**  ARP/ICMP via a TCP connect and a ping.
2. **Does it answer discovery?**  The Blue Robotics discovery protocol, a
   broadcast on UDP 30303, which is how SonarView finds devices whose address
   is not on your subnet.
3. **Is it sending us Ping Protocol frames?**  Listen on the data port and
   report every message id seen, decoding OS3D_POINT_SET (3104) if it arrives.

Usage::

    # tonight, factory address
    python3 scripts/sonar_probe.py --host 192.168.2.92

    # against the simulator, no hardware at all
    python3 -m asket_sim.core.fake_sonar_server --port 62312 &
    python3 scripts/sonar_probe.py --host 127.0.0.1

A WARNING ABOUT THE DATA PORT
=============================

This script defaults to UDP 62312, because that is what ``omniscan_bridge`` is
built around. **That port is not corroborated by Cerulean's published
documentation.** Their SonarView session-configuration page states the opposite
for the 450 family:

    "Omniscan 450 communicates with tcp, ip address must match the actual
    device settings, and 51200 is the required port number for Omniscan 450."
    -- docs.ceruleansonar.com, Session Configurations / Multiple Omniscans

So ``--probe-tcp`` also tries a TCP connect to 51200, and the report says which
of the two answered. If TCP 51200 is the live one, the bridge's transport is
wrong and that is a finding worth having before the boat is in the water, not
after. See docs/SONAR_BRINGUP.md.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time

#: Blue Robotics / Cerulean Ethernet discovery. Broadcast, which is why it does
#: not survive a Wi-Fi bridge — run this on the wired segment.
DISCOVERY_PORT = 30303
DISCOVERY_REQUEST = b"DISCOVER"

#: What omniscan_bridge expects. Unconfirmed against Cerulean's docs.
DEFAULT_DATA_PORT = 62312

#: What Cerulean's docs state for the Omniscan 450 family.
DOCUMENTED_TCP_PORT = 51200

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


def listen_udp(host: str, port: int, seconds: float, bind_port: int | None = None) -> dict[int, int]:
    """Listen for Ping Protocol frames and count them by message id."""
    try:
        from omniscan_bridge.core.parser import PingParser
    except ImportError:
        PingParser = None

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
    parser = PingParser() if PingParser else None
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
    ap.add_argument("--host", default="192.168.2.92",
                    help="sonar address (factory default 192.168.2.92)")
    ap.add_argument("--port", type=int, default=DEFAULT_DATA_PORT,
                    help=f"UDP data port to listen on (default {DEFAULT_DATA_PORT})")
    ap.add_argument("--broadcast", default="255.255.255.255",
                    help="discovery broadcast address")
    ap.add_argument("--listen", type=float, default=8.0,
                    help="seconds to listen for data (default 8)")
    ap.add_argument("--bind-port", type=int, default=None,
                    help="local UDP port to listen on, if it must differ from "
                         "--port (needed only when a fake sonar on THIS machine "
                         "already holds that port)")
    ap.add_argument("--probe-tcp", action="store_true", default=True,
                    help=f"also try TCP {DOCUMENTED_TCP_PORT} (Cerulean's documented port)")
    ap.add_argument("--no-probe-tcp", dest="probe_tcp", action="store_false")
    args = ap.parse_args(argv)

    print(f"Probing sonar at {args.host}\n")

    print("1. On the network?")
    reachable = check_ping(args.host)

    print("\n2. Answers discovery?")
    check_discovery(3.0, args.broadcast)

    print("\n3. Sending data?")
    udp_seen = listen_udp(args.host, args.port, args.listen, args.bind_port)

    tcp_open = False
    if args.probe_tcp:
        print(f"\n4. Cerulean's documented transport (TCP {DOCUMENTED_TCP_PORT})?")
        tcp_open = check_tcp(args.host, DOCUMENTED_TCP_PORT)

    print("\n--- verdict ---")
    if reachable is False:
        print("The sonar is not reachable. Nothing below this matters until it is:")
        print("power, cable, and an address on the same /24. Guide step 2.")
        return 1
    if udp_seen.get(3104):
        print(f"Sonar alive and sending OS3D_POINT_SET on UDP {args.port}.")
        print("omniscan_bridge should work as configured. Guide step 5.")
        return 0
    if udp_seen:
        print(f"Frames arrived on UDP {args.port} but no OS3D_POINT_SET (3104).")
        print("It may not be pinging — start a session in SonarView, or put it")
        print("in water. See guide step 3.")
        return 0
    if tcp_open:
        print(f"Nothing on UDP {args.port}, but TCP {DOCUMENTED_TCP_PORT} is open.")
        print("That matches Cerulean's documentation and NOT omniscan_bridge's")
        print("configuration. Expect the GUI's sonar health to stay silent.")
        print("Record this — it means the bridge's transport needs changing.")
        return 2
    print("Reachable, but silent on both ports.")
    print("Most likely it is simply not pinging yet: it streams once a client")
    print("asks. Run SonarView (guide step 4) and probe again while it runs.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
