"""A fake flight controller: MAVLink 1 HEARTBEATs over UDP, nothing else.

For checking the Phase B (d) wiring without a Pixhawk: MAVROS connects, the
autopilot_probe reads the firmware, and the pre-flight's autopilot.match
passes or fails. Stdlib only.

    ros2 launch bringup njord.launch.py autopilot:=ardupilot \\
        fcu_url:=udp://127.0.0.1:14555@127.0.0.1:14556 ...
    ros2 run asket_sim fake_fcu px4        # → autopilot.match FAIL
    ros2 run asket_sim fake_fcu ardupilot  # → PASS
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

#: MAV_AUTOPILOT values.
AUTOPILOTS = {"ardupilot": 3, "px4": 12}
#: MAV_TYPE_SURFACE_BOAT.
SURFACE_BOAT = 11
#: HEARTBEAT's CRC_EXTRA (MAVLink common.xml).
HEARTBEAT_CRC_EXTRA = 50


def crc_x25(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def heartbeat(seq: int, autopilot: int, mav_type: int = SURFACE_BOAT,
              sysid: int = 1, compid: int = 1) -> bytes:
    """One MAVLink 1 HEARTBEAT frame."""
    payload = struct.pack("<IBBBBB", 0, mav_type, autopilot, 0x81, 4, 3)
    header = struct.pack("<BBBBB", len(payload), seq & 0xFF, sysid, compid, 0)
    crc = crc_x25(bytes([HEARTBEAT_CRC_EXTRA]), crc_x25(header + payload))
    return b"\xfe" + header + payload + struct.pack("<H", crc)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("autopilot", choices=sorted(AUTOPILOTS))
    parser.add_argument("--to", default="127.0.0.1:14555", help="MAVROS's UDP bind address")
    parser.add_argument("--from-port", type=int, default=14556)
    args = parser.parse_args(argv)
    host, port = args.to.rsplit(":", 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", args.from_port))
    seq = 0
    while True:
        sock.sendto(heartbeat(seq, AUTOPILOTS[args.autopilot]), (host, int(port)))
        seq += 1
        time.sleep(0.5)


if __name__ == "__main__":
    main()
