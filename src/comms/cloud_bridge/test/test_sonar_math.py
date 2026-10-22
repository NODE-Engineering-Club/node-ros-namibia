"""Tests for sonar_protocol.py's pure parsing/decimation logic.

IMPORTANT SCOPE NOTE: test_frame_round_trip_is_self_consistent() below
builds a synthetic packet using sonar_protocol.build_synthetic_frame(),
matching the module's own assumed field layout, and checks the parser
decodes it back correctly. That proves the parser is internally consistent
with its own stated assumptions — it does NOT prove those assumptions
(byte offsets, field widths, checksum algorithm) match the real Cerulean
os_mono_profile wire format, which was not verified against a live device
or the current docs.ceruleansonar.com API reference in this environment.
Treat a real device's first connection as the actual verification; see
sonar_protocol.py's module docstring.
"""
import pytest

from cloud_bridge.geo_utils import peak_hold_decimate
from cloud_bridge.sonar_protocol import (
    MSG_ID_OS_MONO_PROFILE,
    build_synthetic_frame,
    parse_os_mono_profile,
    parse_ping_protocol_frame,
)


def test_frame_round_trip_is_self_consistent():
    samples = [0] * 600
    samples[12] = 65000
    frame = build_synthetic_frame(ping_number=8421, range_mm=30000, gain_index=22, samples=samples)

    parsed = parse_ping_protocol_frame(frame)
    assert parsed is not None
    message_id, payload = parsed
    assert message_id == MSG_ID_OS_MONO_PROFILE

    profile = parse_os_mono_profile(payload)
    assert profile is not None
    assert profile["ping_id"] == 8421
    assert profile["range_m"] == pytest.approx(30.0)
    assert len(profile["samples"]) == 600
    assert profile["samples"][12] == 65000


def test_corrupted_checksum_is_rejected_not_trusted():
    frame = bytearray(build_synthetic_frame(1, 1000, 10, [0] * 600))
    frame[-1] ^= 0xFF  # flip bits in the checksum
    assert parse_ping_protocol_frame(bytes(frame)) is None


def test_truncated_frame_is_rejected_not_indexed_out_of_bounds():
    frame = build_synthetic_frame(1, 1000, 10, [0] * 600)
    assert parse_ping_protocol_frame(frame[:10]) is None


def test_wrong_sync_bytes_rejected():
    frame = bytearray(build_synthetic_frame(1, 1000, 10, [0] * 10))
    frame[0:2] = b"XX"
    assert parse_ping_protocol_frame(bytes(frame)) is None


def test_peak_hold_decimate_matches_600_to_100_contract():
    samples = [0] * 600
    samples[12] = 65000
    decimated = peak_hold_decimate(samples, n_bins=100, window=6)
    assert len(decimated) == 100
    assert decimated[2] > 0  # sample 12 falls in bin 12//6 = 2
    assert decimated[50] == 0
    assert all(0 <= v <= 255 for v in decimated)


def test_peak_hold_decimate_clamps_saturated_input():
    decimated = peak_hold_decimate([65535] * 600, n_bins=100, window=6)
    assert all(v == 255 for v in decimated)
