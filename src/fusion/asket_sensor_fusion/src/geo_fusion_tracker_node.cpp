// File: src/fusion/asket_sensor_fusion/src/geo_fusion_tracker_node.cpp
//
// Transforms local 3D obstacle detections into the map frame (UM982
// RTK + GPS-yaw via the existing TF tree, per design note §2.5) and tracks
// them with a constant-velocity Kalman filter + gated nearest-neighbour
// association (asket_sensor_fusion/ekf_tracker.hpp).
//
// The original design note's version of this node had NO tracking at all —
// it transformed each raw per-frame detection straight through with no
// track IDs, no velocity, no persistence, no confirm/miss lifecycle —
// despite the architecture (§1, §2.5, "Superpower 1 & 2") explicitly
// promising "3D EKF Tracking (Hungarian Association + velocity)". Without
// it, a single noisy frame (a wave crest briefly surviving the water-plane
// filter, a mis-segmented glint) would reach Nav2's costmap as a confirmed
// obstacle just as readily as something real, and nothing would carry a
// velocity estimate for "Superpower 1" (georeferenced sonar/obstacle
// tracking) to use. ekf_tracker.hpp implements the promised tracker; this
// node is its ROS wiring.
#include <algorithm>
#include <array>
#include <cmath>
#include <memory>
#include <string>
#include <vector>

#include <asket_sensor_fusion/ekf_tracker.hpp>
#include <geometry_msgs/msg/point32.hpp>
#include <geometry_msgs/msg/polygon_stamped.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <sensor_msgs/point_cloud2_iterator.hpp>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>
#include <vision_msgs/msg/detection3_d.hpp>
#include <vision_msgs/msg/detection3_d_array.hpp>

class GeoFusionTrackerNode : public rclcpp::Node
{
public:
  GeoFusionTrackerNode()
  : Node("geo_fusion_tracker_node")
  {
    this->declare_parameter<std::string>("map_frame", "map");
    this->declare_parameter<double>("track_gate_m", 2.0);
    this->declare_parameter<double>("track_process_var", 1.0);
    this->declare_parameter<double>("track_meas_var", 0.10);
    this->declare_parameter<int>("track_confirm_hits", 3);
    this->declare_parameter<int>("track_max_misses", 5);
    this->declare_parameter<double>("canopy_safety_buffer_m", 1.0);

    map_frame_ = this->get_parameter("map_frame").as_string();
    canopy_buffer_m_ = this->get_parameter("canopy_safety_buffer_m").as_double();

    tracker_ = std::make_unique<asket_sensor_fusion::EkfTracker>(
      this->get_parameter("track_gate_m").as_double(),
      this->get_parameter("track_process_var").as_double(),
      this->get_parameter("track_meas_var").as_double(),
      static_cast<int>(this->get_parameter("track_confirm_hits").as_int()),
      static_cast<int>(this->get_parameter("track_max_misses").as_int()));

    tf_buffer_ = std::make_shared<tf2_ros::Buffer>(this->get_clock());
    tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);

    sub_local_obstacles_ = this->create_subscription<vision_msgs::msg::Detection3DArray>(
      "/fusion/obstacles_local", 10,
      std::bind(&GeoFusionTrackerNode::obstacleCallback, this, std::placeholders::_1));

    pub_global_obstacles_ = this->create_publisher<vision_msgs::msg::Detection3DArray>(
      "/obstacles/fused_tracked", 10);
    pub_canopy_keepout_ = this->create_publisher<geometry_msgs::msg::PolygonStamped>(
      "/fusion/canopy_polygon", 10);
    // Nav2's ObstacleLayer marks/clears from a PointCloud2, not from a
    // Detection3DArray — this is what bringup/config/nav2_params.yaml's
    // sam2_obstacle_layer actually consumes, so confirmed tracks reach the
    // costmap, not just the /obstacles/fused_tracked topic nothing in Nav2
    // reads.
    pub_tracked_points_ = this->create_publisher<sensor_msgs::msg::PointCloud2>(
      "/obstacles/fused_tracked_points", 10);

    RCLCPP_INFO(
      this->get_logger(),
      "GeoFusionTrackerNode online. RTK-referenced EKF tracking enabled "
      "(gate=%.1fm, confirm=%d hits, max_misses=%d).",
      this->get_parameter("track_gate_m").as_double(),
      static_cast<int>(this->get_parameter("track_confirm_hits").as_int()),
      static_cast<int>(this->get_parameter("track_max_misses").as_int()));
  }

private:
  void obstacleCallback(const vision_msgs::msg::Detection3DArray::SharedPtr msg)
  {
    geometry_msgs::msg::TransformStamped tf_map_base;
    try {
      tf_map_base = tf_buffer_->lookupTransform(
        map_frame_, msg->header.frame_id, msg->header.stamp,
        rclcpp::Duration::from_seconds(0.05));
    } catch (const tf2::TransformException & ex) {
      RCLCPP_WARN_THROTTLE(
        this->get_logger(), *this->get_clock(), 1000,
        "TF %s -> %s unavailable: %s", map_frame_.c_str(),
        msg->header.frame_id.c_str(), ex.what());
      return;
    }

    // dt for the Kalman predict step, from the node clock (matches the
    // already-tested Python tracker's convention in geo_fusion_node.py).
    const rclcpp::Time now = this->get_clock()->now();
    double dt = 1e-3;
    if (last_update_time_.nanoseconds() > 0) {
      dt = (now - last_update_time_).seconds();
    }
    last_update_time_ = now;

    // Each Measurement carries its own radius (derived from the detection's
    // bbox footprint), so the tracker can hand it back on every confirmed
    // Track without this node needing to separately correlate detections
    // to tracks after association — ekf_tracker.hpp's update() already
    // copies radius/class_id/confidence from the matched measurement onto
    // the track it updates.
    std::vector<asket_sensor_fusion::Measurement> meas;
    meas.reserve(msg->detections.size());
    for (const auto & det : msg->detections) {
      geometry_msgs::msg::PoseStamped local_pose, global_pose;
      local_pose.header = msg->header;
      local_pose.pose = det.bbox.center;
      tf2::doTransform(local_pose, global_pose, tf_map_base);

      asket_sensor_fusion::Measurement m;
      m.x = global_pose.pose.position.x;
      m.y = global_pose.pose.position.y;
      m.class_id = det.results.empty() ? "unknown" : det.results[0].hypothesis.class_id;
      m.confidence = det.results.empty() ? 0.0 : det.results[0].hypothesis.score;
      m.radius = std::hypot(det.bbox.size.x, det.bbox.size.y) / 2.0;
      meas.push_back(m);
    }

    tracker_->predict(dt);
    const auto tracks = tracker_->update(meas);

    vision_msgs::msg::Detection3DArray global_detections;
    global_detections.header.stamp = msg->header.stamp;
    global_detections.header.frame_id = map_frame_;

    // One point per confirmed track's footprint corners + centroid (5
    // points), not just its centroid: a single point per tracked blob would
    // under-represent a wide kelp patch or buoy cluster in the costmap
    // until inflation_radius happens to cover the gap, which is exactly the
    // kind of margin that should come from the tracked SIZE, not from
    // hoping the inflation radius is large enough.
    std::vector<std::array<float, 3>> cloud_points;

    for (const auto & tr : tracks) {
      vision_msgs::msg::Detection3D det;
      det.header = global_detections.header;
      // NOTE: vision_msgs::msg::Detection3D's exact field set (whether it
      // carries a `string id` on this installed vision_msgs version) was
      // not verified against the live Jazzy source in this environment —
      // not used here to avoid a hard compile-time risk for something
      // purely informational. tr.id (the persistent EKF track id) is
      // otherwise unused outside this loop; surface it once vision_msgs'
      // exact field is confirmed, e.g. via det.id or a results[].hypothesis
      // extension, rather than guessing at a field name here.
      det.bbox.center.position.x = tr.x(0);
      det.bbox.center.position.y = tr.x(1);
      det.bbox.size.x = std::max(0.2, tr.radius * 2.0);
      det.bbox.size.y = std::max(0.2, tr.radius * 2.0);
      det.bbox.size.z = 0.3;  // unknown from a 2D-tracked centroid; conservative placeholder

      vision_msgs::msg::ObjectHypothesisWithPose hyp;
      hyp.hypothesis.class_id = tr.class_id;
      hyp.hypothesis.score = tr.confidence;
      det.results.push_back(hyp);
      global_detections.detections.push_back(det);

      if (tr.class_id == "KELP_CANOPY") {
        publishKeepoutPolygon(
          det.bbox.center.position, det.bbox.size, global_detections.header.stamp);
      } else {
        // Rigid obstacles (buoys, debris, vessels) mark the costmap;
        // KELP_CANOPY does not also double-mark it here — it is already
        // handled, more conservatively, as a hard keep-out polygon above,
        // not as a point obstacle Nav2 might merely route tightly around.
        const double hx = det.bbox.size.x / 2.0;
        const double hy = det.bbox.size.y / 2.0;
        const double cx = det.bbox.center.position.x;
        const double cy = det.bbox.center.position.y;
        for (const auto & d : {
            std::array<double, 2>{0.0, 0.0}, std::array<double, 2>{hx, hy},
            std::array<double, 2>{hx, -hy}, std::array<double, 2>{-hx, hy},
            std::array<double, 2>{-hx, -hy}})
        {
          cloud_points.push_back(
            {static_cast<float>(cx + d[0]), static_cast<float>(cy + d[1]), 0.0F});
        }
      }
    }

    pub_global_obstacles_->publish(global_detections);
    publishTrackedPointCloud(cloud_points, global_detections.header.stamp);
  }

  void publishTrackedPointCloud(
    const std::vector<std::array<float, 3>> & points, const rclcpp::Time & stamp)
  {
    sensor_msgs::msg::PointCloud2 cloud;
    cloud.header.frame_id = map_frame_;
    cloud.header.stamp = stamp;
    cloud.height = 1;
    cloud.width = static_cast<uint32_t>(points.size());
    cloud.is_dense = true;

    sensor_msgs::PointCloud2Modifier modifier(cloud);
    modifier.setPointCloud2FieldsByString(1, "xyz");
    modifier.resize(points.size());

    sensor_msgs::PointCloud2Iterator<float> it_x(cloud, "x");
    sensor_msgs::PointCloud2Iterator<float> it_y(cloud, "y");
    sensor_msgs::PointCloud2Iterator<float> it_z(cloud, "z");
    for (const auto & p : points) {
      *it_x = p[0];
      *it_y = p[1];
      *it_z = p[2];
      ++it_x; ++it_y; ++it_z;
    }

    pub_tracked_points_->publish(cloud);
  }

  void publishKeepoutPolygon(
    const geometry_msgs::msg::Point & center,
    const geometry_msgs::msg::Vector3 & size,
    const rclcpp::Time & stamp)
  {
    geometry_msgs::msg::PolygonStamped poly;
    poly.header.frame_id = map_frame_;
    poly.header.stamp = stamp;

    const double hx = size.x / 2.0 + canopy_buffer_m_;
    const double hy = size.y / 2.0 + canopy_buffer_m_;

    geometry_msgs::msg::Point32 p1, p2, p3, p4;
    p1.x = center.x - hx; p1.y = center.y - hy;
    p2.x = center.x + hx; p2.y = center.y - hy;
    p3.x = center.x + hx; p3.y = center.y + hy;
    p4.x = center.x - hx; p4.y = center.y + hy;
    poly.polygon.points = {p1, p2, p3, p4};

    pub_canopy_keepout_->publish(poly);
  }

  std::string map_frame_;
  double canopy_buffer_m_{1.0};
  std::unique_ptr<asket_sensor_fusion::EkfTracker> tracker_;
  rclcpp::Time last_update_time_{0, 0, RCL_ROS_TIME};

  rclcpp::Subscription<vision_msgs::msg::Detection3DArray>::SharedPtr sub_local_obstacles_;
  rclcpp::Publisher<vision_msgs::msg::Detection3DArray>::SharedPtr pub_global_obstacles_;
  rclcpp::Publisher<geometry_msgs::msg::PolygonStamped>::SharedPtr pub_canopy_keepout_;
  rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr pub_tracked_points_;
  std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
  std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<GeoFusionTrackerNode>());
  rclcpp::shutdown();
  return 0;
}
