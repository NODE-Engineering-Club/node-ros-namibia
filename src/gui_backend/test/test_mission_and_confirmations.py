""""Drop to MANUAL" and "Start recording" can be confirmed by the real vessel.

Two buttons that could never succeed on the boat, however well it behaved:

* "Drop to MANUAL" requests MANUAL, but was confirmed by a latched ESTOP — so
  every press ended "failed" after the timeout.
* The mission state was hard-coded IDLE in ``RosSource.state()``, so "Start
  recording" never confirmed and "Stop" confirmed instantly, recording or not.

No ROS: the adapters and predicates are plain functions.
"""

from types import SimpleNamespace

from gui_backend.core import adapters
from gui_backend.core.commands import (
    propulsion_cut_confirmed,
    recording_confirmed,
    software_authority_removed,
)
from gui_backend.core.ros_source import RosSource


def test_drop_to_manual_is_confirmed_by_manual_or_estop():
    assert software_authority_removed({"pico": {"mode": "MANUAL"}})
    assert software_authority_removed({"pico": {"mode": "ESTOP"}})
    assert not software_authority_removed({"pico": {"mode": "AUTONOMOUS"}})
    assert not software_authority_removed({})
    # the old predicate, which the real stack could never satisfy
    assert not propulsion_cut_confirmed({"pico": {"mode": "MANUAL"}})


def test_the_ros_source_says_what_its_estop_button_does():
    source = object.__new__(RosSource)
    source.config = {"estop": {"mode": "mode_request_manual", "label": "Drop to MANUAL"}}
    assert source.describe()["estop"]["mode"] == "mode_request_manual"


def test_no_recorder_report_confirms_neither_start_nor_stop():
    assert not recording_confirmed(True)({"mission": {}})
    assert not recording_confirmed(False)({"mission": {}})
    assert recording_confirmed(True)({"mission": {"state": "RECORDING"}})
    assert recording_confirmed(False)({"mission": {"state": "IDLE"}})


def _mission_msg(**kw):
    base = dict(state=1, mission_name="bay_01", mission_dir="/data/missions/bay_01",
                elapsed_s=12.34, bytes_written=2048, disk_free_bytes=10**9,
                estimated_remaining_s=3600.4, error_message="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_mission_state_message_becomes_the_panel_payload():
    payload = adapters.mission_from_ros(_mission_msg())
    assert payload["state"] == "RECORDING"
    assert payload["name"] == "bay_01"
    assert payload["elapsed_s"] == 12.3
    assert payload["estimated_remaining_s"] == 3600


def test_zero_remaining_is_unknown_not_none_left():
    assert adapters.mission_from_ros(_mission_msg(estimated_remaining_s=0.0))[
        "estimated_remaining_s"] is None


def test_ros_source_state_carries_the_recorder_report(tmp_path):
    source = object.__new__(RosSource)
    source.config = {"missions": {"root": str(tmp_path)}}
    source.latest = {"mission_state": SimpleNamespace(message=_mission_msg(state=0))}
    payload = source._mission_payload()
    assert payload["state"] == "IDLE"
    assert payload["missions"] == []


def test_ros_source_without_a_recorder_reports_nothing():
    source = object.__new__(RosSource)
    source.config = {}
    source.latest = {}
    assert source._mission_payload() is None
