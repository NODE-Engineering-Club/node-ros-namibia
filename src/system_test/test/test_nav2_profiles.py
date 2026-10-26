"""The Nav2 parameter files load, and the survey profile is what it says.

Both faults below were in nav2_params.yaml and stopped Nav2 entirely; neither
shows up until a node parses the file, so they are checked here as text:

* collision_monitor polygon ``points`` as a nested YAML list. Jazzy wants a
  string ("[[x, y], ...]"); a nested list makes the whole generated params
  file unparseable, and every Nav2 node exited at start-up.
* ``nav2_costmap_2d::KeepOutFilter``: no such class (it is ``KeepoutFilter``).

Verified against Nav2 Jazzy 1.3.13 in a ROS image: with these fixed both
profiles reach "Managed nodes are active", the survey fence blocks a plan out
of it, and velocity_smoother caps 2 m/s to 0.8 m/s.
"""

import ast
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "src" / "bringup" / "config"
LAUNCH = ROOT / "src" / "bringup" / "launch" / "njord.launch.py"
PROFILES = ("nav2_params.yaml", "nav2_params_survey.yaml")

#: The costmap plugin classes Nav2 Jazzy actually provides that these files use.
KNOWN_PLUGINS = {
    "nav2_costmap_2d::ObstacleLayer",
    "nav2_costmap_2d::InflationLayer",
    "nav2_costmap_2d::KeepoutFilter",
    "nav2_costmap_2d::StaticLayer",
}


def _load(name):
    return yaml.safe_load((CONFIG / name).read_text())


def _costmap(doc, which):
    return doc[which][which]["ros__parameters"]


@pytest.mark.parametrize("name", PROFILES)
def test_polygon_points_are_strings(name):
    cm = _load(name)["collision_monitor"]["ros__parameters"]
    for polygon in cm["polygons"]:
        points = cm[polygon].get("points")
        if points is not None:
            assert isinstance(points, str), f"{name}: {polygon}.points must be a string"


@pytest.mark.parametrize("name", PROFILES)
def test_costmap_plugins_are_real_classes(name):
    doc = _load(name)
    for which in ("global_costmap", "local_costmap"):
        params = _costmap(doc, which)
        for plugin in params["plugins"]:
            cls = params[plugin]["plugin"]
            assert cls in KNOWN_PLUGINS, f"{name}: {which}.{plugin} is {cls!r}"


def test_the_survey_profile_has_no_obstacle_layer_and_a_fence():
    doc = _load("nav2_params_survey.yaml")
    for which in ("global_costmap", "local_costmap"):
        params = _costmap(doc, which)
        classes = [params[p]["plugin"] for p in params["plugins"]]
        assert "nav2_costmap_2d::ObstacleLayer" not in classes, which
        fence = params["geofence_layer"]
        assert fence["plugin"] == "nav2_costmap_2d::KeepoutFilter"
        assert fence["filter_info_topic"] == "/geofence/filter_info"


def test_the_survey_profile_caps_speed_and_never_reverses():
    doc = _load("nav2_params_survey.yaml")
    vs = doc["velocity_smoother"]["ros__parameters"]
    rpp = doc["controller_server"]["ros__parameters"]["FollowPath"]
    assert vs["max_velocity"][0] == rpp["desired_linear_vel"] == 0.8
    assert vs["min_velocity"][0] == 0.0
    assert rpp["allow_reversing"] is False
    assert abs(vs["max_velocity"][2]) <= 0.6 and abs(vs["min_velocity"][2]) <= 0.6


def test_the_survey_collision_monitor_watches_nothing():
    """Stated, not implied: in this profile it is not a safety layer."""
    cm = _load("nav2_params_survey.yaml")["collision_monitor"]["ros__parameters"]
    assert all(cm[p]["enabled"] is False for p in cm["polygons"])
    assert all(cm[s]["enabled"] is False for s in cm["observation_sources"])


def test_the_default_profile_is_survey():
    tree = ast.parse(LAUNCH.read_text())
    for call in ast.walk(tree):
        if (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "DeclareLaunchArgument"
                and call.args and getattr(call.args[0], "value", None) == "nav2_profile"):
            defaults = [kw.value.value for kw in call.keywords if kw.arg == "default_value"]
            assert defaults == ["survey"]
            return
    pytest.fail("nav2_profile is not declared")


def test_the_example_geofence_is_valid():
    from asket_common.geofence import load_geofence

    fence = load_geofence(yaml.safe_load((CONFIG / "geofence.example.yaml").read_text()))
    assert "EXAMPLE" in fence.name
