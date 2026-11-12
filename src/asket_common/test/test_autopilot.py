"""The autopilot type, read from a raw HEARTBEAT as MAVROS republishes it."""

from asket_common.autopilot import from_mavlink, heartbeat_fields, label

# Captured from MAVROS (/uas1/mavlink_source) fed a PX4 heartbeat:
# custom_mode 0, type 11 (surface boat), autopilot 12, base_mode 0x81,
# system_status 4, mavlink_version 3.
PX4_PAYLOAD = [324553889531559936, 3]


def _payload(autopilot, mav_type=11):
    word = (mav_type << 32) | (autopilot << 40) | (0x81 << 48) | (4 << 56)
    return [word, 3]


def test_the_captured_px4_heartbeat_decodes():
    fields = heartbeat_fields(PX4_PAYLOAD)
    assert fields["autopilot"] == 12
    assert fields["type"] == 11
    assert from_mavlink(0, 1, PX4_PAYLOAD) == "px4"


def test_ardupilot_and_others():
    assert from_mavlink(0, 1, _payload(3)) == "ardupilot"
    assert from_mavlink(0, 1, _payload(8)) == "other:8"


def test_only_the_autopilot_components_heartbeat_counts():
    assert from_mavlink(0, 191, _payload(3)) is None   # a companion / GCS
    assert from_mavlink(30, 1, _payload(3)) is None    # not a HEARTBEAT


def test_labels():
    assert label("px4") == "PX4"
    assert label("ardupilot") == "ArduPilot"
    assert label(None) == "not detected"
