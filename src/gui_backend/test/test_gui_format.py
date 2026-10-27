"""Battery time left reads the same in the top bar and the Power panel.

`lib/format.js` is pure, so Node runs it directly. Skips without Node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[2] / "asket_gui"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not present")


def _endurance(values):
    script = (
        "import { endurance } from './src/lib/format.js';\n"
        f"console.log(JSON.stringify({json.dumps(values)}.map((v) => endurance(v))));\n"
    )
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60, cwd=str(GUI))
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_endurance_is_hours_and_minutes_with_no_seconds():
    assert _endurance([7530, 3600, 2712, 59]) == ["2 h 05 min", "1 h 00 min", "45 min", "0 min"]


def test_both_places_use_the_same_formatter():
    strip = (GUI / "src" / "components" / "StatusStrip.jsx").read_text()
    power = (GUI / "src" / "panels" / "PowerPanel.jsx").read_text()
    assert "endurance(power.endurance_s)" in strip
    assert "endurance(power.endurance_s)" in power
