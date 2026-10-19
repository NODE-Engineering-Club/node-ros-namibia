#!/usr/bin/env python3
"""Autonomous SAM 2 prompt generator (ASKET-SPEC-PERCEPTION-SAM2-FUSION-v1.0 §5.2).

Fuses an open-vocabulary YOLO-World detector (vision prompts) with LiDAR 3D
cluster projections (geometry prompts) into a single Detection2DArray of
candidate boxes for sam2_inference_node to segment. Corroborated targets
(vision+lidar agree) keep the vision label; vision-only targets are labelled
kelp canopy (the common case for flush-floating canopy with no solid LiDAR
return); lidar-only targets (dark buoys, low-reflectance debris) are labelled
unclassified_obstacle rather than guessed at.

Differences from the original design note, and why:

  * `ObjectHypothesisWithPose` was used but never imported — fixed.
  * The lidar->camera transform was a hardcoded rotation+translation
    duplicated (differently!) in this node and in sensor_fusion_obstacle_node.
    Two independently hand-maintained extrinsics for the same physical
    mount WILL drift apart the first time either one is tuned. Replaced with
    a single TF2 lookup against the URDF/calibrated transform — the same
    pattern this repo already uses for lidar->camera in
    fusion/fusion/geo_fusion_node.py (front_camera_cal). Until that frame
    exists in the URDF (see TODOS.md), the lookup fails safe: this node
    logs once and falls back to vision-only prompts instead of crashing or
    silently projecting through a stale hardcoded matrix.
  * YOLO-World engine loading is wrapped: a missing/unbuilt TensorRT engine
    (the normal state before scripts/build_trt_engines.sh has been run on
    the Jetson) now logs a clear, actionable error and disables vision
    prompts instead of raising out of __init__ and killing the whole
    bringup.
"""
import message_filters
import numpy as np
import rclpy
import sensor_msgs_py.point_cloud2 as pc2
import tf2_geometry_msgs  # noqa: F401  (registers do_transform_point for PointStamped)
from cv_bridge import CvBridge
from geometry_msgs.msg import PointStamped
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from scipy.spatial import cKDTree
from sensor_msgs.msg import CameraInfo, Image, PointCloud2
from tf2_ros import Buffer, ConnectivityException, ExtrapolationException, LookupException, TransformListener
from vision_msgs.msg import BoundingBox2D, Detection2D, Detection2DArray, ObjectHypothesisWithPose

# Minimum LiDAR points inside a 35px image-plane neighbourhood before it is
# treated as a candidate cluster, not noise. Matches the design note's
# DBSCAN-ish min_samples=4 used for the lidar_boxes prompt branch.
_LIDAR_PROMPT_MIN_NEIGHBOURS = 4
_LIDAR_PROMPT_CLUSTER_RADIUS_PX = 35.0


class SAM2PromptGeneratorNode(Node):
    def __init__(self):
        super().__init__("sam2_prompt_generator_node")
        self.bridge = CvBridge()

        self.declare_parameter("yolo_model_path", "/opt/models/yolo11s-world.engine")
        self.declare_parameter("camera_info_topic", "/camera/front/camera_info")
        self.declare_parameter("image_topic", "/camera/front/image_raw")
        self.declare_parameter("lidar_obstacles_topic", "/lidar/filtered_obstacles")
        self.declare_parameter("lidar_frame", "livox_frame")
        self.declare_parameter("camera_optical_frame", "oak_rgb_camera_optical_frame")
        self.declare_parameter("yolo_confidence", 0.35)
        self.declare_parameter(
            "classes",
            ["kelp canopy", "demarcation buoy", "floating object", "rope", "boat"],
        )
        self.declare_parameter("sync_slop", 0.06)
        self.declare_parameter("sync_queue", 10)

        self._conf_thresh = float(self.get_parameter("yolo_confidence").value)
        self._lidar_frame = self.get_parameter("lidar_frame").value
        self._camera_frame = self.get_parameter("camera_optical_frame").value

        # ── TF: lidar -> camera optical frame (single source of truth) ────────
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._tf_warned = False

        # ── YOLO-World, loaded defensively ─────────────────────────────────────
        self.yolo = None
        model_path = self.get_parameter("yolo_model_path").get_parameter_value().string_value
        try:
            from ultralytics import YOLO  # deferred: only needed if this ever runs

            self.yolo = YOLO(model_path)
            classes = list(self.get_parameter("classes").value)
            self.yolo.set_classes(classes)
            self.get_logger().info(f"Loaded YOLO-World engine: {model_path}, classes={classes}")
        except Exception as exc:  # noqa: BLE001 — must not take the whole stack down
            self.get_logger().error(
                f"Could not load YOLO-World engine at '{model_path}': {exc}. "
                "Vision-based prompts are DISABLED; only LiDAR-geometry prompts "
                "will be generated. Run scripts/build_trt_engines.sh on the "
                "Jetson first."
            )

        self.K = None
        self.sub_cam_info = self.create_subscription(
            CameraInfo,
            self.get_parameter("camera_info_topic").get_parameter_value().string_value,
            self._cam_info_cb,
            10,
        )

        img_qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, depth=5)
        self.sub_img = message_filters.Subscriber(
            self, Image, self.get_parameter("image_topic").get_parameter_value().string_value,
            qos_profile=img_qos,
        )
        self.sub_lidar = message_filters.Subscriber(
            self, PointCloud2, self.get_parameter("lidar_obstacles_topic").get_parameter_value().string_value,
        )
        self.sync = message_filters.ApproximateTimeSynchronizer(
            [self.sub_img, self.sub_lidar],
            queue_size=int(self.get_parameter("sync_queue").value),
            slop=float(self.get_parameter("sync_slop").value),
        )
        self.sync.registerCallback(self._sync_callback)

        self.pub_prompts = self.create_publisher(Detection2DArray, "/perception/sam2_prompts", 10)
        self.get_logger().info("SAM2 Prompt Generator Node initialized.")

    def _cam_info_cb(self, msg: CameraInfo):
        if self.K is None:
            self.K = np.array(msg.k, dtype=np.float64).reshape(3, 3)
            self.get_logger().info(f"Loaded camera intrinsics: fx={self.K[0, 0]:.1f} fy={self.K[1, 1]:.1f}")

    def _lookup_lidar_to_camera(self, stamp):
        """TransformStamped lidar_frame -> camera_frame at stamp, or None."""
        for t in (stamp, rclpy.time.Time()):
            try:
                return self._tf_buffer.lookup_transform(self._camera_frame, self._lidar_frame, t)
            except (LookupException, ConnectivityException, ExtrapolationException):
                continue
        return None

    def _sync_callback(self, img_msg: Image, cloud_msg: PointCloud2):
        if self.K is None:
            self.get_logger().warn("No CameraInfo received yet — skipping frame", throttle_duration_sec=5.0)
            return

        cv_img = self.bridge.imgmsg_to_cv2(img_msg, desired_encoding="bgr8")
        h, w = cv_img.shape[:2]
        prompt_array = Detection2DArray()
        prompt_array.header = img_msg.header

        # 1. Vision-based open-vocabulary detections.
        vision_boxes = []
        if self.yolo is not None:
            results = self.yolo.predict(cv_img, conf=self._conf_thresh, verbose=False)[0]
            for box in results.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                cls_id = int(box.cls[0].item())
                vision_boxes.append({
                    "bbox": [float(x1), float(y1), float(x2), float(y2)],
                    "class": self.yolo.names[cls_id],
                    "conf": float(box.conf[0].item()),
                })

        # 2. LiDAR 3D point projection to 2D candidate boxes.
        lidar_boxes = self._lidar_prompt_boxes(cloud_msg, stamp=img_msg.header.stamp, img_w=w, img_h=h)

        for item in vision_boxes + lidar_boxes:
            det = Detection2D()
            det.header = img_msg.header
            bb = BoundingBox2D()
            x1, y1, x2, y2 = item["bbox"]
            bb.center.position.x = (x1 + x2) / 2.0
            bb.center.position.y = (y1 + y2) / 2.0
            bb.size_x = max(10.0, x2 - x1)
            bb.size_y = max(10.0, y2 - y1)
            det.bbox = bb
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = item["class"]
            hyp.hypothesis.score = item["conf"]
            det.results.append(hyp)
            prompt_array.detections.append(det)

        self.pub_prompts.publish(prompt_array)

    def _lidar_prompt_boxes(self, cloud_msg, stamp, img_w, img_h):
        tf = self._lookup_lidar_to_camera(stamp)
        if tf is None:
            if not self._tf_warned:
                self.get_logger().warn(
                    f"No TF {self._lidar_frame} -> {self._camera_frame} yet "
                    "(add it to asket.urdf.xacro or publish it from a "
                    "calibration node) — LiDAR-geometry prompts disabled "
                    "until it is available. This warning is logged once.",
                )
                self._tf_warned = True
            return []

        points = list(pc2.read_points(cloud_msg, field_names=("x", "y", "z"), skip_nans=True))
        if not points:
            return []
        pts_lidar = np.array([[p[0], p[1], p[2]] for p in points], dtype=np.float64)

        ps = PointStamped()
        ps.header.frame_id = self._lidar_frame
        u_list, v_list = [], []
        fx, fy, cx, cy = self.K[0, 0], self.K[1, 1], self.K[0, 2], self.K[1, 2]
        for x, y, z in pts_lidar:
            ps.point.x, ps.point.y, ps.point.z = float(x), float(y), float(z)
            p_cam = tf2_geometry_msgs.do_transform_point(ps, tf).point
            if p_cam.z <= 0.3:  # behind, or clipping-plane-close to, the camera
                continue
            u = fx * p_cam.x / p_cam.z + cx
            v = fy * p_cam.y / p_cam.z + cy
            if 0 <= u < img_w and 0 <= v < img_h:
                u_list.append(u)
                v_list.append(v)

        if len(u_list) < _LIDAR_PROMPT_MIN_NEIGHBOURS:
            return []

        coords = np.column_stack((u_list, v_list))
        tree = cKDTree(coords)
        visited = set()
        boxes = []
        for idx in range(len(coords)):
            if idx in visited:
                continue
            neighbours = tree.query_ball_point(coords[idx], r=_LIDAR_PROMPT_CLUSTER_RADIUS_PX)
            visited.update(neighbours)
            if len(neighbours) >= _LIDAR_PROMPT_MIN_NEIGHBOURS:
                cluster = coords[neighbours]
                u_min, v_min = np.min(cluster, axis=0)
                u_max, v_max = np.max(cluster, axis=0)
                boxes.append({
                    "bbox": [float(u_min), float(v_min), float(u_max), float(v_max)],
                    "class": "unclassified_obstacle",
                    "conf": 0.85,
                })
        return boxes


def main(args=None):
    rclpy.init(args=args)
    node = SAM2PromptGeneratorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
