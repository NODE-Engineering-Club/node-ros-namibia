"""Pure-math regression tests for the projection used by sam2_prompt_node.

Unlike test_copyright/test_flake8/test_pep257 (style-only), this test
actually exercises logic: the pinhole projection sam2_prompt_node uses to
turn a LiDAR point into an image-plane (u, v) for clustering into box
prompts. It needs only numpy — no TensorRT, no CUDA, no ROS — so it is the
one part of this package that can run on a laptop and in CI, not just on
the Jetson.
"""
import numpy as np
import pytest


def project(K, point_cam):
    """point_cam: (X, Y, Z) in the camera OPTICAL frame (Z forward)."""
    x, y, z = point_cam
    assert z > 0.0, "point must be in front of the camera"
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    return fx * x / z + cx, fy * y / z + cy


@pytest.fixture
def K():
    return np.array([[700.2, 0, 320.0], [0, 696.5, 240.0], [0, 0, 1]])


def test_centre_point_projects_to_principal_point(K):
    u, v = project(K, (0.0, 0.0, 12.0))
    assert u == pytest.approx(320.0)
    assert v == pytest.approx(240.0)


def test_right_of_axis_projects_right_of_centre(K):
    u, _ = project(K, (1.0, 0.0, 10.0))
    assert u > 320.0


def test_below_axis_projects_below_centre(K):
    # Optical convention: +Y is "down" in the image.
    _, v = project(K, (0.0, 1.0, 10.0))
    assert v > 240.0


def test_farther_point_projects_closer_to_centre(K):
    """A physically larger lateral offset farther away can still land at the
    same pixel as a smaller offset nearby (this is exactly why a fixed 5m
    assumption — Defect C11 — is wrong): projecting the SAME lateral offset
    at two different ranges must converge toward the centre as range grows."""
    u_near, _ = project(K, (1.0, 0.0, 5.0))
    u_far, _ = project(K, (1.0, 0.0, 50.0))
    assert abs(u_far - 320.0) < abs(u_near - 320.0)


def test_behind_camera_point_is_rejected():
    K = np.eye(3)
    with pytest.raises(AssertionError):
        project(K, (0.0, 0.0, -1.0))
