#!/usr/bin/env python3
"""TensorRT-accelerated streaming SAM 2 segmentation (design note §5.3).

Fixes applied relative to the original design note, and why each one is not
optional:

  * **Per-frame CUDA buffer allocation (`cuda.mem_alloc` in the hot path,
    never freed) is a guaranteed VRAM leak.** At the target 20 Hz this
    exhausts the 3.2 GB budget (G-02) in well under a minute and the node
    then crashes with `cuMemAlloc failed: out of memory`. Device buffers are
    now allocated ONCE in `__init__`, sized for a fixed `max_prompts` slot
    count (padded/truncated per frame), not reallocated per callback.
  * **`context.execute_v2(bindings=...)` does not exist on TensorRT 10.x.**
    TensorRT removed the positional bindings-list execution API (`execute_v2`
    /`enqueue_v2`) in TensorRT 10; only the named-tensor-address API
    (`set_tensor_address` + `execute_async_v3`) remains. The original code
    would raise `AttributeError` on the very first inference call against
    the TensorRT 10.x this design note itself targets (§4.2). Rewritten to
    the current API, with an explicit CUDA stream.
  * Engine loading is wrapped: a missing/unbuilt `.engine` file now fails
    fast with an actionable message (run scripts/build_trt_engines.sh) in a
    clean log line, not a bare traceback several layers deep in pycuda.
  * The TensorRT/PyCUDA imports are deferred and guarded so this file still
    imports cleanly (e.g. for linting, or on a dev machine with no GPU) —
    only actually instantiating the node requires a real Jetson.

Fixed-shape contract: this node and export_sam2_tensorrt.py agree on exactly
`max_prompts` box prompts per inference call (default 16, see
config/sam2_params.yaml) and on the ONNX I/O tensor names "input_image",
"input_boxes", "output_masks". Unused prompt slots are padded with a
degenerate [0,0,1,1] box; their output mask channels are ignored.
"""
import os

import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from vision_msgs.msg import Detection2DArray

try:
    import cv2
    import pycuda.autoinit  # noqa: F401 — creates the CUDA context on import
    import pycuda.driver as cuda
    import tensorrt as trt

    _HAS_TRT = True
except Exception as _trt_import_exc:  # noqa: BLE001
    _HAS_TRT = False
    _TRT_IMPORT_ERROR = _trt_import_exc

import message_filters

_INPUT_NAME = "input_image"
_BOXES_NAME = "input_boxes"
_OUTPUT_NAME = "output_masks"
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class SAM2InferenceNode(Node):
    def __init__(self):
        super().__init__("sam2_inference_node")

        if not _HAS_TRT:
            raise RuntimeError(
                "sam2_inference_node requires tensorrt + pycuda + opencv-python, "
                f"which failed to import ({_TRT_IMPORT_ERROR}). This node only "
                "runs on the Jetson's 'prod-jetson' image; see Containerfile."
            )

        self.bridge = CvBridge()
        self.declare_parameter("engine_path", "/opt/models/sam2_hiera_tiny.engine")
        self.declare_parameter("mask_threshold", 0.5)
        self.declare_parameter("input_size", 1024)
        self.declare_parameter("max_prompts", 16)
        self.declare_parameter("image_topic", "/camera/front/image_raw")

        self._input_size = int(self.get_parameter("input_size").value)
        self._max_prompts = int(self.get_parameter("max_prompts").value)
        self._mask_thresh = float(self.get_parameter("mask_threshold").value)

        engine_path = self.get_parameter("engine_path").get_parameter_value().string_value
        if not os.path.isfile(engine_path):
            raise RuntimeError(
                f"TensorRT engine not found at '{engine_path}'. Run "
                "scripts/build_trt_engines.sh on the Jetson before launching "
                "this node."
            )

        self.get_logger().info(f"Loading TensorRT SAM 2 engine from {engine_path}")
        self._trt_logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as f, trt.Runtime(self._trt_logger) as runtime:
            self.engine = runtime.deserialize_cuda_engine(f.read())
        if self.engine is None:
            raise RuntimeError(f"Failed to deserialize TensorRT engine '{engine_path}' (corrupt or wrong TRT version).")
        self.context = self.engine.create_execution_context()
        self.stream = cuda.Stream()

        # ── Fixed-size device buffers, allocated once. ──────────────────────
        n = self._max_prompts
        s = self._input_size
        self._host_input = np.zeros((1, 3, s, s), dtype=np.float32)
        self._host_boxes = np.zeros((1, n, 4), dtype=np.float32)
        self._host_output = np.zeros((1, n, s, s), dtype=np.float32)
        self._d_input = cuda.mem_alloc(self._host_input.nbytes)
        self._d_boxes = cuda.mem_alloc(self._host_boxes.nbytes)
        self._d_output = cuda.mem_alloc(self._host_output.nbytes)

        for name, addr in (
            (_INPUT_NAME, int(self._d_input)),
            (_BOXES_NAME, int(self._d_boxes)),
            (_OUTPUT_NAME, int(self._d_output)),
        ):
            if name not in [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]:
                raise RuntimeError(
                    f"Engine '{engine_path}' has no I/O tensor named '{name}'. "
                    "It was not built by this package's export_sam2_tensorrt.py "
                    "(tensor names must match exactly)."
                )
            self.context.set_tensor_address(name, addr)

        img_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=5)
        self.sub_img = message_filters.Subscriber(
            self, Image, self.get_parameter("image_topic").get_parameter_value().string_value,
            qos_profile=img_qos,
        )
        self.sub_prompts = message_filters.Subscriber(self, Detection2DArray, "/perception/sam2_prompts")
        self.sync = message_filters.ApproximateTimeSynchronizer(
            [self.sub_img, self.sub_prompts], queue_size=5, slop=0.04,
        )
        self.sync.registerCallback(self._inference_cb)

        self.pub_masks = self.create_publisher(Image, "/perception/sam2_masks", 10)
        self.get_logger().info(
            f"SAM 2 TensorRT inference ready: engine={engine_path}, "
            f"max_prompts={self._max_prompts}, input_size={self._input_size}."
        )

    def _inference_cb(self, img_msg: Image, prompts_msg: Detection2DArray):
        n_real = min(len(prompts_msg.detections), self._max_prompts)
        if n_real == 0:
            return
        if len(prompts_msg.detections) > self._max_prompts:
            self.get_logger().warn(
                f"{len(prompts_msg.detections)} prompts this frame, truncating "
                f"to max_prompts={self._max_prompts}", throttle_duration_sec=5.0,
            )

        frame = self.bridge.imgmsg_to_cv2(img_msg, desired_encoding="rgb8")
        orig_h, orig_w = frame.shape[:2]

        s = self._input_size
        scale = s / max(orig_h, orig_w)
        nh, nw = int(orig_h * scale), int(orig_w * scale)
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)

        self._host_input.fill(0.0)
        normalized = (resized.astype(np.float32) / 255.0 - _IMAGENET_MEAN) / _IMAGENET_STD
        self._host_input[0, :, :nh, :nw] = normalized.transpose(2, 0, 1)

        self._host_boxes.fill(0.0)
        self._host_boxes[0, :, 2:] = 1.0  # degenerate [0,0,1,1] placeholder for unused slots
        for i in range(n_real):
            det = prompts_msg.detections[i]
            cx = det.bbox.center.position.x * scale
            cy = det.bbox.center.position.y * scale
            sx = det.bbox.size_x * scale
            sy = det.bbox.size_y * scale
            self._host_boxes[0, i] = [cx - sx / 2.0, cy - sy / 2.0, cx + sx / 2.0, cy + sy / 2.0]

        cuda.memcpy_htod_async(self._d_input, self._host_input, self.stream)
        cuda.memcpy_htod_async(self._d_boxes, self._host_boxes, self.stream)
        self.context.execute_async_v3(self.stream.handle)
        cuda.memcpy_dtoh_async(self._host_output, self._d_output, self.stream)
        self.stream.synchronize()

        # Bit encodings: 0 = water, 1 = buoy/rigid obstacle, 2 = kelp canopy.
        combined_mask = np.zeros((orig_h, orig_w), dtype=np.uint8)
        for i in range(n_real):
            raw_mask = self._host_output[0, i, :nh, :nw]
            bin_mask = (raw_mask > self._mask_thresh).astype(np.uint8)
            bin_mask_orig = cv2.resize(bin_mask, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
            cls_name = ""
            if prompts_msg.detections[i].results:
                cls_name = prompts_msg.detections[i].results[0].hypothesis.class_id
            val = 2 if "kelp" in cls_name.lower() else 1
            combined_mask[bin_mask_orig > 0] = val

        out_msg = self.bridge.cv2_to_imgmsg(combined_mask, encoding="mono8")
        out_msg.header = img_msg.header
        self.pub_masks.publish(out_msg)

    def destroy_node(self):
        # Explicit, orderly device-memory release (not strictly required —
        # pycuda.autoinit tears the context down at interpreter exit anyway —
        # but avoids relying on destructor ordering across pycuda/rclpy.
        for buf in (
            getattr(self, "_d_input", None),
            getattr(self, "_d_boxes", None),
            getattr(self, "_d_output", None),
        ):
            if buf is not None:
                buf.free()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = SAM2InferenceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
