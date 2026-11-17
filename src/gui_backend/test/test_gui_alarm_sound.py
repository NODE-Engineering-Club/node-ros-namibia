"""Which alarms beep: red ones only, and a silence never mutes a new one.

`lib/alarmSound.js`'s decision is pure, so Node runs it directly. Skips when
Node is absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[2] / "asket_gui"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not present")

SCRIPT = """
import { audibleAlarms, silenceAll, SILENCE_MS } from './src/lib/alarmSound.js';
const red = { key: 'rc_link_lost', severity: 'alarm' };
const amber = { key: 'battery_low', severity: 'warn' };
const other = { key: 'geofence_outside', severity: 'alarm' };
const now = 1000;
const silenced = silenceAll([red, amber], now);
console.log(JSON.stringify({
  amberOnly: audibleAlarms([amber], {}, now).map((a) => a.key),
  red: audibleAlarms([red, amber], {}, now).map((a) => a.key),
  silenced: audibleAlarms([red], silenced, now + 1).map((a) => a.key),
  newOne: audibleAlarms([red, other], silenced, now + 1).map((a) => a.key),
  expired: audibleAlarms([red], silenced, now + SILENCE_MS + 1).map((a) => a.key),
  silencedKeys: Object.keys(silenced),
}));
"""


@pytest.fixture(scope="module")
def out():
    run = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT],
        capture_output=True, text=True, timeout=60, cwd=str(GUI),
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_amber_never_beeps(out):
    assert out["amberOnly"] == []
    assert out["silencedKeys"] == ["rc_link_lost"]


def test_red_beeps(out):
    assert out["red"] == ["rc_link_lost"]


def test_silence_mutes_what_is_showing_but_not_a_new_red_alarm(out):
    assert out["silenced"] == []
    assert out["newOne"] == ["geofence_outside"]


def test_silence_is_temporary(out):
    assert out["expired"] == ["rc_link_lost"]
