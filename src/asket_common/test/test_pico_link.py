"""pico_bridge forwards motion only to the firmware this stack drives (motherboard v2, version 6).

``asket_common.pico_link`` is the decision; ``control/pico_bridge.py`` is the
ROS node that applies it and cannot run here without ROS, so its source is
checked for using the decision rather than writing motion lines itself.
"""

import re
from pathlib import Path

import pytest

from asket_common.pico_link import EXPECTED_FIRMWARE_VERSION, FirmwareGate, version_of

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "src" / "control" / "control" / "pico_bridge.py"
V5_STATE = "STATE ver=5 mode=2 armed=0 relay=0 wantauto=0 link=1 estoplatch=0"
V2_STATE = ("STATE mode=2 armed=1 relay=1 pwr=3 wantauto=0 link=1 estoplatch=0 esc=1 "
            "vrail=16.4 v12=12.0 sag=0 pwmL=1500 pwmR=1500 pwmlim=1760 thr=992 yaw=992 "
            "ch7=0 ch8=992 rc=1 dtrc=4 maxgap=12 lq=100 rssi=-40 rcok=900 rcbad=0 jet=1 "
            "pg=1 leak=0 tenc=31.2 rh=44 warn=0")


@pytest.mark.parametrize("line,version", [
    (V2_STATE, 6),
    (V5_STATE, 5),
    ("STATE ver=4 mode=2 armed=0", 4),
    ("[VER] pico-node 5", 5),
    ("[VER] pico-node 4\r", 4),
    ("STATE mode=2 armed=0", None),            # asket_ec_pico: no ver= at all
    ("[STAT] Mode:3 Armed:Y", None),           # pico-node_v3
    ("STATE ver=x mode=2", None),
    ("[ACK] MODE AUTO requested", None),
    (None, None),
])
def test_version_of(line, version):
    assert version_of(line) == version


def test_nothing_moves_until_the_pico_is_recognised():
    gate = FirmwareGate()
    assert not gate.ok
    assert gate.motor_line(0.5, -0.5) == "PING"
    assert gate.mode_line("AUTO") is None
    assert gate.mode_line("MANUAL") == "MODE MANUAL"   # always allowed
    assert gate.observe(V2_STATE) is True
    assert gate.ok
    assert gate.motor_line(0.5, -0.5) == "0.500,-0.500"
    assert gate.mode_line("mode auto") == "MODE AUTO"


def test_a_v4_pico_gets_heartbeats_and_manual_only():
    gate = FirmwareGate()
    gate.observe("[VER] pico-node 4")
    assert gate.reported == 4 and not gate.ok
    assert gate.motor_line(1.0, 1.0) == "PING"
    assert gate.mode_line("AUTO") is None
    assert gate.mode_line("MANUAL") == "MODE MANUAL"
    assert "v4" in gate.describe() and "v6" in gate.describe()


def test_a_reflashed_pico_changes_the_answer():
    gate = FirmwareGate()
    gate.observe(V2_STATE)
    assert gate.ok
    assert gate.observe("STATE ver=4 mode=1") is True
    assert not gate.ok
    assert gate.observe("STATE ver=4 mode=1") is False   # no change, no event


def test_an_unknown_request_is_never_sent():
    gate = FirmwareGate()
    gate.observe(V5_STATE)
    assert gate.mode_line("ESTOP") is None
    assert gate.mode_line("") is None


def test_the_expected_version_is_the_firmware_the_gui_and_the_preflight_expect():
    from gui_backend.core.pico_state import PROTOCOL_VERSION
    from system_test.core.checks import EXPECTED_FIRMWARE_VERSION as PREFLIGHT

    ino = (ROOT / "firmware" / "pico-motherboard_v2" / "pico-motherboard_v2.ino").read_text()
    assert 'out.print(F("STATE mode="))' in ino and 'F(" pwr=")' in ino and 'F(" vrail=")' in ino
    assert EXPECTED_FIRMWARE_VERSION == PROTOCOL_VERSION == PREFLIGHT == 6


def test_the_bridge_sends_motion_only_through_the_gate():
    source = BRIDGE.read_text()
    assert "self._gate.motor_line(" in source
    assert "self._gate.mode_line(" in source
    assert "self._gate.observe(" in source
    # no motion line built by hand anywhere in the node
    assert not re.search(r'_write\(\s*f"\{left', source)
    assert '_write("MODE AUTO")' not in source
    assert 'expected_fw_version' in source
    assert "write_timeout=" in source, "serial writes must not block the timer"
