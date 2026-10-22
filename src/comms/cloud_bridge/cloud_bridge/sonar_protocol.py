#!/usr/bin/env python3
"""Cerulean/Blue-Robotics ping-protocol framing + os_mono_profile payload
parsing, kept free of any rclpy/ROS import — same reasoning as geo_utils.py:
this is what makes it testable in an environment with no ROS 2 install at
all (test/test_sonar_math.py).

HONESTY NOTE: the generic outer framing (2-byte sync "BR", length, message
id, checksum) is the long-stable Blue-Robotics ping-protocol structure
Cerulean's own sonars are documented to use, and is reasonably solid; the
`os_mono_profile` PAYLOAD field offsets in `PROFILE_HEADER_STRUCT` below are
reproduced from KELP_MISSIONS.md §5.1's prose description, NOT from a live
packet capture or the current docs.ceruleansonar.com API reference — this
is the one part of the sonar pipeline that must be checked against a real
device or those live docs before being trusted on the water. See
test_sonar_math.py's own docstring for exactly what is and isn't proven by
its round-trip test.
"""
import struct

SYNC = b"BR"
HEADER_STRUCT = struct.Struct("<2sHHBB")  # sync, payload_len, message_id, src_id, dst_id
CHECKSUM_STRUCT = struct.Struct("<H")
MSG_ID_OS_MONO_PROFILE = 2198

# Best-effort field layout for os_mono_profile's payload, per
# KELP_MISSIONS.md §5.1's field list — NOT byte-verified against a real
# device. ping_number(u32) timestamp_ms(u32) ping_hz(u32) gain_index(u32)
# num_results(u16) speed_of_sound_mms(u32) range_mm(u32) ... followed by
# num_results * u16 power samples. Confirm against docs.ceruleansonar.com
# before trusting on hardware.
PROFILE_HEADER_STRUCT = struct.Struct("<IIIIHII")


def parse_ping_protocol_frame(data: bytes):
    """Parse one Blue-Robotics-ping-protocol frame. Returns
    (message_id, payload_bytes) or None if the frame is malformed, short,
    or fails its checksum (never trust corrupt data silently)."""
    if len(data) < HEADER_STRUCT.size + CHECKSUM_STRUCT.size:
        return None
    sync, payload_len, message_id, _src, _dst = HEADER_STRUCT.unpack_from(data, 0)
    if sync != SYNC:
        return None
    payload_start = HEADER_STRUCT.size
    payload_end = payload_start + payload_len
    if len(data) < payload_end + CHECKSUM_STRUCT.size:
        return None
    payload = data[payload_start:payload_end]
    (checksum,) = CHECKSUM_STRUCT.unpack_from(data, payload_end)
    computed = sum(data[:payload_end]) & 0xFFFF
    if computed != checksum:
        return None
    return message_id, payload


def parse_os_mono_profile(payload: bytes):
    """Best-effort os_mono_profile payload parse — see module docstring's
    honesty note. Returns a dict with ping_id/range_m/gain_db/samples, or
    None if the payload is too short for even the fixed-size header."""
    if len(payload) < PROFILE_HEADER_STRUCT.size:
        return None
    ping_number, _timestamp_ms, _ping_hz, gain_index, num_results, _speed_mms, range_mm = (
        PROFILE_HEADER_STRUCT.unpack_from(payload, 0)
    )
    samples_start = PROFILE_HEADER_STRUCT.size
    samples_end = samples_start + num_results * 2
    if len(payload) < samples_end:
        return None
    samples = struct.unpack_from(f"<{num_results}H", payload, samples_start)
    return {
        "ping_id": ping_number,
        "range_m": round(range_mm / 1000.0, 2),
        "gain_db": float(gain_index),  # gain INDEX, not literal dB — a unit placeholder until confirmed
        "samples": samples,
    }


def build_synthetic_frame(ping_number: int, range_mm: int, gain_index: int, samples):
    """Encode a frame matching this module's own assumed layout — used only
    by tests to check parse_ping_protocol_frame/parse_os_mono_profile are
    self-consistent with their own stated assumptions (not a real-device
    protocol compliance check; see this module's own honesty note)."""
    profile_payload = PROFILE_HEADER_STRUCT.pack(
        ping_number, 0, 0, gain_index, len(samples), 1500000, range_mm,
    ) + struct.pack(f"<{len(samples)}H", *samples)
    header = HEADER_STRUCT.pack(SYNC, len(profile_payload), MSG_ID_OS_MONO_PROFILE, 0, 0)
    body = header + profile_payload
    checksum = sum(body) & 0xFFFF
    return body + struct.pack("<H", checksum)
