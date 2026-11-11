"""Autonomous is refused over a NO-GO pre-flight, unless deliberately forced.

An aid, not a safety: the transmitter stays the authority. The forced press
is logged, so "who sent it out over a red verdict" has an answer.
"""

import json

import pytest
from asket_sim.core.world import SimWorld, WorldConfig
from gui_backend.core import setup_store
from gui_backend.core.hub import Hub
from gui_backend.core.sim_source import SimSource
from gui_backend.core.source import Sample


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_store, "DEFAULT_PATH", tmp_path / "setup" / "asket_setup.yaml")
    return Hub(SimSource(SimWorld(WorldConfig())))


def _report(hub, monkeypatch, go):
    real = hub.source.snapshot

    def snapshot(stream, detail="full", cursor=0):
        if stream == "diagnostics":
            return Sample(stream, 1, {"go": go, "summary": "NO-GO — Battery", "run_utc_ms": 1})
        return real(stream, detail, cursor)

    monkeypatch.setattr(hub.source, "snapshot", snapshot)


def test_nogo_blocks_autonomous_and_says_how_to_get_past(hub, monkeypatch):
    _report(hub, monkeypatch, go=False)
    result = hub.issue_command("set_mode", {"mode": "AUTONOMOUS"})
    assert result["status"] == "failed"
    assert result["nogo"] is True
    assert "NO-GO" in result["detail"]
    assert "force" in result["detail"]


def test_manual_is_never_blocked(hub, monkeypatch):
    _report(hub, monkeypatch, go=False)
    assert hub.issue_command("set_mode", {"mode": "MANUAL"})["status"] != "failed"


def test_go_does_not_block(hub, monkeypatch):
    _report(hub, monkeypatch, go=True)
    assert hub.issue_command("set_mode", {"mode": "AUTONOMOUS"})["status"] != "failed"


def test_forcing_goes_through_and_is_logged(hub, monkeypatch, tmp_path):
    _report(hub, monkeypatch, go=False)
    result = hub.issue_command("set_mode", {"mode": "AUTONOMOUS", "force_nogo": True, "by": "op"})
    assert result["status"] != "failed"
    lines = (tmp_path / "setup" / "asket_overrides.jsonl").read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["action"] == "autonomous_forced_over_nogo"
    assert "NO-GO" in record["preflight_summary"]
