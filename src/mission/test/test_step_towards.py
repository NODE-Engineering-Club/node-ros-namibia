"""Long legs are sailed in steps that stay inside Nav2's planning window."""
import importlib.util
import math
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "geo_utils", Path(__file__).resolve().parents[1] / "mission" / "geo_utils.py")
geo_utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geo_utils)


def test_a_near_waypoint_is_the_goal():
    assert geo_utils.step_towards(0, 0, 30, 40, 50) == (30, 40, False)


def test_a_far_waypoint_gets_a_step_on_the_line_to_it():
    x, y, intermediate = geo_utils.step_towards(10, 10, 310, 410, 50)
    assert intermediate and math.isclose(math.hypot(x - 10, y - 10), 50)
    assert math.isclose((y - 10) / (x - 10), 400 / 300)


def test_stepping_arrives():
    x, y, steps, intermediate = 0.0, 0.0, 0, True
    while intermediate:
        x, y, intermediate = geo_utils.step_towards(x, y, 2700, -1000, 50)
        steps += 1
    assert (x, y) == (2700, -1000) and steps == 58
