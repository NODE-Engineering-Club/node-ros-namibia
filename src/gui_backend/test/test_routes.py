"""Routes: stored on the GUI server, deployed to mission_manager, followed live.

Three promises:

* the Repo's files are validated and kept on the server (route_store, /api/routes);
* "Deploy" sends the STORED waypoints and is confirmed only by mission_manager's
  own /mission/status, published after the command, saying RUNNING — and a
  refusal is reported in the vessel's words, not as a timeout;
* progress (which waypoint is passed / current) comes from that status, and the
  simulator produces it by actually steering through the waypoints.

No ROS: the adapters and predicates are plain functions, and the simulator runs
in-process.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from asket_sim.core.world import SimWorld, WorldConfig
from fastapi.testclient import TestClient

from gui_backend.core import adapters
from gui_backend.core.commands import (
    CMD_ABORT_ROUTE,
    CMD_DEPLOY_ROUTE,
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_PENDING,
    route_refused,
    route_running,
    route_stopped,
)
from gui_backend.core.hub import Hub
from gui_backend.core.ros_source import RosSource
from gui_backend.core.route_store import RouteError, RouteStore, validate
from gui_backend.core.server import create_app
from gui_backend.core.sim_source import SimSource


def _route_near(origin_lat, origin_lon, name="BAY_TEST"):
    """A small square ~60 m on a side next to the simulated boat."""
    d = 0.0006
    return {
        "name": name,
        "cruise_speed_ms": 1.5,
        "waypoints": [
            {"lat": origin_lat + d, "lon": origin_lon},
            {"lat": origin_lat + d, "lon": origin_lon + d},
            {"lat": origin_lat, "lon": origin_lon + d},
        ],
    }


# -- the store ---------------------------------------------------------------


def test_a_bad_waypoint_is_refused_by_number(tmp_path):
    with pytest.raises(RouteError, match="waypoint 2: latitude 140"):
        validate({"name": "x", "waypoints": [{"lat": 1, "lon": 2}, {"lat": 140, "lon": 2}]})
    with pytest.raises(RouteError, match="no waypoints"):
        validate({"name": "x", "waypoints": []})
    with pytest.raises(RouteError, match="no name"):
        validate({"waypoints": [{"lat": 1, "lon": 2}]})
    with pytest.raises(RouteError, match="numeric"):
        validate({"name": "x", "waypoints": [{"lat": "north", "lon": 2}]})


def test_saved_routes_are_listed_read_and_deleted(tmp_path):
    store = RouteStore(tmp_path, clock=lambda: 1000)
    saved = store.save(_route_near(-22.9, 14.5))
    assert saved["id"].startswith("bay-test-")
    assert saved["fingerprint"]
    [listed] = store.list()
    assert len(listed["waypoints"]) == 3 and listed["id"] == saved["id"]
    replaced = store.save({**_route_near(-22.9, 14.5), "name": "RENAMED"}, route_id=saved["id"])
    assert replaced["id"] == saved["id"] and store.get(saved["id"])["name"] == "RENAMED"
    with pytest.raises(RouteError, match="no route"):
        store.save(_route_near(-22.9, 14.5), route_id="missing-000000")
    assert store.get(saved["id"])["waypoints"][0]["lat"] == pytest.approx(-22.8994)
    store.delete(saved["id"])
    assert store.list() == []
    with pytest.raises(RouteError):
        store.get(saved["id"])


def test_a_route_id_cannot_escape_the_folder(tmp_path):
    with pytest.raises(RouteError, match="not a route id"):
        RouteStore(tmp_path).get("../../etc/passwd")


def test_the_deployment_record_keeps_the_waypoints_that_were_sent(tmp_path):
    store = RouteStore(tmp_path)
    route = store.save(_route_near(-22.9, 14.5))
    store.record_deployment(route, 42)
    assert store.deployed()["waypoints"] == route["waypoints"]
    assert "waypoints" not in store.deployed_summary()
    assert store.deployed_summary()["deployed_utc_ms"] == 42
    # the record is not itself listed as a route
    assert [r["id"] for r in store.list()] == [route["id"]]


# -- predicates --------------------------------------------------------------


def _state(**route):
    return {"mission": {"route": route}}


def test_only_a_running_status_published_after_the_command_confirms_it():
    check = route_running(seq_before=5, total_waypoints=3)
    # the same RUNNING that was there before the command: not a confirmation
    assert not check(_state(seq=5, state="RUNNING", total_waypoints=3))
    # a different route's RUNNING
    assert not check(_state(seq=6, state="RUNNING", total_waypoints=4))
    assert check(_state(seq=6, state="RUNNING", total_waypoints=3))
    assert route_stopped(5)(_state(seq=6, state="ABORTED"))
    assert not route_stopped(5)(_state(seq=6, state="RUNNING"))


def test_a_refusal_is_reported_in_the_vessels_words():
    check = route_refused(reply_before=1, command=CMD_DEPLOY_ROUTE)
    reply = {"seq": 2, "command": CMD_DEPLOY_ROUTE, "success": False,
             "message": "Mission already running — call /mission/abort first"}
    assert check(_state(reply=reply)) == reply["message"]
    assert check(_state(reply={**reply, "seq": 1})) is None        # an old reply
    assert check(_state(reply={**reply, "success": True})) is None


def test_mission_status_message_becomes_route_fields():
    msg = SimpleNamespace(state=1, message="Waypoint 2/5 completed",
                          current_waypoint=2, total_waypoints=5)
    assert adapters.route_status_from_ros(msg) == {
        "state": "RUNNING", "message": "Waypoint 2/5 completed",
        "current_waypoint": 2, "total_waypoints": 5,
    }
    assert adapters.route_status_from_ros(
        SimpleNamespace(state=9, message="", current_waypoint=0, total_waypoints=0)
    )["state"] == "UNKNOWN(9)"


def test_ros_source_without_mission_manager_says_unknown_not_idle(tmp_path):
    source = object.__new__(RosSource)
    source.config = {"routes": {"root": str(tmp_path)}}
    source.latest = {}
    assert source._route_payload()["state"] is None
    assert source._mission_payload() is None


def test_ros_source_reports_route_progress_without_a_recorder(tmp_path):
    source = object.__new__(RosSource)
    source.config = {"routes": {"root": str(tmp_path)}}
    source.latest = {"route_status": SimpleNamespace(message=SimpleNamespace(
        state=1, message="", current_waypoint=1, total_waypoints=3))}
    source._route_seq = 4
    payload = source._mission_payload()
    assert payload["route"]["state"] == "RUNNING"
    assert payload["route"]["current_waypoint"] == 1
    assert payload["route"]["seq"] == 4


# -- end to end on the simulator ----------------------------------------------


@pytest.fixture
def sim(tmp_path):
    cfg = WorldConfig()
    source = SimSource(SimWorld(cfg), missions_root=tmp_path / "missions",
                       routes_root=tmp_path / "routes")
    hub = Hub(source)
    route = hub.routes.save(_route_near(cfg.origin_lat, cfg.origin_lon))
    return source, hub, route


def _run(source, hub, seconds, until=None):
    t = 0.0
    source.step(0.0)
    while t < seconds:
        t += 0.5
        source.step(t)
        hub._update_commands(source.now_utc_ms())
        if until and until():
            return True
    return False


def test_deploy_is_confirmed_by_the_vessel_and_waypoints_are_passed_in_order(sim):
    source, hub, route = sim
    result = hub.issue_command(CMD_DEPLOY_ROUTE, {"route_id": route["id"]})
    assert result["status"] == STATUS_PENDING
    assert hub.routes.deployed()["route_id"] == route["id"]

    _run(source, hub, 2)
    [cmd] = [c for c in hub.commands.history if c.name == CMD_DEPLOY_ROUTE]
    assert cmd.status == STATUS_CONFIRMED

    seen = []

    def progress():
        r = source.state()["mission"]["route"]
        if not seen or seen[-1] != r["current_waypoint"]:
            seen.append(r["current_waypoint"])
        return r["state"] == "SUCCEEDED"

    assert _run(source, hub, 600, until=progress), seen
    assert seen == sorted(seen) and seen[-1] == 3
    route_state = source.state()["mission"]["route"]
    assert route_state["total_waypoints"] == 3
    assert route_state["deployed"]["fingerprint"] == route["fingerprint"]


def test_a_second_deploy_while_running_is_refused_before_sending(sim):
    source, hub, route = sim
    hub.issue_command(CMD_DEPLOY_ROUTE, {"route_id": route["id"]})
    _run(source, hub, 1)
    again = hub.issue_command(CMD_DEPLOY_ROUTE, {"route_id": route["id"]})
    assert again["status"] == STATUS_FAILED
    assert "already running" in again["detail"]


def test_abort_stops_the_route_and_is_confirmed(sim):
    source, hub, route = sim
    hub.issue_command(CMD_DEPLOY_ROUTE, {"route_id": route["id"]})
    _run(source, hub, 1)
    hub.issue_command(CMD_ABORT_ROUTE, {})
    _run(source, hub, 1)
    [cmd] = [c for c in hub.commands.history if c.name == CMD_ABORT_ROUTE]
    assert cmd.status == STATUS_CONFIRMED
    assert source.state()["mission"]["route"]["state"] == "ABORTED"


def test_an_unknown_route_fails_with_its_name(sim):
    _source, hub, _route = sim
    result = hub.issue_command(CMD_DEPLOY_ROUTE, {"route_id": "nope-000000"})
    assert result["status"] == STATUS_FAILED
    assert "nope-000000" in result["detail"]


def test_deploy_is_blocked_over_a_no_go_preflight(sim):
    source, hub, route = sim
    real_snapshot = source.snapshot

    def nogo(stream, detail, cursor=0):
        if stream == "diagnostics":
            return SimpleNamespace(payload={"go": False, "summary": "NO-GO — Battery"})
        return real_snapshot(stream, detail, cursor)

    source.snapshot = nogo
    result = hub.issue_command(CMD_DEPLOY_ROUTE, {"route_id": route["id"]})
    assert result["status"] == STATUS_FAILED
    assert "pre-flight" in result["detail"]


# -- HTTP ------------------------------------------------------------------------


def test_routes_api_upload_list_get_delete(sim):
    _source, hub, _route = sim
    with TestClient(create_app(hub, static_dir=None, tiles_path=None, ping_interval_s=0.05)) as c:
        bad = c.post("/api/routes", json={"name": "x", "waypoints": [{"lat": 99, "lon": 0}]})
        assert bad.status_code == 400 and "latitude 99" in bad.json()["error"]

        up = c.post("/api/routes", json=_route_near(-22.9, 14.5, name="UPLOADED"))
        assert up.status_code == 200
        rid = up.json()["route"]["id"]
        names = [r["name"] for r in c.get("/api/routes").json()["routes"]]
        assert "UPLOADED" in names
        assert len(c.get(f"/api/routes/{rid}").json()["waypoints"]) == 3
        put = c.put(f"/api/routes/{rid}", json=_route_near(-22.9, 14.5, name="EDITED"))
        assert put.status_code == 200 and put.json()["route"]["id"] == rid
        assert c.put("/api/routes/missing-000000", json=_route_near(-22.9, 14.5)).status_code == 404
        assert c.delete(f"/api/routes/{rid}").status_code == 200
        assert c.get(f"/api/routes/{rid}").status_code == 404
        assert c.get("/api/routes/deployed").json() == {"deployed": None}


# -- shipped routes ------------------------------------------------------------

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "routes"


def test_shipped_plot_routes_are_valid_closed_loops():
    files = sorted(SHIPPED.glob("*.json"))
    assert {f.stem for f in files} == {
        "na-sb-n-08-kelp", "na-sb-n-09-kelp",
        "na-sb-l-26-nets", "na-sb-l-27-nets", "na-sb-l-28-nets",
    }
    for f in files:
        doc = json.loads(f.read_text())
        route = validate(doc)
        wps = route["waypoints"]
        # Four corners, then back to the first: the full loop of the plot.
        assert len(wps) == 5 and wps[0] == wps[4], f.name
        assert len({(w["lat"], w["lon"]) for w in wps}) == 4, f.name


def test_seed_adds_each_route_once_and_respects_the_operator(tmp_path):
    store = RouteStore(tmp_path / "routes", clock=lambda: 1)
    added = store.seed(SHIPPED)
    assert len(added) == 5
    assert {r["id"] for r in store.list()} == set(added)

    # Edited: kept as edited. Deleted: stays deleted. Restart: nothing re-added.
    edited = store.get("na-sb-n-08-kelp")
    store.save({**edited, "name": "N08 EDITED"}, route_id="na-sb-n-08-kelp")
    store.delete("na-sb-l-28-nets")
    assert store.seed(SHIPPED) == []
    ids = {r["id"] for r in store.list()}
    assert "na-sb-l-28-nets" not in ids
    assert store.get("na-sb-n-08-kelp")["name"] == "N08 EDITED"


def test_seed_without_a_seed_dir_does_nothing(tmp_path):
    store = RouteStore(tmp_path / "routes")
    assert store.seed(None) == []
    assert store.seed(tmp_path / "missing") == []
    assert store.list() == []
