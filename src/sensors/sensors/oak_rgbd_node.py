#!/usr/bin/env python3
"""OAK-D-LR colour + metric depth, aligned pixel-for-pixel, as ROS topics.

Why this node and not ``depthai_ros_driver``
-------------------------------------------
The driver is the long-term answer (``njord.launch.py enable_oak:=true``,
docs/CAMERA_OAK.md) but its LR handling and its parameter names differ
between the v2 and v3 packages, and what the segmentation needs is narrow
and exact: a colour frame and a depth frame **of the same instant, on the
same pixel grid**, so that "the median depth under this mask" is the
distance to that object. This node builds exactly that pipeline with the
DepthAI v2 Python API and nothing else.

Pipeline (all on the camera's own processor)
-------------------------------------------
* CAM_A (centre AR0234)      -> ISP 1/2  -> 960x600 colour.
* CAM_B + CAM_C (15 cm apart) -> ISP 2/3 -> 1280x800 each -> StereoDepth,
  left-right check + subpixel, **aligned to CAM_A** and resized to 960x600.
  15 cm is the LR's widest pair: the longest usable range (tens of metres
  with subpixel), at the cost of a ~1.2 m minimum distance.
* A ``Sync`` node pairs colour and depth by device timestamp, so the host
  receives them together and both messages get the same header stamp.

Published (relative to the node's namespace; launch it in ``/oak``)
-------------------------------------------------------------
* ``rgb/image_raw``             sensor_msgs/Image, bgr8
* ``rgb/image_raw/compressed``  sensor_msgs/CompressedImage, JPEG (the GUI's)
* ``rgb/camera_info``           sensor_msgs/CameraInfo, factory calibration
* ``stereo/depth``              sensor_msgs/Image, 16UC1, millimetres, 0 = no
                                 measurement, on the rgb pixel grid

On-camera detection (``detection_blob``, default YOLOv8n COCO for RVC2)
--------------------------------------------------------------------
The OAK's own chip runs the detector and measures each object's 3D position
from its stereo depth (YoloSpatialDetectionNetwork), in the same Sync group
as the frame, so boxes, depth and picture are one instant. The Jetson only
draws: a mask per object from the depth pixels near that object's distance,
its outline, and "<label> <distance>". About 0% Jetson CPU for the detector
and ~10 fps, against seconds per frame for SAM 2 on the CPU (2026-11-12).

* ``detections/image/compressed`` sensor_msgs/CompressedImage, the annotated
                                 frame (the GUI's "Segmented + distance" view)
* ``detections``                 std_msgs/String, JSON: per object label,
                                 confidence, distance and x/y/z in metres,
                                 bearing, bbox in pixels

Connection is USB (the camera is plugged in over USB on the bench) unless
``device_ip`` is set, which selects the PoE unit at that address.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time

import cv2
import numpy as np
import rclpy
from builtin_interfaces.msg import Time
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import String

try:
    import depthai as dai

    _DAI_ERROR = None
except Exception as exc:  # noqa: BLE001
    dai = None
    _DAI_ERROR = exc


COCO_LABELS = [
    "person", "bicycle", "car", "motorbike", "aeroplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "sofa",
    "pottedplant", "bed", "diningtable", "toilet", "tvmonitor", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]

#: Mask / outline colours (BGR), one per object in a frame.
PALETTE = [(60, 180, 255), (80, 220, 100), (255, 140, 60), (200, 90, 255), (60, 220, 230),
           (255, 90, 160), (150, 255, 140), (90, 140, 255)]


def depth_mask(depth_mm: np.ndarray, box: tuple[int, int, int, int], z_mm: float) -> np.ndarray | None:
    """Pixels inside ``box`` (x0, y0, x1, y1) at about the object's distance.

    The OAK measured the object at ``z_mm`` (from the centre of its box). The
    object is the connected region of depth pixels within a tolerance of that
    distance: 0.4 m, or 15 % of the distance, whichever is larger (stereo
    error grows with range). Returns a full-frame bool mask, or None.
    """
    x0, y0, x1, y1 = box
    if z_mm <= 0 or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    roi = depth_mm[y0:y1, x0:x1].astype(np.float32)
    tol = max(400.0, 0.15 * z_mm)
    m = ((roi > 0) & (np.abs(roi - z_mm) < tol)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return None
    # The object is the region at the centre of its box, not the largest one:
    # on a dark, glossy object stereo depth has holes, and the largest patch
    # at that distance was a strip of wall at the box edge (2026-11-12).
    h, w = m.shape
    cy, cx = h / 2.0, w / 2.0
    areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)
    dist = np.hypot(centroids[1:, 0] - cx, centroids[1:, 1] - cy) / max(h, w)
    best = 1 + int(np.argmax(areas * np.clip(1.0 - 1.5 * dist, 0.05, 1.0)))
    obj = labels == best
    # A believable mask: a fair share of the box, and centred in it. Anything
    # else is shown as the box, never as a confident wrong outline.
    ys, xs = np.nonzero(obj)
    if obj.sum() < 0.15 * m.size or abs(xs.mean() - cx) > 0.3 * w or abs(ys.mean() - cy) > 0.3 * h:
        return None
    full = np.zeros(depth_mm.shape[:2], bool)
    full[y0:y1, x0:x1] = obj
    return full


def annotate(bgr: np.ndarray, objects: list[dict], title: str) -> np.ndarray:
    """Masks, outlines, boxes and "<label> <distance>" on a copy of the frame."""
    out = bgr.copy()
    overlay = bgr.copy()
    for i, o in enumerate(objects):
        if o["mask"] is not None:
            overlay[o["mask"]] = PALETTE[i % len(PALETTE)]
    out = cv2.addWeighted(overlay, 0.45, out, 0.55, 0)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for i, o in enumerate(objects):
        colour = PALETTE[i % len(PALETTE)]
        x0, y0, x1, y1 = o["box"]
        if o["mask"] is not None:
            contours, _ = cv2.findContours(o["mask"].astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(out, contours, -1, colour, 2, cv2.LINE_AA)
        else:  # no usable depth: the box is all there is
            cv2.rectangle(out, (x0, y0), (x1, y1), colour, 2, cv2.LINE_AA)
        dist = f"{o['distance_m']:.1f} m" if o["distance_m"] is not None else "-- m"
        text = f"{o['label']} {dist}"
        (tw, th), base = cv2.getTextSize(text, font, 0.6, 2)
        tx = int(np.clip(x0, 2, out.shape[1] - tw - 6))
        ty = int(np.clip(y0 - th - base - 6, 2, out.shape[0] - th - base - 6))
        cv2.rectangle(out, (tx - 3, ty - 3), (tx + tw + 3, ty + th + base + 1), (20, 20, 20), -1)
        cv2.rectangle(out, (tx - 3, ty - 3), (tx + tw + 3, ty + th + base + 1), colour, 1)
        cv2.putText(out, text, (tx, ty + th), font, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    (tw, th), base = cv2.getTextSize(title, font, 0.5, 1)
    cv2.rectangle(out, (0, 0), (tw + 12, th + base + 10), (20, 20, 20), -1)
    cv2.putText(out, title, (6, th + 5), font, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
    return out


class OakRgbdNode(Node):
    def __init__(self) -> None:
        super().__init__("oak_rgbd")
        if dai is None:
            raise RuntimeError(f"oak_rgbd needs the depthai Python package (v2): {_DAI_ERROR}")

        self.declare_parameter("fps", 10.0)
        self.declare_parameter("width", 960)
        self.declare_parameter("height", 600)
        self.declare_parameter("frame_id", "oak_rgb_camera_optical_frame")
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("device_ip", "")
        self.declare_parameter("publish_raw", True)
        # USB2 (480 Mb/s) instead of USB3. Bus-powered through a hub, the LR
        # boots at USB3, then drops off the bus ("USB disconnect", back to its
        # bootloader) the moment its three sensors start (seen 2026-11-12).
        # 960x600 colour + depth at 10 fps fits in USB2 comfortably.
        self.declare_parameter("usb2_mode", True)
        # On-camera detector (RVC2 .blob, YOLO with 80 COCO classes by default).
        # Empty or missing file = colour + depth only, as before.
        self.declare_parameter("detection_blob", "/ws/models/yolov8n_coco_640x352.blob")
        self.declare_parameter("detection_input", [640, 352])
        self.declare_parameter("detection_confidence", 0.4)
        self.declare_parameter("detection_labels", COCO_LABELS)

        self._fps = float(self.get_parameter("fps").value)
        self._w = int(self.get_parameter("width").value)
        self._h = int(self.get_parameter("height").value)
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._jpeg_q = int(self.get_parameter("jpeg_quality").value)
        self._ip = str(self.get_parameter("device_ip").value)
        self._publish_raw = bool(self.get_parameter("publish_raw").value)
        blob = str(self.get_parameter("detection_blob").value)
        self._blob = blob if blob and os.path.isfile(blob) else ""
        if blob and not self._blob:
            self.get_logger().warn(f"detection blob not found at {blob!r}: colour + depth only")
        self._nn_size = [int(v) for v in self.get_parameter("detection_input").value]
        self._nn_conf = float(self.get_parameter("detection_confidence").value)
        self._labels = list(self.get_parameter("detection_labels").value)

        qos = QoSProfile(depth=2, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._pub_rgb = self.create_publisher(Image, "rgb/image_raw", qos)
        self._pub_jpeg = self.create_publisher(CompressedImage, "rgb/image_raw/compressed", qos)
        self._pub_info = self.create_publisher(CameraInfo, "rgb/camera_info", 10)
        self._pub_depth = self.create_publisher(Image, "stereo/depth", qos)
        self._pub_det_img = self.create_publisher(CompressedImage, "detections/image/compressed", qos)
        self._pub_det = self.create_publisher(String, "detections", 5)

        self._info: CameraInfo | None = None
        self._stop = threading.Event()
        self._frames = 0
        self._detections = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.create_timer(10.0, self._report)
        self._last_report = (time.monotonic(), 0)

    # ── pipeline ─────────────────────────────────────────────────────────
    def _pipeline(self):
        S = dai.CameraBoardSocket
        p = dai.Pipeline()

        rgb = p.create(dai.node.ColorCamera)
        rgb.setBoardSocket(S.CAM_A)
        rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1200_P)
        rgb.setIspScale(self._w, 1920)  # 960/1920 = 1/2 by default
        rgb.setFps(self._fps)

        stereo = p.create(dai.node.StereoDepth)
        for sock, port in ((S.CAM_B, stereo.left), (S.CAM_C, stereo.right)):
            cam = p.create(dai.node.ColorCamera)
            cam.setBoardSocket(sock)
            cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1200_P)
            cam.setIspScale(2, 3)  # 1280x800
            cam.setFps(self._fps)
            cam.isp.link(port)

        stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_DENSITY)
        stereo.setLeftRightCheck(True)
        stereo.setSubpixel(True)
        stereo.setExtendedDisparity(False)
        stereo.setDepthAlign(S.CAM_A)
        stereo.setOutputSize(self._w, self._h)
        # Only the cheap filters: the spatial (hole-filling) filter on a
        # 1280x800 disparity map took the pipeline from 10 to 2.5 fps on the
        # bench. Per-mask distance is a median, which already ignores holes.
        cfg = stereo.initialConfig.get()
        cfg.postProcessing.speckleFilter.enable = True
        cfg.postProcessing.speckleFilter.speckleRange = 12
        cfg.postProcessing.thresholdFilter.minRange = 300
        cfg.postProcessing.thresholdFilter.maxRange = 60000
        stereo.initialConfig.set(cfg)

        sync = p.create(dai.node.Sync)
        sync.setSyncThreshold(__import__("datetime").timedelta(milliseconds=int(500 / self._fps)))
        rgb.isp.link(sync.inputs["rgb"])
        stereo.depth.link(sync.inputs["depth"])

        if self._blob:
            # The detector's input: the same sensor's full field of view,
            # squeezed to the network's size, so a normalised box maps straight
            # onto the colour frame and the depth aligned to it.
            rgb.setPreviewSize(*self._nn_size)
            rgb.setPreviewKeepAspectRatio(False)
            rgb.setInterleaved(False)
            rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
            nn = p.create(dai.node.YoloSpatialDetectionNetwork)
            nn.setBlobPath(self._blob)
            nn.setConfidenceThreshold(self._nn_conf)
            nn.setNumClasses(len(self._labels))
            nn.setCoordinateSize(4)
            nn.setIouThreshold(0.5)
            nn.setBoundingBoxScaleFactor(0.5)  # distance from the box's centre half
            nn.setDepthLowerThreshold(300)
            nn.setDepthUpperThreshold(60000)
            nn.setNumInferenceThreads(2)
            nn.input.setBlocking(False)
            nn.input.setQueueSize(1)
            rgb.preview.link(nn.input)
            stereo.depth.link(nn.inputDepth)
            nn.inputDepth.setBlocking(False)
            nn.inputDepth.setQueueSize(1)
            # Detections on their OWN stream, not in the Sync group: when the
            # detector skipped a frame, Sync held colour + depth waiting for it
            # and the camera pipeline choked, 9.8 -> 0 fps in ~40 s, no error
            # (2026-11-12). The host pairs them by capture timestamp instead.
            det_out = p.create(dai.node.XLinkOut)
            det_out.setStreamName("det")
            det_out.input.setBlocking(False)
            det_out.input.setQueueSize(4)
            nn.out.link(det_out.input)

        # Depth feeds two consumers (Sync and the detector): non-blocking, so a
        # consumer that falls behind drops frames instead of stalling StereoDepth.
        for name in ("rgb", "depth"):
            sync.inputs[name].setBlocking(False)
            sync.inputs[name].setQueueSize(2)
        out = p.create(dai.node.XLinkOut)
        out.setStreamName("rgbd")
        sync.out.link(out.input)
        return p

    def _open_device(self, pipeline):
        if self._ip:
            return dai.Device(pipeline, dai.DeviceInfo(self._ip))
        # Give the host 30 s (not the default ~8 s) to connect after the camera
        # boots, so the restart below has time to reach it.
        board = dai.BoardConfig()
        board.watchdogInitialDelayMs = 30000
        pipeline.setBoardConfig(board)
        # Already booted (by the previous run of this node): connect straight to it.
        booted = [d for d in dai.XLinkConnection.getAllConnectedDevices(dai.XLinkDeviceState.X_LINK_ANY_STATE)
                  if d.state == dai.XLinkDeviceState.X_LINK_BOOTED]
        if booted:
            return dai.Device(pipeline, booted[0])
        speed = dai.UsbSpeed.HIGH if self.get_parameter("usb2_mode").value else dai.UsbSpeed.SUPER
        try:
            return dai.Device(pipeline, maxUsbSpeed=speed)
        except RuntimeError as exc:
            if "after booting" not in str(exc):
                raise
            # The camera booted and came back on USB as a NEW device, but this
            # process never sees it: in a rootless container libusb gets no
            # hotplug events, so its device list is frozen at start-up (seen
            # 2026-11-12). A fresh process does see it. Exit and let the launch
            # respawn us; the next run takes the "already booted" branch.
            self.get_logger().warn(
                "OAK booted but this process cannot see it re-enumerate (rootless container); "
                "restarting to connect")
            os._exit(3)

    def _camera_info(self, device) -> CameraInfo:
        cal = device.readCalibration()
        S = dai.CameraBoardSocket
        k = np.array(cal.getCameraIntrinsics(S.CAM_A, self._w, self._h), dtype=float)
        d = [float(x) for x in cal.getDistortionCoefficients(S.CAM_A)]
        info = CameraInfo()
        info.header.frame_id = self._frame_id
        info.width, info.height = self._w, self._h
        info.distortion_model = "rational_polynomial"
        info.d = d[:8]
        info.k = k.flatten().tolist()
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.p = [k[0, 0], 0.0, k[0, 2], 0.0, 0.0, k[1, 1], k[1, 2], 0.0, 0.0, 0.0, 1.0, 0.0]
        return info

    def _run(self) -> None:
        while not self._stop.is_set() and rclpy.ok():
            try:
                with self._open_device(self._pipeline()) as device:
                    self._info = self._camera_info(device)
                    self.get_logger().info(
                        f"OAK connected: {device.getProductName()} {device.getMxId()} "
                        f"usb={device.getUsbSpeed().name} {self._w}x{self._h}@{self._fps:g} fps, "
                        f"fx={self._info.k[0]:.1f}"
                    )
                    q = device.getOutputQueue("rgbd", maxSize=2, blocking=False)
                    qd = device.getOutputQueue("det", maxSize=8, blocking=False) if self._blob else None
                    dets = {}      # capture timestamp (s) -> detections message
                    pending = []   # (arrived monotonic, group) waiting for their detections
                    last = time.monotonic()
                    while not self._stop.is_set():
                        got = False
                        if qd is not None:
                            for d in qd.tryGetAll():
                                dets[d.getTimestamp().total_seconds()] = d
                                got = True
                        group = q.tryGet()
                        now = time.monotonic()
                        if group is not None:
                            last = now
                            got = True
                            if qd is None:
                                self._publish(group)
                            else:
                                pending.append((now, group))
                        # Each frame goes out with the detections of ITS OWN
                        # instant, or none after 250 ms, never a neighbour's.
                        while pending:
                            t_arr, g = pending[0]
                            ts = g["rgb"].getTimestamp().total_seconds()
                            match = next((dets.pop(k) for k in list(dets) if abs(k - ts) < 0.02), None)
                            if match is None and now - t_arr < 0.25:
                                break
                            pending.pop(0)
                            self._publish(g, match)
                        if group is not None:
                            newest = group["rgb"].getTimestamp().total_seconds()
                            for k in [k for k in dets if k < newest - 1.0]:
                                dets.pop(k, None)  # its frame is long gone
                        if not got:
                            # Watchdog: a pipeline that stalls raises nothing
                            # and q.get() would wait forever, leaving the GUI
                            # on a frozen camera. Reconnect instead.
                            if now - last > 5.0:
                                raise RuntimeError("no frames for 5 s (camera pipeline stalled)")
                            time.sleep(0.003)
            except Exception as exc:  # noqa: BLE001 — unplugged, rebooted, XLink error
                if self._stop.is_set():
                    return
                self.get_logger().error(f"OAK pipeline stopped ({exc}); retrying in 3 s")
                time.sleep(3.0)

    def _stamp(self, dai_ts) -> Time:
        """Device timestamp (host-synced steady clock) -> ROS wall time."""
        age = (dai.Clock.now() - dai_ts).total_seconds()
        ns = self.get_clock().now().nanoseconds - int(age * 1e9)
        return Time(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)

    def _publish(self, group, det_msg=None) -> None:
        rgb_msg = group["rgb"]
        depth_msg = group["depth"]
        stamp = self._stamp(rgb_msg.getTimestamp())

        bgr = rgb_msg.getCvFrame()
        depth = depth_msg.getFrame()
        if depth.shape[:2] != bgr.shape[:2]:
            depth = cv2.resize(depth, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)

        # Raw colour (1.7 MB) and depth (1.1 MB) at 10 fps are ~28 MB/s of
        # best-effort UDP (ROS shared memory does not work in this container).
        # Unread, they only crowd out the JPEGs the GUI needs: with nobody
        # subscribed the annotated frames never reached gui_backend
        # (2026-11-12). Only the SAM 2 segmenter reads them.
        if self._publish_raw and self._pub_rgb.get_subscription_count() > 0:
            self._pub_rgb.publish(self._image(bgr, "bgr8", stamp))
        if self._pub_depth.get_subscription_count() > 0:
            self._pub_depth.publish(self._image(depth.astype(np.uint16), "16UC1", stamp))

        ok, jpg = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_q])
        if ok:
            c = CompressedImage()
            c.header.stamp, c.header.frame_id = stamp, self._frame_id
            c.format = "jpeg"
            c.data = jpg.tobytes()
            self._pub_jpeg.publish(c)

        if self._info is not None:
            self._info.header.stamp = stamp
            self._pub_info.publish(self._info)
        if self._blob:
            self._publish_detections(det_msg, bgr, depth, stamp)
        self._frames += 1

    def _publish_detections(self, det_msg, bgr: np.ndarray, depth: np.ndarray, stamp: Time) -> None:
        h, w = bgr.shape[:2]
        fx = self._info.k[0] if self._info else None
        cx = self._info.k[2] if self._info else w / 2
        objects = []
        for d in (det_msg.detections if det_msg is not None else []):
            box = (int(max(0.0, d.xmin) * w), int(max(0.0, d.ymin) * h),
                   int(min(1.0, d.xmax) * w), int(min(1.0, d.ymax) * h))
            z = float(d.spatialCoordinates.z)
            label = self._labels[d.label] if 0 <= d.label < len(self._labels) else str(d.label)
            objects.append({
                "label": label,
                "confidence": round(float(d.confidence), 3),
                "distance_m": round(z / 1000.0, 2) if z > 0 else None,
                "xyz_m": [round(float(d.spatialCoordinates.x) / 1000.0, 2),
                          round(float(d.spatialCoordinates.y) / 1000.0, 2), round(z / 1000.0, 2)],
                "bearing_deg": round(math.degrees(math.atan2((box[0] + box[2]) / 2 - cx, fx)), 1) if fx else None,
                "box": box,
                "mask": depth_mask(depth, box, z),
            })
        objects.sort(key=lambda o: (o["distance_m"] is None, o["distance_m"] or 0.0))
        img = annotate(bgr, objects, f"OAK on-camera YOLO | {len(objects)} objects")
        ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_q])
        if ok:
            c = CompressedImage()
            c.header.stamp, c.header.frame_id = stamp, self._frame_id
            c.format = "jpeg"
            c.data = jpg.tobytes()
            self._pub_det_img.publish(c)
        self._pub_det.publish(String(data=json.dumps({
            "stamp_utc_ms": stamp.sec * 1000 + stamp.nanosec // 1_000_000,
            "source": "oak_on_camera_yolo",
            "width": w,
            "height": h,
            "objects": [dict({k: v for k, v in o.items() if k != "mask"},
                             bbox=[o["box"][0], o["box"][1], o["box"][2] - o["box"][0], o["box"][3] - o["box"][1]],
                             mask_px=int(o["mask"].sum()) if o["mask"] is not None else 0)
                        for o in objects],
        }, separators=(",", ":"))))
        self._detections += len(objects)

    def _image(self, array: np.ndarray, encoding: str, stamp: Time) -> Image:
        msg = Image()
        msg.header.stamp, msg.header.frame_id = stamp, self._frame_id
        msg.height, msg.width = array.shape[:2]
        msg.encoding = encoding
        msg.is_bigendian = 0
        msg.step = int(array.strides[0])
        msg.data = np.ascontiguousarray(array).tobytes()
        return msg

    def _report(self) -> None:
        t0, n0 = self._last_report
        now = time.monotonic()
        rate = (self._frames - n0) / max(1e-6, now - t0)
        self._last_report = (now, self._frames)
        if self._frames:
            extra = f", on-camera detector, {self._detections} objects in 10 s" if self._blob else ""
            self._detections = 0
            self.get_logger().info(f"publishing {rate:.1f} fps (rgb + aligned depth{extra})")
        else:
            self.get_logger().warn("no frames from the OAK yet")

    def destroy_node(self) -> None:
        self._stop.set()
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OakRgbdNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
