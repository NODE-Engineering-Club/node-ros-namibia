"""The readiness line at the top of the cockpit: "READY TO GO" or the one
reason it is not.

`lib/readiness.js` is pure, so Node runs it directly. Each case is a state an
operator meets on the beach, and the reason it must produce. The order matters
as much as the sentences: a lost RC link must never be reported as "run the
pre-flight", and a boat that sends nothing must never read as ready.

Skips when Node is absent — a cross-language check, not a build dependency.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[2] / "asket_gui"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not present")

NOW = 1_800_000_000_000
GO = {"go": True, "run_utc_ms": NOW - 60_000, "items": []}
NOGO = {
    "go": False,
    "run_utc_ms": NOW - 60_000,
    "items": [
        {"name": "Pico firmware version", "status": "PASS"},
        {"name": "Battery", "status": "FAIL"},
    ],
}
PICO_OK = {"mode": "MANUAL", "armed": True, "rc_link_ok": True}
BASE = {
    "connected": True,
    "receiving": True,
    "report": GO,
    "pico": PICO_OK,
    "link": {"active_link": "wifi"},
    "profile": "full",
    "nowMs": NOW,
}

CASES = [
    ("ready", {}, "ok", "READY TO GO"),
    ("offline", {"connected": False}, "alarm", "NOT READY: no connection to the boat"),
    ("silent", {"receiving": False}, "alarm", "NOT READY: no data from the boat"),
    (
        "rc lost beats no-go",
        {"pico": {**PICO_OK, "rc_link_ok": False}, "report": NOGO},
        "alarm",
        "RC transmitter link lost",
    ),
    ("rc latched", {"pico": {**PICO_OK, "rc_lost_latched": True}}, "alarm", "flip Ch7"),
    ("ch8 down", {"pico": {**PICO_OK, "mode": "ESTOP", "armed": False}}, "alarm", "Ch8"),
    ("shore link down", {"link": {"active_link": "none"}}, "alarm", "shore station"),
    ("no pre-flight", {"report": None}, "warn", "NOT READY: run the pre-flight"),
    ("no-go names the failing check", {"report": NOGO}, "alarm", "NO-GO — Battery"),
    (
        "old pre-flight",
        {"report": {**GO, "run_utc_ms": NOW - 2 * 3600_000}},
        "warn",
        "over an hour old",
    ),
    ("disarmed", {"pico": {**PICO_OK, "armed": False}}, "warn", "disarmed"),
    ("armed unknown", {"pico": {"mode": "MANUAL", "rc_link_ok": True}}, "warn", "armed"),
    ("beacon link", {"profile": "minimal"}, "warn", "weak link"),
]


@pytest.fixture(scope="module")
def results() -> dict:
    cases = {name: {**BASE, **override} for name, override, _, _ in CASES}
    script = (
        "import { readiness } from './src/lib/readiness.js';\n"
        f"const cases = {json.dumps(cases)};\n"
        "const out = {};\n"
        "for (const [k, v] of Object.entries(cases)) out[k] = readiness(v);\n"
        "console.log(JSON.stringify(out));\n"
    )
    run = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60, cwd=str(GUI),
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.parametrize("name,override,level,text", CASES, ids=[c[0] for c in CASES])
def test_readiness(results, name, override, level, text):
    got = results[name]
    assert got["level"] == level, got
    assert text in got["text"], got
    assert got["ready"] is (level == "ok")
