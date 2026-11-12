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

Connection is USB (the camera is plugged in over USB on the bench) unless
``device_ip`` is set, which selects the PoE unit at that address.
"""

from __future__ import annotations

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

try:
    import depthai as dai

    _DAI_ERROR = None
except Exception as exc:  # noqa: BLE001
    dai = None
    _DAI_ERROR = exc


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

        self._fps = float(self.get_parameter("fps").value)
        self._w = int(self.get_parameter("width").value)
        self._h = int(self.get_parameter("height").value)
        self._frame_id = str(self.get_parameter("frame_id").value)
        self._jpeg_q = int(self.get_parameter("jpeg_quality").value)
        self._ip = str(self.get_parameter("device_ip").value)
        self._publish_raw = bool(self.get_parameter("publish_raw").value)

        qos = QoSProfile(depth=2, reliability=ReliabilityPolicy.BEST_EFFORT)
        self._pub_rgb = self.create_publisher(Image, "rgb/image_raw", qos)
        self._pub_jpeg = self.create_publisher(CompressedImage, "rgb/image_raw/compressed", qos)
        self._pub_info = self.create_publisher(CameraInfo, "rgb/camera_info", 10)
        self._pub_depth = self.create_publisher(Image, "stereo/depth", qos)

        self._info: CameraInfo | None = None
        self._stop = threading.Event()
        self._frames = 0
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
                    while not self._stop.is_set():
                        group = q.get()
                        self._publish(group)
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

    def _publish(self, group) -> None:
        rgb_msg = group["rgb"]
        depth_msg = group["depth"]
        stamp = self._stamp(rgb_msg.getTimestamp())

        bgr = rgb_msg.getCvFrame()
        depth = depth_msg.getFrame()
        if depth.shape[:2] != bgr.shape[:2]:
            depth = cv2.resize(depth, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)

        if self._publish_raw:
            self._pub_rgb.publish(self._image(bgr, "bgr8", stamp))
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
        self._frames += 1

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
            self.get_logger().info(f"publishing {rate:.1f} fps (rgb + aligned depth)")
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
