"""The fake flight controller's heartbeat is what MAVROS and the probe read.

The frame is decoded as MAVROS would hand it on: payload packed into
little-endian uint64 words. The CRC is checked against a frame MAVROS
accepted ("CON: Got HEARTBEAT, connected. FCU: ArduPilot" in the ROS image).
"""

import struct

import pytest

from asket_common.autopilot import from_mavlink
from asket_sim.fake_fcu import AUTOPILOTS, crc_x25, heartbeat


def _as_mavros(frame: bytes):
    length, seq, sysid, compid, msgid = struct.unpack_from("<BBBBB", frame, 1)
    payload = frame[6:6 + length] + b"\0" * (-length % 8)
    words = [struct.unpack_from("<Q", payload, i)[0] for i in range(0, len(payload), 8)]
    return msgid, compid, words


@pytest.mark.parametrize("name", sorted(AUTOPILOTS))
def test_the_probe_reads_the_fake_heartbeat(name):
    msgid, compid, words = _as_mavros(heartbeat(0, AUTOPILOTS[name]))
    assert from_mavlink(msgid, compid, words) == name


def test_the_frame_checksum_is_valid():
    frame = heartbeat(7, AUTOPILOTS["px4"])
    assert frame[0] == 0xFE and len(frame) == 6 + 9 + 2
    crc = crc_x25(bytes([50]), crc_x25(frame[1:15]))
    assert struct.unpack_from("<H", frame, 15)[0] == crc
