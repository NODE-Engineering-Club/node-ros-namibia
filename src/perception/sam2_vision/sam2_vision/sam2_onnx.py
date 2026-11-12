"""SAM 2.1 on ONNX Runtime, plus the depth and drawing it needs. ROS-free.

Why this exists next to sam2_inference_node (TensorRT)
------------------------------------------------------
The TensorRT node needs an engine built on the Jetson with TensorRT/PyCUDA,
and the Jazzy images have neither (the host is JetPack 5.1.2, whose
TensorRT 8.5 is built for Ubuntu 20.04, not the 24.04 these images run).
This module runs the same model, SAM 2.1 hiera-tiny, on the CPU through
ONNX Runtime, which the image already has. It is slower (~2 s per frame on
the Orin's 8 online cores) but it runs today, on any machine, and its
output is the contract the GPU path should keep when it lands.

Model files: the Hugging Face ``onnx-community/sam2.1-hiera-tiny-ONNX``
export (``vision_encoder.onnx`` + ``prompt_encoder_mask_decoder.onnx``,
each with its ``.onnx_data``). See ``scripts/fetch_sam2_onnx.sh``.

Pipeline per frame
------------------
1. Encoder: the frame, resized to 1024x1024 (not letterboxed — that is what
   the export's processor does), ImageNet-normalised.
2. Prompts: a regular grid of points ("segment everything"), plus any boxes
   given by a detector (the buoy model). Each prompt is one decoder batch
   item; the best of SAM's three candidate masks is kept.
3. Filtering: predicted IoU and object score, area limits, then mask-level
   NMS so one object is reported once.
4. Distance: the median of the aligned depth under the (eroded) mask.
   Median, not mean or minimum: stereo holes are 0 and excluded, and a few
   wrong matches at a mask's edge cannot drag it.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
SAM_SIZE = 1024

#: Distinct, colour-blind-friendlier palette (BGR) for mask overlays.
PALETTE = [
    (255, 159, 28), (46, 196, 182), (231, 29, 54), (255, 214, 10), (131, 56, 236),
    (58, 134, 255), (6, 214, 160), (255, 0, 110), (251, 86, 7), (138, 201, 38),
    (25, 130, 196), (255, 202, 58), (106, 76, 147), (0, 187, 249), (241, 91, 181),
]


@dataclass
class Segment:
    mask: np.ndarray            # bool, HxW of the original frame
    score: float                # SAM's predicted IoU of the chosen mask
    label: str = ""
    prompt: str = "grid"        # "grid" or "box"
    distance_m: float | None = None
    depth_coverage: float = 0.0  # fraction of mask pixels with a depth value
    bearing_deg: float | None = None
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h (pixels)
    extra: dict = field(default_factory=dict)

    @property
    def area(self) -> int:
        return int(self.mask.sum())


def grid_points(width: int, height: int, nx: int, ny: int) -> np.ndarray:
    """Cell-centred grid in pixel coordinates, shape (nx*ny, 2)."""
    xs = (np.arange(nx) + 0.5) * width / nx
    ys = (np.arange(ny) + 0.5) * height / ny
    return np.array([(x, y) for y in ys for x in xs], dtype=np.float32)


def mask_iou(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(IoU, intersection over the smaller mask)."""
    inter = float(np.logical_and(a, b).sum())
    if inter == 0.0:
        return 0.0, 0.0
    union = float(np.logical_or(a, b).sum())
    return inter / union, inter / min(float(a.sum()), float(b.sum()))


def nms_masks(segments: list[Segment], iou_thresh: float, contain_thresh: float) -> list[Segment]:
    """Greedy mask NMS: box prompts first (they carry a label), then by score."""
    order = sorted(segments, key=lambda s: (s.prompt != "box", -s.score))
    kept: list[Segment] = []
    for seg in order:
        if all(
            (lambda r: r[0] < iou_thresh and r[1] < contain_thresh)(mask_iou(seg.mask, k.mask))
            for k in kept
        ):
            kept.append(seg)
    return kept


def mask_distance(
    mask: np.ndarray, depth_mm: np.ndarray, min_pixels: int = 40, min_coverage: float = 0.03,
    erode_px: int = 3,
) -> tuple[float | None, float]:
    """(median distance in metres or None, fraction of mask with depth)."""
    m = mask.astype(np.uint8)
    if erode_px > 0:
        eroded = cv2.erode(m, np.ones((2 * erode_px + 1, 2 * erode_px + 1), np.uint8))
        if eroded.sum() >= min_pixels:
            m = eroded
    sel = m.astype(bool)
    total = int(sel.sum())
    if total == 0:
        return None, 0.0
    values = depth_mm[sel]
    values = values[values > 0]
    coverage = values.size / total
    if values.size < min_pixels or coverage < min_coverage:
        return None, coverage
    return float(np.median(values)) / 1000.0, coverage


def label_anchor(mask: np.ndarray) -> tuple[int, int]:
    """A point well inside the mask (max of the distance transform)."""
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    y, x = np.unravel_index(int(np.argmax(dt)), dt.shape)
    return int(x), int(y)


class Sam2Onnx:
    def __init__(self, model_dir: str, encoder: str = "vision_encoder.onnx",
                 decoder: str = "prompt_encoder_mask_decoder.onnx", threads: int = 6,
                 gpu_server: str = "", gpu_encoder: str = "vision_encoder.onnx", log=print) -> None:
        # Sessions are HybridSession (remote_ort.py): the Jetson's GPU through
        # the GPU container when `gpu_server` answers, the CPU otherwise. The
        # local CPU sessions use an explicit thread count: ORT's default pins
        # threads to cores, and that fails inside a container's cpuset.
        from sam2_vision.remote_ort import HybridSession

        enc_path = os.path.join(model_dir, encoder)
        dec_path = os.path.join(model_dir, decoder)
        for p in (enc_path, dec_path):
            if not os.path.isfile(p):
                raise FileNotFoundError(
                    f"SAM 2 ONNX file missing: {p}. Run src/perception/sam2_vision/"
                    "scripts/fetch_sam2_onnx.sh"
                )
        gpu_enc = os.path.join(model_dir, gpu_encoder)
        if not os.path.isfile(gpu_enc):
            gpu_enc = enc_path
        self.encoder = HybridSession(enc_path, gpu_enc, threads, gpu_server, log)
        self.decoder = HybridSession(dec_path, dec_path, threads, gpu_server, log)
        self._variant = encoder.replace("vision_encoder", "").replace(".onnx", "").strip("_") or "fp32"

    @property
    def name(self) -> str:
        """Model and where it is running now (it can change: GPU lost or back)."""
        if self.encoder.backend == "GPU":
            return "SAM2.1-tiny fp32 (GPU)"
        return f"SAM2.1-tiny {self._variant} (CPU)"

    def encode(self, bgr: np.ndarray) -> list[np.ndarray]:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        x = cv2.resize(rgb, (SAM_SIZE, SAM_SIZE), interpolation=cv2.INTER_LINEAR).astype(np.float32)
        x = (x / 255.0 - _MEAN) / _STD
        x = x.transpose(2, 0, 1)[None]
        return self.encoder.run(None, {"pixel_values": np.ascontiguousarray(x)})

    def decode(self, emb: list[np.ndarray], width: int, height: int,
               points: np.ndarray | None = None, boxes: np.ndarray | None = None,
               chunk: int = 16):
        """Masks for N point prompts or N box prompts (pixel coords).

        Returns (masks bool [N,H,W], iou [N], object_logit [N]).
        """
        sx, sy = SAM_SIZE / width, SAM_SIZE / height
        if points is not None:
            prompts = np.asarray(points, np.float32) * np.array([sx, sy], np.float32)
        else:
            prompts = np.asarray(boxes, np.float32) * np.array([sx, sy, sx, sy], np.float32)
        n = len(prompts)
        out_m, out_i, out_o = [], [], []
        for start in range(0, n, chunk):
            p = prompts[start:start + chunk]
            b = len(p)
            feeds = {f"image_embeddings.{i}": np.repeat(e, b, axis=0) for i, e in enumerate(emb)}
            if points is not None:
                feeds["input_points"] = p.reshape(b, 1, 1, 2)
                feeds["input_labels"] = np.ones((b, 1, 1), np.int64)
                feeds["input_boxes"] = np.zeros((b, 0, 4), np.float32)
            else:
                feeds["input_points"] = np.zeros((b, 1, 1, 2), np.float32)
                feeds["input_labels"] = np.full((b, 1, 1), -10, np.int64)  # padding: no point
                feeds["input_boxes"] = p.reshape(b, 1, 4)
            iou, masks, obj = self.decoder.run(None, feeds)
            best = np.argmax(iou[:, 0, :], axis=1)
            for k in range(b):
                logit = masks[k, 0, best[k]]
                full = cv2.resize(logit, (width, height), interpolation=cv2.INTER_LINEAR)
                out_m.append(full > 0.0)
                out_i.append(float(iou[k, 0, best[k]]))
                out_o.append(float(obj[k, 0, 0]))
        if not out_m:
            return np.zeros((0, height, width), bool), np.zeros(0), np.zeros(0)
        return np.stack(out_m), np.array(out_i), np.array(out_o)


@dataclass
class SegmenterConfig:
    grid_x: int = 6
    grid_y: int = 4
    min_iou: float = 0.75
    min_object_logit: float = 0.0
    min_area_frac: float = 0.002
    max_area_frac: float = 0.40
    nms_iou: float = 0.5
    nms_contain: float = 0.8
    max_objects: int = 12
    #: Point-grid prompts ("segment everything"). Off = only detector boxes
    #: (every mask labelled, and a much shorter decode).
    grid_prompts: bool = True


def segment_frame(sam: Sam2Onnx, bgr: np.ndarray, depth_mm: np.ndarray | None,
                  cfg: SegmenterConfig, fx: float | None = None, cx: float | None = None,
                  boxes: list[tuple[float, float, float, float, str, float]] | None = None):
    """Run SAM 2 on one frame and measure each mask. Returns (segments, timings)."""
    h, w = bgr.shape[:2]
    t0 = time.monotonic()
    emb = sam.encode(bgr)
    t1 = time.monotonic()

    candidates: list[Segment] = []
    if cfg.grid_prompts or not boxes:
        pts = grid_points(w, h, cfg.grid_x, cfg.grid_y)
        masks, ious, objs = sam.decode(emb, w, h, points=pts)
        for m, iou, obj in zip(masks, ious, objs):
            candidates.append(Segment(mask=m, score=iou, prompt="grid", extra={"obj": obj}))
    if boxes:
        arr = np.array([b[:4] for b in boxes], np.float32)
        bm, bi, bo = sam.decode(emb, w, h, boxes=arr)
        for m, iou, obj, b in zip(bm, bi, bo, boxes):
            candidates.append(Segment(mask=m, score=max(iou, 0.99), label=b[4], prompt="box",
                                      extra={"obj": obj, "det_score": b[5]}))
    t2 = time.monotonic()

    area = float(w * h)
    good = [
        s for s in candidates
        if (s.prompt == "box" or (s.score >= cfg.min_iou and s.extra["obj"] > cfg.min_object_logit))
        and cfg.min_area_frac * area <= s.area <= cfg.max_area_frac * area
    ]
    kept = nms_masks(good, cfg.nms_iou, cfg.nms_contain)[: cfg.max_objects]

    for s in kept:
        ys, xs = np.nonzero(s.mask)
        s.bbox = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        if depth_mm is not None:
            s.distance_m, s.depth_coverage = mask_distance(s.mask, depth_mm)
        if fx and cx is not None:
            ax, _ = label_anchor(s.mask)
            s.bearing_deg = math.degrees(math.atan2(ax - cx, fx))
    # Nearest first: that is the order an operator reads them in.
    kept.sort(key=lambda s: (s.distance_m is None, s.distance_m or 0.0))
    t3 = time.monotonic()
    return kept, {"encode_s": t1 - t0, "decode_s": t2 - t1, "post_s": t3 - t2, "total_s": t3 - t0,
                  "prompts": len(candidates)}


def annotate(bgr: np.ndarray, segments: list[Segment], title: str) -> np.ndarray:
    """Masks, outlines and distance labels drawn on a copy of the frame."""
    out = bgr.copy()
    overlay = bgr.copy()
    for i, s in enumerate(segments):
        overlay[s.mask] = PALETTE[i % len(PALETTE)]
    out = cv2.addWeighted(overlay, 0.45, out, 0.55, 0)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for i, s in enumerate(segments):
        colour = PALETTE[i % len(PALETTE)]
        contours, _ = cv2.findContours(s.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, colour, 2, cv2.LINE_AA)
        dist = f"{s.distance_m:.1f} m" if s.distance_m is not None else "-- m"
        # Every mask says what it is: the detector's class, or "object" for a
        # shape SAM 2 found that no detector recognised.
        text = f"{s.label or 'object'} {dist}"
        x, y = label_anchor(s.mask)
        (tw, th), base = cv2.getTextSize(text, font, 0.6, 2)
        x0 = int(np.clip(x - tw // 2, 2, out.shape[1] - tw - 6))
        y0 = int(np.clip(y - th // 2, 2, out.shape[0] - th - base - 6))
        cv2.rectangle(out, (x0 - 3, y0 - 3), (x0 + tw + 3, y0 + th + base + 1), (20, 20, 20), -1)
        cv2.rectangle(out, (x0 - 3, y0 - 3), (x0 + tw + 3, y0 + th + base + 1), colour, 1)
        cv2.putText(out, text, (x0, y0 + th), font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    (tw, th), base = cv2.getTextSize(title, font, 0.5, 1)
    cv2.rectangle(out, (0, 0), (tw + 12, th + base + 10), (20, 20, 20), -1)
    cv2.putText(out, title, (6, th + 5), font, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
    return out


def segments_json(segments: list[Segment], width: int, height: int) -> list[dict]:
    """The per-object summary (fractions of the frame, so it survives scaling)."""
    out = []
    for i, s in enumerate(segments):
        x, y, w, h = s.bbox
        out.append({
            "id": i,
            "label": s.label or "object",
            "prompt": s.prompt,
            "score": round(s.score, 3),
            "distance_m": None if s.distance_m is None else round(s.distance_m, 2),
            "depth_coverage": round(s.depth_coverage, 2),
            "bearing_deg": None if s.bearing_deg is None else round(s.bearing_deg, 1),
            "bbox": [round(x / width, 4), round(y / height, 4), round(w / width, 4), round(h / height, 4)],
            "area_frac": round(s.area / float(width * height), 4),
            "colour_bgr": list(PALETTE[i % len(PALETTE)]),
        })
    return out
