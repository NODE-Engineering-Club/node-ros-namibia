"""Starting a mission from the cockpit, and the motherboard v2 / BMS data behind it."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from asket_common.battery import parse_bms_csv
from asket_common.geofence import (
    fence_token, load_geofence, plan_route_fence, signed_distance_m,
)
from gui_backend.core import setup_store
from gui_backend.core.commands import CMD_DEPLOY_ROUTE, CMD_SET_MODE, CMD_START_ROUTE
from gui_backend.core.hub import Hub
from gui_backend.core.pico_state import PROTOCOL_VERSION, parse_state_line
from gui_backend.core.route_store import RouteStore
from gui_backend.core.source import CommandOutcome

V2_LINE = (
    "STATE mode=2 armed=1 relay=1 pwr=3 wantauto=0 link=1 estoplatch=0 esc=1 vrail=16.4 "
    "v12=12.0 sag=0 pwmL=1500 pwmR=1500 pwmlim=1760 thr=992 yaw=992 ch7=0 ch8=1811 rc=1 "
    "dtrc=4 maxgap=12 lq=100 rssi=-40 rcok=900 rcbad=0 jet=1 pg=1 leak=0 tenc=nan rh=nan warn=16"
)
SEEDS = Path(__file__).resolve().parents[1] / "config" / "routes"


class Source:
    def __init__(self):
        self.sent = []
        self.go = True
        self.pico = {"mode": "MANUAL", "armed": True, "rc_link_ok": True, "link_live": True,
                     "rc_channel8_raw": 1811}
        self.route = {"state": "IDLE", "seq": 1}

    def now_utc_ms(self):
        return 1_000

    def state(self):
        return {"pico": self.pico, "mission": {"route": self.route}}

    def snapshot(self, stream, detail):
        return SimpleNamespace(payload={"go": self.go, "summary": "GO" if self.go else "NO-GO"})

    def describe(self):
        return {}

    def send_command(self, name, args):
        self.sent.append((name, args.get("mode")))
        return CommandOutcome(True, "sent")


@pytest.fixture
def rig(tmp_path):
    source = Source()
    hub = Hub(source)
    hub.routes = RouteStore(tmp_path)
    assert set(hub.routes.seed(SEEDS)) >= {"nav-towards-shore", "nav-away-shore"}
    return hub, source


def test_the_two_missions_are_seeded_in_waypoint_order(rig):
    hub, _ = rig
    assert len(hub.routes.get("nav-towards-shore")["waypoints"]) == 35
    assert len(hub.routes.get("nav-away-shore")["waypoints"]) == 33


def test_route_goes_out_only_after_the_pico_reports_autonomous(rig):
    hub, source = rig
    assert hub.issue_command(CMD_START_ROUTE, {"route_id": "nav-away-shore"})["status"] == "pending"
    assert source.sent == [(CMD_SET_MODE, "AUTONOMOUS")]
    hub._update_commands(1_100)
    assert len(source.sent) == 1          # still MANUAL: no route yet
    source.pico = {**source.pico, "mode": "AUTONOMOUS"}
    hub._update_commands(1_200)
    assert source.sent[1] == (CMD_DEPLOY_ROUTE, None)


def test_pico_refusing_autonomy_withdraws_the_request_and_sends_no_route(rig):
    hub, source = rig
    hub.issue_command(CMD_START_ROUTE, {"route_id": "nav-away-shore"})
    hub._update_commands(20_000)          # past the confirmation timeout
    assert source.sent == [(CMD_SET_MODE, "AUTONOMOUS"), (CMD_SET_MODE, "MANUAL")]


@pytest.mark.parametrize("change,word", [
    ({"rc_channel8_raw": 992}, "CH3"),
    ({"rc_link_ok": False}, "RC link"),
    ({"link_live": False}, "Jetson"),
    ({"armed": False}, "armed"),
])
def test_start_is_refused_without_sending_anything(rig, change, word):
    hub, source = rig
    source.pico = {**source.pico, **change}
    result = hub.issue_command(CMD_START_ROUTE, {"route_id": "nav-away-shore"})
    assert result["status"] == "failed" and word in result["detail"]
    assert source.sent == []


def test_start_needs_a_go_preflight(rig):
    hub, source = rig
    source.go = False
    assert hub.issue_command(CMD_START_ROUTE, {"route_id": "nav-away-shore"})["status"] == "failed"
    assert source.sent == []


def test_start_can_be_forced_over_a_nogo_preflight_and_is_logged(rig, monkeypatch, tmp_path):
    hub, source = rig
    monkeypatch.setattr(setup_store, "DEFAULT_PATH", tmp_path / "setup" / "asket_setup.yaml")
    source.go = False
    result = hub.issue_command(
        CMD_START_ROUTE, {"route_id": "nav-away-shore", "force_nogo": True, "by": "op"},
    )
    assert result["status"] != "failed"
    assert source.sent == [(CMD_SET_MODE, "AUTONOMOUS")]
    lines = (tmp_path / "setup" / "asket_overrides.jsonl").read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["action"] == "route_forced_over_nogo"
    assert record["by"] == "op"
    assert "NO-GO" in record["preflight_summary"]


def test_motherboard_v2_line_is_fully_understood():
    state = parse_state_line(V2_LINE)
    assert state.parsed and not state.unknown_keys and not state.version_mismatch
    assert state.firmware_version == PROTOCOL_VERSION
    assert state.rc_link_ok is True and state.ch7_arm_high is True
    assert state.rc_channel7_raw is None and state.bus_voltage == 16.4
    assert state.enclosure_temp_c is None     # "nan" is no reading


def test_bms_row():
    row = parse_bms_csv("41254,3999,3987,3986,4000,15972,24.5,0,-2150,1,1,0,0,0,0,0,0,0,1")
    assert row["cells_v"] == [3.999, 3.987, 3.986, 4.0]
    assert row["pack_v"] == 15.972 and row["current_a"] == -2.15 and row["temp_c"] == 24.5
    assert row["dsg"] is True and row["faults"] == []


def test_bms_without_comms_reports_no_measurements():
    row = parse_bms_csv("500,0,0,0,0,0,nan,1,0,0,0,1,0,0,0,0,0,3,0")
    assert row["comms"] is False and row["cells_v"] == []
    assert row["pack_v"] is None and row["current_a"] is None and row["temp_c"] is None
    assert row["faults"] == ["uvL"] and row["retries"] == 3


@pytest.mark.parametrize("line", [
    "# BMS banner", "t_ms,c1_mV,c2_mV", "", "1,2,3", "x,0,0,0,0,0,nan,1,0,0,0,0,0,0,0,0,0,0,1",
])
def test_bms_non_data_lines_are_skipped(line):
    assert parse_bms_csv(line) is None


# -- a mission anywhere: the fence follows it -----------------------------------

FAR = [{"lat": -26.70, "lon": 15.20}, {"lat": -26.705, "lon": 15.205}, {"lat": -26.71, "lon": 15.20}]
BOAT = (-26.6995, 15.2)


def test_auto_fence_contains_the_boat_and_every_leg_with_margin():
    document, token, problem = plan_route_fence(None, FAR, BOAT, "far")
    assert problem is None and token
    fence = load_geofence(document)
    assert fence_token(fence.polygon) == token
    for a, b in zip(FAR, FAR[1:]):
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            lat = a["lat"] + t * (b["lat"] - a["lat"])
            lon = a["lon"] + t * (b["lon"] - a["lon"])
            assert signed_distance_m(fence.polygon, lat, lon) > 50.0
    assert signed_distance_m(fence.polygon, *BOAT) > 50.0


def test_an_auto_fence_is_replaced_but_a_hand_written_one_is_not():
    auto, _, _ = plan_route_fence(None, FAR, BOAT, "first")
    assert plan_route_fence(auto, FAR, BOAT, "again")[0] is not None
    hand = {"geofence": {"name": "day block", "polygon": [
        [-26.63, 15.10], [-26.63, 15.11], [-26.64, 15.11], [-26.64, 15.10]]}}
    document, token, problem = plan_route_fence(hand, FAR, BOAT, "far")
    assert document is None and token is None and "hand-written" in problem


def test_a_route_inside_a_hand_written_fence_keeps_it():
    hand = {"geofence": {"name": "big", "polygon": [
        [-26.69, 15.19], [-26.69, 15.22], [-26.72, 15.22], [-26.72, 15.19]]}}
    document, token, problem = plan_route_fence(hand, FAR, BOAT, "far")
    assert document is None and problem is None
    assert token == fence_token(load_geofence(hand).polygon)


def test_a_boat_far_from_waypoint_1_is_refused():
    assert "Drive closer" in plan_route_fence(None, FAR, (-26.60, 15.2), "far")[2]


def test_route_waits_for_the_mission_fence_as_well_as_the_pico(rig):
    hub, source = rig
    source.apply_route_fence = lambda route: ("tok", None)
    hub.issue_command(CMD_START_ROUTE, {"route_id": "nav-away-shore"})
    source.pico = {**source.pico, "mode": "AUTONOMOUS"}
    hub._update_commands(1_100)
    assert (CMD_DEPLOY_ROUTE, None) not in source.sent
    base_state = source.state
    source.state = lambda: {**base_state(), "geofence_active": "tok"}
    hub._update_commands(1_200)
    assert (CMD_DEPLOY_ROUTE, None) in source.sent


def test_a_fence_problem_stops_the_start_before_anything_is_sent(rig):
    hub, source = rig
    source.apply_route_fence = lambda route: (None, "no GPS position")
    result = hub.issue_command(CMD_START_ROUTE, {"route_id": "nav-away-shore"})
    assert result["status"] == "failed" and "no GPS" in result["detail"]
    assert source.sent == []
