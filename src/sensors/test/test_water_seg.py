"""Floating-obstacle logic of sensors/water_seg.py on synthetic label maps (no model, no ROS)."""

import numpy as np

from sensors.water_seg import OBSTACLE, SKY, WATER, floating_obstacles


def scene(h=120, w=160):
    lab = np.full((h, w), WATER, np.uint8)
    lab[:40] = SKY
    return lab


def test_floating_object_on_water_is_reported_with_stereo_distance():
    lab = scene()
    lab[70:90, 60:90] = OBSTACLE  # a kelp mat / branch in the middle of the water
    depth = np.zeros(lab.shape, np.uint16)
    depth[70:90, 60:90] = 6500
    objs, frac = floating_obstacles(lab, depth, fx=100.0, cx=80.0)
    assert [o["label"] for o in objs] == ["obstacle"]
    assert objs[0]["distance_m"] == 6.5
    assert frac > 0.5


def test_shore_band_with_water_below_is_shore():
    lab = scene()
    lab[30:45, :] = OBSTACLE  # land along the horizon, water beneath it
    objs, _ = floating_obstacles(lab, None, fx=100.0, cx=80.0)
    assert [o["label"] for o in objs] == ["shore"]
    assert objs[0]["distance_m"] is None  # no depth: not guessed


def test_obstacle_with_sky_below_is_not_on_the_water():
    lab = np.full((120, 160), SKY, np.uint8)
    lab[100:, :] = WATER
    lab[20:40, 60:90] = OBSTACLE  # something up in the sky region (a bird, a mast top)
    objs, _ = floating_obstacles(lab, None, fx=100.0, cx=80.0)
    assert objs == []


def test_no_water_in_view_reports_nothing():
    lab = np.full((120, 160), OBSTACLE, np.uint8)
    lab[:20] = SKY
    objs, frac = floating_obstacles(lab, None, fx=100.0, cx=80.0)
    assert objs == [] and frac == 0.0


def test_close_object_at_bottom_edge_needs_water_around_it():
    lab = scene()
    lab[100:, 70:90] = OBSTACLE  # close, cut by the bottom of the frame, water on both sides
    objs, _ = floating_obstacles(lab, None, fx=100.0, cx=80.0)
    assert len(objs) == 1
    lab = scene()
    lab[90:, :] = OBSTACLE  # bow, or a kelp mat right in front: still reported (no self mask yet)
    objs, _ = floating_obstacles(lab, None, fx=100.0, cx=80.0)
    assert len(objs) == 1
    lab = scene()
    lab[100:, 70:90] = OBSTACLE
    lab[95:, 60:100] = SKY  # (nonsense labels) mostly non-water around it: not on the water
    lab[100:, 70:90] = OBSTACLE
    objs, _ = floating_obstacles(lab, None, fx=100.0, cx=80.0)
    assert objs == []
