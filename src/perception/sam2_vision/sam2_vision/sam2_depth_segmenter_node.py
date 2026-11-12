#!/usr/bin/env python3
"""SAM 2.1 segmentation with a distance per object, on the CPU (ONNX Runtime).

Inputs (same header stamp, from sensors/oak_rgbd_node):
  * ``rgb_topic``    sensor_msgs/Image bgr8           (/oak/rgb/image_raw)
  * ``depth_topic``  sensor_msgs/Image 16UC1, mm, on the rgb pixel grid
                                                       (/oak/stereo/depth)
  * ``info_topic``   sensor_msgs/CameraInfo            (/oak/rgb/camera_info)

Outputs:
  * ``/perception/segmentation/image/compressed`` — the frame that was
    segmented, with each mask, its outline and its distance drawn on it.
    Its header is the camera frame's, so its age is the age of the picture,
    not of the inference.
  * ``/perception/segments`` — std_msgs/String, JSON: per object its label,
    distance (median depth under the mask), bearing, bbox and SAM score.

One frame at a time: the newest synchronised pair is taken when the
previous inference finishes, and frames in between are dropped. At ~3 s
per frame on the CPU, queueing would only make every result older.

Prompts and labels: SAM 2 finds shapes but does not know what they are, so
labels come from detectors whose boxes become SAM 2 box prompts:
  * the general object model (``object_model_path``, YOLO26n-seg on COCO:
    person, boat, bird, car, ... 80 classes), and
  * the navigation-mark model (``buoy_model_path``: green/red/cardinal buoys).
Each detected object is drawn as its mask with "<label> <distance>". The
point grid (``grid_prompts``) adds the shapes no detector knows, drawn as
"object <distance>". A missing model file only removes its labels.
"""

from __future__ import annotations

import json
import os
import threading
import time

import cv2
import message_filters
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import String

from sam2_vision.sam2_onnx import Sam2Onnx, SegmenterConfig, annotate, segment_frame, segments_json


def _image_to_array(msg: Image) -> np.ndarray:
    if msg.encoding in ("bgr8", "rgb8"):
        arr = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.step // 1)[:, : msg.width * 3]
        arr = arr.reshape(msg.height, msg.width, 3)
        return arr[:, :, ::-1] if msg.encoding == "rgb8" else arr
    if msg.encoding in ("16UC1", "mono16"):
        return np.frombuffer(msg.data, np.uint16).reshape(msg.height, msg.step // 2)[:, : msg.width]
    raise ValueError(f"unsupported encoding {msg.encoding}")


class YoloDetector:
    """A YOLO26 ONNX export (end-to-end, NMS-free), used for labelled box prompts only.

    Works for the repo's navigation-mark model and for the COCO object model:
    both output ``[x1, y1, x2, y2, score, class, ...]`` rows.
    """

    def __init__(self, path: str, conf: float, threads: int, suffix: str = "",
                 gpu_server: str = "", log=print) -> None:
        import onnxruntime as ort

        from sam2_vision.remote_ort import HybridSession

        # Class names from the file's own metadata (a cheap local read), then
        # inference through the GPU server when it answers.
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.log_severity_level = 3
        meta = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"]).get_modelmeta().custom_metadata_map
        self.sess = HybridSession(path, path, threads, gpu_server, log)
        import ast

        self.names = ast.literal_eval(meta.get("names", "{}"))
        self.size = 640
        self.conf = conf
        self.suffix = suffix

    def __call__(self, bgr: np.ndarray):
        h, w = bgr.shape[:2]
        r = self.size / max(h, w)
        nh, nw = round(h * r), round(w * r)
        canvas = np.full((self.size, self.size, 3), 114, np.uint8)
        top, left = (self.size - nh) // 2, (self.size - nw) // 2
        canvas[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh))
        x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        det = self.sess.run(None, {"images": np.ascontiguousarray(x)})[0][0]
        boxes = []
        for row in det:
            score = float(row[4])
            if score < self.conf:
                continue
            x1, y1, x2, y2 = ((row[[0, 1, 2, 3]] - [left, top, left, top]) / r).tolist()
            name = self.names.get(int(row[5]), str(int(row[5])))
            boxes.append((max(0.0, x1), max(0.0, y1), min(w, x2), min(h, y2), f"{name}{self.suffix}", score))
        return boxes


class Sam2DepthSegmenter(Node):
    def __init__(self) -> None:
        super().__init__("sam2_depth_segmenter")
        p = self.declare_parameter
        p("rgb_topic", "/oak/rgb/image_raw")
        p("depth_topic", "/oak/stereo/depth")
        p("info_topic", "/oak/rgb/camera_info")
        p("model_dir", "/ws/models/sam2.1_hiera_tiny")
        p("encoder_file", "vision_encoder_int8.onnx")
        p("decoder_file", "prompt_encoder_mask_decoder.onnx")
        p("threads", 6)
        p("grid_x", 6)
        p("grid_y", 4)
        p("min_iou", 0.75)
        p("max_area_frac", 0.40)
        p("max_objects", 12)
        p("buoy_model_path", "/ws/models/yolo26n-seg-navier.onnx")
        p("buoy_confidence", 0.4)
        p("object_model_path", "/ws/models/yolo26n-seg-coco.onnx")
        p("object_confidence", 0.35)
        p("grid_prompts", True)
        # The GPU container's ONNX Runtime server (remote_ort.py, started by
        # scripts/boat_up.sh). Empty = CPU only. Unreachable = CPU until it is back.
        p("gpu_server", "127.0.0.1:7090")
        p("gpu_encoder_file", "vision_encoder.onnx")
        p("jpeg_quality", 80)
        p("min_period_s", 0.0)
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        self.cfg = SegmenterConfig(
            grid_x=int(g("grid_x")), grid_y=int(g("grid_y")), min_iou=float(g("min_iou")),
            max_area_frac=float(g("max_area_frac")), max_objects=int(g("max_objects")),
        )
        threads = int(g("threads"))
        gpu = str(g("gpu_server"))
        log = self.get_logger().info
        self.sam = Sam2Onnx(g("model_dir"), g("encoder_file"), g("decoder_file"), threads,
                            gpu_server=gpu, gpu_encoder=str(g("gpu_encoder_file")), log=log)
        self.get_logger().info(f"Loaded {self.sam.name} from {g('model_dir')}, {threads} threads")

        self.detectors = []
        for key, suffix in (("object", ""), ("buoy", " buoy")):
            path = str(g(f"{key}_model_path"))
            if not (path and os.path.isfile(path)):
                self.get_logger().warn(f"{key} model not found at {path!r}: no {key} labels")
                continue
            try:
                det = YoloDetector(path, float(g(f"{key}_confidence")), threads, suffix, gpu, log)
                self.detectors.append(det)
                names = list(det.names.values())
                self.get_logger().info(f"{key} labels from {path}: {names[:12]}{' ...' if len(names) > 12 else ''}")
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"{key} model not loaded ({exc})")
        self.cfg.grid_prompts = bool(g("grid_prompts"))

        self._jpeg_q = int(g("jpeg_quality"))
        self._min_period = float(g("min_period_s"))
        self._fx = self._cx = None
        self._latest = None
        self._cv = threading.Condition()

        self.create_subscription(CameraInfo, g("info_topic"), self._on_info, 10)
        subs = [
            message_filters.Subscriber(self, Image, g("rgb_topic"), qos_profile=qos_profile_sensor_data),
            message_filters.Subscriber(self, Image, g("depth_topic"), qos_profile=qos_profile_sensor_data),
        ]
        self._sync = message_filters.TimeSynchronizer(subs, 10)
        self._sync.registerCallback(self._on_pair)

        self.pub_img = self.create_publisher(CompressedImage, "/perception/segmentation/image/compressed", 2)
        self.pub_json = self.create_publisher(String, "/perception/segments", 5)

        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def _on_info(self, msg: CameraInfo) -> None:
        self._fx, self._cx = msg.k[0], msg.k[2]

    def _on_pair(self, rgb: Image, depth: Image) -> None:
        with self._cv:
            self._latest = (rgb, depth)
            self._cv.notify()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while self._latest is None and not self._stop.is_set():
                    self._cv.wait(timeout=1.0)
                pair, self._latest = self._latest, None
            if pair is None:
                continue
            started = time.monotonic()
            try:
                self._process(*pair)
            except Exception as exc:  # noqa: BLE001
                self.get_logger().error(f"segmentation failed: {exc!r}", throttle_duration_sec=10.0)
            rest = self._min_period - (time.monotonic() - started)
            if rest > 0:
                self._stop.wait(rest)

    def _process(self, rgb_msg: Image, depth_msg: Image) -> None:
        bgr = np.ascontiguousarray(_image_to_array(rgb_msg))
        depth = _image_to_array(depth_msg)
        h, w = bgr.shape[:2]

        t0 = time.monotonic()
        boxes = [b for det in self.detectors for b in det(bgr)]
        t_det = time.monotonic() - t0
        segs, t = segment_frame(self.sam, bgr, depth, self.cfg, fx=self._fx, cx=self._cx, boxes=boxes)
        total = t["total_s"] + t_det

        title = f"{self.sam.name} | {len(segs)} objects | {total:.1f} s"
        img = annotate(bgr, segs, title)
        ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_q])
        if ok:
            out = CompressedImage()
            out.header = rgb_msg.header
            out.format = "jpeg"
            out.data = jpg.tobytes()
            self.pub_img.publish(out)

        stamp_ms = rgb_msg.header.stamp.sec * 1000 + rgb_msg.header.stamp.nanosec // 1_000_000
        self.pub_json.publish(String(data=json.dumps({
            "stamp_utc_ms": stamp_ms,
            "model": self.sam.name,
            "width": w,
            "height": h,
            "timing_s": {k: round(v, 3) for k, v in t.items() if k.endswith("_s")} | {"detect_s": round(t_det, 3)},
            "detections": len(boxes),
            "objects": segments_json(segs, w, h),
        }, separators=(",", ":"))))
        dists = ", ".join(
            f"{s.label or 'obj'}={s.distance_m:.2f}m" if s.distance_m is not None else f"{s.label or 'obj'}=--"
            for s in segs
        )
        self.get_logger().info(
            f"{len(segs)} objects in {total:.2f} s (enc {t['encode_s']:.2f}, dec {t['decode_s']:.2f}, "
            f"{t['prompts']} prompts, {len(boxes)} detections): {dists}",
            throttle_duration_sec=15.0,
        )

    def destroy_node(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify()
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = Sam2DepthSegmenter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
