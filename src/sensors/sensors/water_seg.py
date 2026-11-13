"""Water / sky / obstacle segmentation for collision avoidance (eWaSR).

Why a water segmenter and not more detector classes
---------------------------------------------------
To avoid something the boat does not need to know *what* it is, only that
it is not water and how far away it is. A maritime segmentation network
labels every pixel obstacle / water / sky, so kelp mats, branches, ropes at
the surface, debris and anything no detector was trained on show up as
"obstacle on the water". The buoy detector still names buoys; this layer
catches the rest.

Model: eWaSR ResNet-18 (Tersek, Zust, Kristan, Sensors 2023), trained on
MaSTr1325, Apache-2.0, https://github.com/tersekmatija/eWaSR (release 0.1.0,
``ewasr_resnet18.onnx``). Input 512x384 RGB, ImageNet mean/std normalised.
Classes: 0 obstacle, 1 water, 2 sky.

Everything here is plain numpy / OpenCV / onnxruntime so it can be tested
without ROS or a camera.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

OBSTACLE, WATER, SKY = 0, 1, 2
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


class WaterSegmenter:
    """One onnxruntime session; ``segment(bgr)`` -> per-pixel labels."""

    def __init__(self, model_path: str, threads: int = 4) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        # Explicit thread counts: with the defaults onnxruntime tries to pin
        # threads to cores and fails inside the container (affinity errors).
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
        inputs = self._sess.get_inputs()
        self._input = inputs[0].name
        h, w = inputs[0].shape[2], inputs[0].shape[3]
        self.size = (int(w) if isinstance(w, int) else 512, int(h) if isinstance(h, int) else 384)
        # Models exported with the IMU branch take a horizon mask as a 2nd
        # input; all ones = "no IMU information".
        self._extra = {i.name: np.ones([d if isinstance(d, int) else 1 for d in i.shape], np.float32)
                       for i in inputs[1:]}
        self._output = self._sess.get_outputs()[0].name

    def segment(self, bgr: np.ndarray) -> np.ndarray:
        """uint8 labels (OBSTACLE / WATER / SKY) at the frame's own size."""
        h, w = bgr.shape[:2]
        x = cv2.resize(bgr, self.size, interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255.0
        x = ((x - MEAN) / STD).transpose(2, 0, 1)[None]
        out = self._sess.run([self._output], {self._input: np.ascontiguousarray(x), **self._extra})[0]
        labels = np.argmax(out[0], axis=0).astype(np.uint8)
        return cv2.resize(labels, (w, h), interpolation=cv2.INTER_NEAREST)


def floating_obstacles(labels: np.ndarray, depth_mm: np.ndarray | None, fx: float | None, cx: float,
                       min_px: int = 150) -> tuple[list[dict], float]:
    """Obstacle regions that sit on the water, nearest first.

    An obstacle region counts when the pixels just below it are mostly water:
    something floating, or the shore at the far edge of the water. Regions
    with no water below them (the sky line, the boat's own bow at the bottom
    of the frame, a room with no water at all) are not reported. A region
    spanning most of the width and touching a side of the frame is labelled
    ``shore``, anything else ``obstacle``.

    Distance: median stereo depth over the lowest third of the region, where
    it meets the water (the part nearest to the boat). ``None`` when stereo
    has too few measurements there (flat kelp on the surface often gives
    none); a range from camera height and horizon needs the mounting
    calibration and is not guessed here.

    Returns (objects, water_fraction).
    """
    h, w = labels.shape
    water = labels == WATER
    water_frac = float(water.mean())
    # Below 10 % this is not a view of the water: indoors a dark glossy
    # screen alone reached 4 % (2026-11-13). At sea water fills far more.
    if water_frac < 0.10:
        return [], water_frac
    obst = (labels == OBSTACLE).astype(np.uint8)
    obst = cv2.morphologyEx(obst, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, comp, stats, _ = cv2.connectedComponentsWithStats(obst, connectivity=8)
    # dst(p) = max src(p + k - anchor): with the anchor at the kernel's BOTTOM
    # the region grows downwards by 8 px (anchor at the top grows it upwards,
    # which tested "water above", and lost the shore and moored boats).
    below = np.ones((9, 1), np.uint8)
    around = np.ones((9, 9), np.uint8)
    objects = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_px:
            continue
        m = comp == i
        mu = m.astype(np.uint8)
        ring = cv2.dilate(mu, below, anchor=(0, 8)).astype(bool) & ~m
        if y + bh >= h - 1:
            # Reaches the bottom of the frame: very close, nothing visible
            # beneath it. Judge by what surrounds it instead, more strictly.
            # Kept on purpose even when it spans the whole width: that is
            # either the boat's own bow or a kelp mat right in front, and
            # hiding the second is worse than showing the first. The bow gets
            # a fixed "self mask" once the camera is mounted and calibrated.
            ring = cv2.dilate(mu, around).astype(bool) & ~m
            if ring.sum() == 0 or water[ring].mean() < 0.7:
                continue
        elif ring.sum() == 0 or water[ring].mean() < 0.5:
            continue
        shore = bw > 0.6 * w and (x == 0 or x + bw >= w)
        dist = None
        if depth_mm is not None:
            low = m.copy()
            low[: y + int(bh * 2 / 3)] = False
            d = depth_mm[low]
            d = d[d > 0]
            if d.size >= 20:
                dist = round(float(np.median(d)) / 1000.0, 2)
        ys, xs = np.nonzero(m)
        bearing = round(math.degrees(math.atan2(xs.mean() - cx, fx)), 1) if fx else None
        objects.append({
            "label": "shore" if shore else "obstacle",
            "confidence": None,
            "distance_m": dist,
            "bearing_deg": bearing,
            "box": (int(x), int(y), int(x + bw), int(y + bh)),
            "area_px": int(area),
            "mask": m,
        })
    objects.sort(key=lambda o: (o["distance_m"] is None, o["distance_m"] or 0.0))
    return objects, water_frac


def tint(bgr: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Water in light blue on a copy of the frame, so the operator sees what the model calls water."""
    out = bgr.copy()
    water = labels == WATER
    out[water] = (0.65 * out[water] + 0.35 * np.array([230, 160, 40])).astype(np.uint8)
    return out
