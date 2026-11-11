#include "boat_bt/boat_bt_node.hpp"

using namespace std::chrono_literals;


BoatBTNode::BoatBTNode()
: Node("boat_bt_node"),
  odom_received_(false),
  mission_status_received_(false),
  mission_state_(njord_msgs::msg::MissionStatus::IDLE),
  tree_finished_(false),
  collision_risk_detected_(false),
  avoidance_target_ready_(false),
  last_avoidance_request_id_(0)
{
  // -----------------------------------------------------------------------
  // Collision Avoidance configuration
  //
  // NOTE:
  // The current fusion tracker estimates velocity in base_link and does not
  // yet compensate for ego motion. Therefore this is intentionally a
  // relative-risk detector, not a full COLREG / CPA implementation.
  // -----------------------------------------------------------------------

  declare_parameter<double>(
    "collision_risk_range_m",
    20.0);

  declare_parameter<double>(
    "collision_forward_sector_deg",
    60.0);

  declare_parameter<double>(
    "collision_min_relative_speed_mps",
    0.15);

  declare_parameter<double>(
    "collision_avoidance_offset_m",
    12.0);

  declare_parameter<double>(
    "request_cooldown_sec",
    5.0);

  // -----------------------------------------------------------------------
  // Buoy minimum standoff
  // -----------------------------------------------------------------------

  declare_parameter<std::string>(
    "buoy_green_class_id",
    "");

  declare_parameter<std::string>(
    "buoy_red_class_id",
    "");

  declare_parameter<double>(
    "buoy_min_standoff_m",
    1.0);

  // -----------------------------------------------------------------------
  // Read collision-avoidance parameters
  // -----------------------------------------------------------------------

  collision_risk_range_m_ =
    get_parameter(
    "collision_risk_range_m").as_double();

  collision_forward_sector_deg_ =
    get_parameter(
    "collision_forward_sector_deg").as_double();

  collision_min_relative_speed_mps_ =
    get_parameter(
    "collision_min_relative_speed_mps").as_double();

  collision_avoidance_offset_m_ =
    get_parameter(
    "collision_avoidance_offset_m").as_double();

  request_cooldown_sec_ =
    get_parameter(
    "request_cooldown_sec").as_double();

  // -----------------------------------------------------------------------
  // Read buoy standoff parameters
  // -----------------------------------------------------------------------

  buoy_green_class_id_ =
    get_parameter(
    "buoy_green_class_id").as_string();

  buoy_red_class_id_ =
    get_parameter(
    "buoy_red_class_id").as_string();

  buoy_min_standoff_m_ =
    get_parameter(
    "buoy_min_standoff_m").as_double();

  // -----------------------------------------------------------------------
  // ROS interfaces
  // -----------------------------------------------------------------------

  /*
   * Published on a dedicated topic, not /cmd_vel directly: Nav2's own
   * pipeline (controller_server, behavior_server's recovery behaviors,
   * collision_monitor's safety-stop heartbeat) also targets /cmd_vel
   * whenever it's alive, even with no active goal. twist_mux arbitrates the
   * two into the real /cmd_vel (see bringup/config/twist_mux.yaml).
   */
  cmd_pub_ =
    create_publisher<geometry_msgs::msg::Twist>(
    "/boat_bt/cmd_vel",
    10);

  // /odometry/gps is never published (see ekf.yaml: deliberately not
  // created, to avoid double-fusing GPS) -- WaitForOdom, the very first
  // node in MainTree's ReactiveSequence, gates the entire tree on
  // odom_received_ becoming true, so this must be a topic that is actually
  // published. Every other consumer in this codebase (mission_manager.py,
  // nav2_params.yaml) uses /odometry/filtered; this matches them.
  odom_sub_ =
    create_subscription<nav_msgs::msg::Odometry>(
    "/odometry/filtered",
    10,
    std::bind(
      &BoatBTNode::odom_callback,
      this,
      std::placeholders::_1));

  rclcpp::QoS mission_status_qos(1);
  mission_status_qos.transient_local();

  mission_status_sub_ =
    create_subscription<njord_msgs::msg::MissionStatus>(
    "/mission/status",
    mission_status_qos,
    std::bind(
      &BoatBTNode::mission_status_callback,
      this,
      std::placeholders::_1));

  obstacles_sub_ =
    create_subscription<njord_msgs::msg::ObstacleArray>(
    "/obstacles/global",
    10,
    std::bind(
      &BoatBTNode::obstacles_callback,
      this,
      std::placeholders::_1));

  bypass_client_ =
    create_client<njord_msgs::srv::SetBypassTarget>(
    "/mission/set_bypass_target");

  // -----------------------------------------------------------------------
  // Behavior Tree
  // -----------------------------------------------------------------------

  register_bt_nodes();

  const std::string xml_path =
    ament_index_cpp::get_package_share_directory(
    "boat_bt") +
    "/bt_xml/simple_boat.xml";

  tree_ =
    factory_.createTreeFromFile(xml_path);

  logger_ =
    std::make_unique<BT::StdCoutLogger>(
    tree_);

  timer_ =
    create_wall_timer(
    100ms,
    std::bind(
      &BoatBTNode::tick_tree,
      this));

  // -----------------------------------------------------------------------
  // Startup information
  // -----------------------------------------------------------------------

  RCLCPP_INFO(
    get_logger(),
    "boat_bt_node started with tree: %s",
    xml_path.c_str());

  if (buoy_green_class_id_.empty() && buoy_red_class_id_.empty()) {
    RCLCPP_INFO(
      get_logger(),
      "Buoy class mapping is not configured (buoy_green_class_id / "
      "buoy_red_class_id); the buoy minimum-standoff check is disabled.");
  }
  else {
    RCLCPP_INFO(
      get_logger(),
      "Buoy minimum standoff enabled: %.2f m "
      "(green class_id='%s', red class_id='%s')",
      buoy_min_standoff_m_,
      buoy_green_class_id_.c_str(),
      buoy_red_class_id_.c_str());
  }

  RCLCPP_INFO(
    get_logger(),
    "Collision avoidance relative-risk detector enabled: "
    "range=%.1f m, forward sector=+/-%.1f deg",
    collision_risk_range_m_,
    collision_forward_sector_deg_);
}


void BoatBTNode::odom_callback(
  const nav_msgs::msg::Odometry::SharedPtr msg)
{
  (void)msg;

  if (!odom_received_) {
    odom_received_ = true;

    RCLCPP_INFO(
      get_logger(),
      "Received first odometry message");
  }
}


void BoatBTNode::mission_status_callback(
  const njord_msgs::msg::MissionStatus::SharedPtr msg)
{
  const uint8_t previous_state =
    mission_state_;

  mission_status_received_ = true;
  mission_state_ = msg->state;

  RCLCPP_INFO(
    get_logger(),
    "Mission status received: "
    "state=%u, message='%s', waypoint=%u/%u",
    static_cast<unsigned int>(msg->state),
    msg->message.c_str(),
    msg->current_waypoint,
    msg->total_waypoints);

  /*
   * tick_tree() latches tree_finished_ = true (and stops ticking the tree,
   * GlobalSafety included) whenever MainTree resolves to SUCCESS or FAILURE,
   * which MissionMonitor causes on any mission ending SUCCEEDED, FAILED or
   * ABORTED. Re-arm the tree whenever a new mission starts, so one finished
   * or aborted mission cannot silently disable collision avoidance for every
   * mission after it in the same process.
   */
  const bool mission_started =
    mission_state_ ==
    njord_msgs::msg::MissionStatus::RUNNING &&
    previous_state !=
    njord_msgs::msg::MissionStatus::RUNNING;

  if (mission_started && tree_finished_) {
    tree_finished_ = false;

    RCLCPP_INFO(
      get_logger(),
      "New mission started; Behavior Tree re-armed");
  }
}


void BoatBTNode::obstacles_callback(
  const njord_msgs::msg::ObstacleArray::SharedPtr msg)
{
  current_boat_position_ =
    msg->boat_position;

  current_boat_heading_ =
    msg->boat_heading;

  updateCollisionRiskState(*msg);
}


void BoatBTNode::publishStopCommand()
{
  cmd_pub_->publish(
    geometry_msgs::msg::Twist());
}


void BoatBTNode::tick_tree()
{
  if (tree_finished_) {
    return;
  }

  if (!odom_received_) {
    return;
  }

  const BT::NodeStatus status =
    tree_.tickOnce();

  if (
    status ==
    BT::NodeStatus::SUCCESS)
  {
    tree_finished_ = true;

    publishStopCommand();

    RCLCPP_INFO(
      get_logger(),
      "Behavior Tree completed successfully");

    return;
  }

  if (
    status ==
    BT::NodeStatus::FAILURE)
  {
    tree_finished_ = true;

    publishStopCommand();

    RCLCPP_ERROR(
      get_logger(),
      "Behavior Tree failed");

    return;
  }
}


int main(
  int argc,
  char ** argv)
{
  rclcpp::init(
    argc,
    argv);

  auto node =
    std::make_shared<BoatBTNode>();

  rclcpp::spin(
    node);

  rclcpp::shutdown();

  return 0;
}
