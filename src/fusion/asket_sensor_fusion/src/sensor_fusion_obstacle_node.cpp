// File: src/fusion/asket_sensor_fusion/src/sensor_fusion_obstacle_node.cpp
//
// Projects the water-filtered LiDAR cloud into the SAM 2 segmentation mask,
// extracts per-class 3D bounding boxes directly from the LiDAR geometry
// (this is what actually resolves Defect C11's fixed 5.0 m assumption —
// range comes from real 3D points, not a guess), and classifies each
// cluster as KELP_CANOPY vs BUOY_OBSTACLE.
//
// Two fixes applied relative to the original design note:
//
//   * The original hand-rolled a FIXED rotation (xc=-pt.y, yc=-pt.z, zc=pt.x)
//     with no translation at all, while sam2_prompt_node.py independently
//     hand-rolled a DIFFERENT rotation+translation for the same physical
//     camera mount. Two hardcoded extrinsics for one mount will not agree
//     with each other, let alone with reality, the first time anyone tunes
//     one of them. Replaced with a single TF2 lookup
//     (base_frame -> camera_optical_frame), transforming the whole cloud in
//     one call via tf2_sensor_msgs — the same pattern
//     lidar_water_filter_node.cpp already uses, so there is exactly one
//     calibrated transform in the whole pipeline, not three.
//   * The original took fx/fy/cx/cy as static launch parameters that could
//     silently drift from the camera's real, possibly-recalibrated
//     intrinsics. This node now requires a live sensor_msgs/CameraInfo
//     message (same requirement sam2_prompt_node.py already has) and warns
//     (not crashes) until one arrives.
#include <algorithm>
#include <memory>
#include <string>
#include <unordered_map>

#include <cv_bridge/cv_bridge.hpp>
#include <message_filters/subscriber.h>
#include <message_filters/sync_policies/approximate_time.h>
#include <message_filters/synchronizer.h>
#include <opencv2/opencv.hpp>
#include <pcl/common/common.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl_conversions/pcl_conversions.h>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/image_encodings.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>
#include <tf2_sensor_msgs/tf2_sensor_msgs.hpp>
#include <vision_msgs/msg/detection3_d.hpp>
#include <vision_msgs/msg/detection3_d_array.hpp>

class SensorFusionObstacleNode : public rclcpp::Node
{
public:
  SensorFusionObstacleNode()
  : Node("sensor_fusion_obstacle_node")
  {
    this->declare_parameter<std::string>("base_frame", "base_link");
    this->declare_parameter<std::string>("camera_optical_frame", "oak_rgb_camera_optical_frame");
    this->declare_parameter<std::string>("camera_info_topic", "/camera/front/camera_info");
    this->declare_parameter<int>("min_cluster_points", 5);

    base_frame_ = this->get_parameter("base_frame").as_string();
    camera_frame_ = this->get_parameter("camera_optical_frame").as_string();
    min_cluster_points_ = this->get_parameter("min_cluster_points").as_int();

    tf_buffer_ = std::make_shared<tf2_ros::Buffer>(this->get_clock());
    tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);

    sub_cam_info_ = this->create_subscription<sensor_msgs::msg::CameraInfo>(
      this->get_parameter("camera_info_topic").as_string(), 10,
      [this](const sensor_msgs::msg::CameraInfo::SharedPtr msg) {
        if (!have_intrinsics_) {
          fx_ = msg->k[0];
          fy_ = msg->k[4];
          cx_ = msg->k[2];
          cy_ = msg->k[5];
          have_intrinsics_ = true;
          RCLCPP_INFO(
            this->get_logger(), "Loaded camera intrinsics: fx=%.1f fy=%.1f cx=%.1f cy=%.1f",
            fx_, fy_, cx_, cy_);
        }
      });

    sub_mask_.subscribe(this, "/perception/sam2_masks");
    sub_cloud_.subscribe(this, "/lidar/filtered_obstacles");
    sync_ = std::make_shared<message_filters::Synchronizer<SyncPolicy>>(
      SyncPolicy(10), sub_mask_, sub_cloud_);
    sync_->registerCallback(
      std::bind(
        &SensorFusionObstacleNode::fusionCallback, this,
        std::placeholders::_1, std::placeholders::_2));

    pub_obstacles_3d_ = this->create_publisher<vision_msgs::msg::Detection3DArray>(
      "/fusion/obstacles_local", 10);
    RCLCPP_INFO(
      this->get_logger(),
      "SensorFusionObstacleNode online. Defect C11 (5m fixed projection) superseded.");
  }

private:
  typedef message_filters::sync_policies::ApproximateTime<
      sensor_msgs::msg::Image, sensor_msgs::msg::PointCloud2> SyncPolicy;

  void fusionCallback(
    const sensor_msgs::msg::Image::ConstSharedPtr & mask_msg,
    const sensor_msgs::msg::PointCloud2::ConstSharedPtr & cloud_msg)
  {
    if (!have_intrinsics_) {
      RCLCPP_WARN_THROTTLE(
        this->get_logger(), *this->get_clock(), 5000,
        "No CameraInfo received yet on the configured topic — skipping frame.");
      return;
    }

    cv_bridge::CvImageConstPtr cv_ptr;
    try {
      cv_ptr = cv_bridge::toCvShare(mask_msg, sensor_msgs::image_encodings::MONO8);
    } catch (const cv_bridge::Exception & e) {
      RCLCPP_ERROR(this->get_logger(), "cv_bridge exception: %s", e.what());
      return;
    }
    const cv::Mat & mask = cv_ptr->image;
    const int img_w = mask.cols;
    const int img_h = mask.rows;

    // Single calibrated transform for the whole cloud: base_frame_ (what
    // lidar_water_filter_node published in) -> camera_frame_. Fails safe
    // (skip this frame, warn once) if the TF isn't published yet, same
    // reasoning as sam2_prompt_node.py.
    sensor_msgs::msg::PointCloud2 cloud_in_camera;
    try {
      const geometry_msgs::msg::TransformStamped tf_stamped = tf_buffer_->lookupTransform(
        camera_frame_, cloud_msg->header.frame_id, cloud_msg->header.stamp,
        rclcpp::Duration::from_seconds(0.05));
      tf2::doTransform(*cloud_msg, cloud_in_camera, tf_stamped);
    } catch (const tf2::TransformException & ex) {
      RCLCPP_WARN_THROTTLE(
        this->get_logger(), *this->get_clock(), 5000,
        "TF %s -> %s unavailable: %s (add it to asket.urdf.xacro)",
        cloud_msg->header.frame_id.c_str(), camera_frame_.c_str(), ex.what());
      return;
    }

    // Keep the ORIGINAL base_frame points for the published 3D geometry
    // (Nav2/costmaps want obstacles in base_link, not in the camera's
    // optical frame) while using the camera-frame copy only to decide which
    // mask class each point falls under.
    pcl::PointCloud<pcl::PointXYZI>::Ptr cloud_base(new pcl::PointCloud<pcl::PointXYZI>);
    pcl::PointCloud<pcl::PointXYZI>::Ptr cloud_cam(new pcl::PointCloud<pcl::PointXYZI>);
    pcl::fromROSMsg(*cloud_msg, *cloud_base);
    pcl::fromROSMsg(cloud_in_camera, *cloud_cam);
    if (cloud_base->size() != cloud_cam->size()) {
      RCLCPP_ERROR(
        this->get_logger(),
        "Point count mismatch after TF transform (%zu vs %zu) — dropping frame.",
        cloud_base->size(), cloud_cam->size());
      return;
    }

    // mask value 1 = rigid obstacle/buoy, 2 = kelp canopy.
    std::unordered_map<uint8_t, pcl::PointCloud<pcl::PointXYZI>::Ptr> clusters;
    clusters[1] = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();
    clusters[2] = std::make_shared<pcl::PointCloud<pcl::PointXYZI>>();

    for (size_t i = 0; i < cloud_cam->size(); ++i) {
      const auto & pc = cloud_cam->points[i];
      if (pc.z <= 0.3F) {
        continue;  // behind, or clipping-plane-close to, the camera
      }
      const int u = static_cast<int>((fx_ * pc.x / pc.z) + cx_);
      const int v = static_cast<int>((fy_ * pc.y / pc.z) + cy_);
      if (u < 0 || u >= img_w || v < 0 || v >= img_h) {
        continue;
      }
      const uint8_t mask_val = mask.at<uint8_t>(v, u);
      if (mask_val == 1 || mask_val == 2) {
        clusters[mask_val]->push_back(cloud_base->points[i]);
      }
    }

    vision_msgs::msg::Detection3DArray out_detections;
    out_detections.header = cloud_msg->header;  // base_frame_, matches the published geometry

    for (auto & pair : clusters) {
      const uint8_t class_type = pair.first;
      auto & cluster_cloud = pair.second;
      if (static_cast<int>(cluster_cloud->size()) < min_cluster_points_) {
        continue;
      }

      Eigen::Vector4f min_pt, max_pt;
      pcl::getMinMax3D(*cluster_cloud, min_pt, max_pt);

      vision_msgs::msg::Detection3D det;
      det.header = cloud_msg->header;
      det.bbox.center.position.x = (min_pt[0] + max_pt[0]) / 2.0;
      det.bbox.center.position.y = (min_pt[1] + max_pt[1]) / 2.0;
      det.bbox.center.position.z = (min_pt[2] + max_pt[2]) / 2.0;
      det.bbox.size.x = std::max(0.2F, max_pt[0] - min_pt[0]);
      det.bbox.size.y = std::max(0.2F, max_pt[1] - min_pt[1]);
      det.bbox.size.z = std::max(0.2F, max_pt[2] - min_pt[2]);

      vision_msgs::msg::ObjectHypothesisWithPose hyp;
      hyp.hypothesis.class_id = (class_type == 2) ? "KELP_CANOPY" : "BUOY_OBSTACLE";
      hyp.hypothesis.score = 0.92;
      det.results.push_back(hyp);

      out_detections.detections.push_back(det);
    }

    pub_obstacles_3d_->publish(out_detections);
  }

  std::string base_frame_;
  std::string camera_frame_;
  int min_cluster_points_{5};
  double fx_{0.0}, fy_{0.0}, cx_{0.0}, cy_{0.0};
  bool have_intrinsics_{false};

  rclcpp::Subscription<sensor_msgs::msg::CameraInfo>::SharedPtr sub_cam_info_;
  message_filters::Subscriber<sensor_msgs::msg::Image> sub_mask_;
  message_filters::Subscriber<sensor_msgs::msg::PointCloud2> sub_cloud_;
  std::shared_ptr<message_filters::Synchronizer<SyncPolicy>> sync_;
  rclcpp::Publisher<vision_msgs::msg::Detection3DArray>::SharedPtr pub_obstacles_3d_;
  std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
  std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<SensorFusionObstacleNode>());
  rclcpp::shutdown();
  return 0;
}
