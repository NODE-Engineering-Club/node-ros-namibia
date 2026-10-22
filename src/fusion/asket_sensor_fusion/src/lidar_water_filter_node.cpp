// File: src/fusion/asket_sensor_fusion/src/lidar_water_filter_node.cpp
//
// Dynamic sea-surface plane filter (design note §2.2, §5.1). Subscribes to
// the inverted Livox point cloud, transforms it into base_link, fits a
// RANSAC plane close to the IMU-predicted horizontal, and keeps only points
// that sit strictly between water-plane-distance_thresh and
// max_obstacle_height above that plane. Structurally this matched the
// design note as given; the one fix applied is an explicit Eigen include
// (PCL headers pull it in transitively today, but relying on a transitive
// include across PCL versions is the kind of thing that breaks a build on
// an unrelated upgrade).
#include <algorithm>
#include <cmath>
#include <memory>
#include <string>

#include <Eigen/Dense>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>
#include <tf2_sensor_msgs/tf2_sensor_msgs.hpp>

#include <pcl/filters/extract_indices.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl/sample_consensus/method_types.h>
#include <pcl/sample_consensus/model_types.h>
#include <pcl/segmentation/sac_segmentation.h>
#include <pcl_conversions/pcl_conversions.h>

class LidarWaterFilterNode : public rclcpp::Node
{
public:
  LidarWaterFilterNode()
  : Node("lidar_water_filter_node")
  {
    this->declare_parameter<std::string>("input_cloud_topic", "/livox/lidar");
    this->declare_parameter<std::string>("output_cloud_topic", "/lidar/filtered_obstacles");
    this->declare_parameter<std::string>("target_frame", "base_link");
    this->declare_parameter<double>("water_plane_distance_thresh", 0.08);
    this->declare_parameter<double>("max_obstacle_height", 2.5);
    this->declare_parameter<double>("min_range", 0.3);
    this->declare_parameter<double>("max_range", 45.0);
    this->declare_parameter<int>("min_points_for_fit", 50);

    tf_buffer_ = std::make_shared<tf2_ros::Buffer>(this->get_clock());
    tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);

    sub_cloud_ = this->create_subscription<sensor_msgs::msg::PointCloud2>(
      this->get_parameter("input_cloud_topic").as_string(),
      rclcpp::SensorDataQoS(),
      std::bind(&LidarWaterFilterNode::cloudCallback, this, std::placeholders::_1));

    pub_filtered_ = this->create_publisher<sensor_msgs::msg::PointCloud2>(
      this->get_parameter("output_cloud_topic").as_string(), 10);

    RCLCPP_INFO(
      this->get_logger(),
      "LidarWaterFilterNode initialized with dynamic sea-surface tracking.");
  }

private:
  void cloudCallback(const sensor_msgs::msg::PointCloud2::SharedPtr msg)
  {
    sensor_msgs::msg::PointCloud2 cloud_transformed;
    const std::string target_frame = this->get_parameter("target_frame").as_string();

    try {
      const geometry_msgs::msg::TransformStamped tf_stamped = tf_buffer_->lookupTransform(
        target_frame, msg->header.frame_id, msg->header.stamp,
        rclcpp::Duration::from_seconds(0.05));
      tf2::doTransform(*msg, cloud_transformed, tf_stamped);
    } catch (const tf2::TransformException & ex) {
      RCLCPP_WARN_THROTTLE(
        this->get_logger(), *this->get_clock(), 1000,
        "TF transform %s -> %s unavailable: %s",
        msg->header.frame_id.c_str(), target_frame.c_str(), ex.what());
      return;
    }

    pcl::PointCloud<pcl::PointXYZI>::Ptr pcl_cloud(new pcl::PointCloud<pcl::PointXYZI>);
    pcl::fromROSMsg(cloud_transformed, *pcl_cloud);
    if (pcl_cloud->empty()) {
      return;
    }

    // 1. Range filtering.
    const double min_r = this->get_parameter("min_range").as_double();
    const double max_r = this->get_parameter("max_range").as_double();
    pcl::PointCloud<pcl::PointXYZI>::Ptr range_filtered(new pcl::PointCloud<pcl::PointXYZI>);
    range_filtered->reserve(pcl_cloud->size());
    for (const auto & pt : pcl_cloud->points) {
      const double r = std::sqrt(pt.x * pt.x + pt.y * pt.y);
      if (r >= min_r && r <= max_r) {
        range_filtered->push_back(pt);
      }
    }

    const int min_points = this->get_parameter("min_points_for_fit").as_int();
    if (static_cast<int>(range_filtered->size()) < min_points) {
      return;
    }

    // 2. RANSAC dynamic water-plane identification (axis-constrained to
    // roughly Z-up in base_link; this tolerates the boat's own roll/pitch
    // via eps_angle, it does not require a perfectly level hull).
    pcl::ModelCoefficients::Ptr coefficients(new pcl::ModelCoefficients);
    pcl::PointIndices::Ptr inliers(new pcl::PointIndices);
    pcl::SACSegmentation<pcl::PointXYZI> seg;
    seg.setOptimizeCoefficients(true);
    seg.setModelType(pcl::SACMODEL_PERPENDICULAR_PLANE);
    seg.setMethodType(pcl::SAC_RANSAC);
    seg.setMaxIterations(100);
    seg.setDistanceThreshold(this->get_parameter("water_plane_distance_thresh").as_double());
    seg.setAxis(Eigen::Vector3f(0.0F, 0.0F, 1.0F));
    seg.setEpsAngle(0.35F);  // ~20 deg of boat roll/pitch tolerance
    seg.setInputCloud(range_filtered);
    seg.segment(*inliers, *coefficients);

    if (inliers->indices.empty()) {
      RCLCPP_WARN_THROTTLE(
        this->get_logger(), *this->get_clock(), 2000,
        "Failed to fit dynamic water plane this frame.");
      return;
    }

    // 3. Keep points strictly above the dynamic water surface.
    const double a = coefficients->values[0];
    const double b = coefficients->values[1];
    const double c = coefficients->values[2];
    const double d = coefficients->values[3];
    const double norm = std::sqrt(a * a + b * b + c * c);
    if (norm < 1e-6) {
      RCLCPP_WARN_THROTTLE(
        this->get_logger(), *this->get_clock(), 2000,
        "Degenerate water-plane fit (near-zero normal) — skipping frame.");
      return;
    }

    const double max_h = this->get_parameter("max_obstacle_height").as_double();
    const double min_h = this->get_parameter("water_plane_distance_thresh").as_double();

    pcl::PointCloud<pcl::PointXYZI>::Ptr obstacles(new pcl::PointCloud<pcl::PointXYZI>);
    for (const auto & pt : range_filtered->points) {
      const double signed_dist = (a * pt.x + b * pt.y + c * pt.z + d) / norm;
      if (signed_dist > min_h && signed_dist < max_h) {
        obstacles->push_back(pt);
      }
    }

    sensor_msgs::msg::PointCloud2 out_msg;
    pcl::toROSMsg(*obstacles, out_msg);
    out_msg.header.stamp = msg->header.stamp;
    out_msg.header.frame_id = target_frame;
    pub_filtered_->publish(out_msg);
  }

  rclcpp::Subscription<sensor_msgs::msg::PointCloud2>::SharedPtr sub_cloud_;
  rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr pub_filtered_;
  std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
  std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<LidarWaterFilterNode>());
  rclcpp::shutdown();
  return 0;
}
