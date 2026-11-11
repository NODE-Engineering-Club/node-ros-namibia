#include "boat_bt/boat_bt_node.hpp"

void BoatBTNode::register_bt_nodes()
{
  factory_.registerSimpleCondition(
    "WaitForOdom",
    [this](BT::TreeNode &) {
      return odom_received_
        ? BT::NodeStatus::SUCCESS
        : BT::NodeStatus::FAILURE;
    });

  // -----------------------------------------------------------------------
  // Collision Avoidance
  // -----------------------------------------------------------------------

  factory_.registerSimpleCondition(
    "CollisionRiskDetected",
    [this](BT::TreeNode &) {
      return collision_risk_detected_
        ? BT::NodeStatus::SUCCESS
        : BT::NodeStatus::FAILURE;
    });

  factory_.registerSimpleAction(
    "DetermineAvoidanceTarget",
    [this](BT::TreeNode &) {
      if (!collision_risk_detected_) {
        avoidance_target_ready_ = false;
        return BT::NodeStatus::FAILURE;
      }

      /*
       * Choose the bypass side from the obstacle bearing.
       *
       * Positive bearing means the obstacle is on the boat's port/left side,
       * so the clearer bypass direction is starboard/right.
       *
       * Negative bearing means the obstacle is on the boat's
       * starboard/right side, so the clearer bypass direction is port/left.
       *
       * For an obstacle approximately straight ahead, keep starboard as the
       * conservative fallback until a full COLREG/CPA classifier is available.
       */
      constexpr double centreline_deadband_deg = 2.0;

      if (
        collision_obstacle_bearing_deg_ >
        centreline_deadband_deg)
      {
        avoidance_side_ = "starboard";
      }
      else if (
        collision_obstacle_bearing_deg_ <
        -centreline_deadband_deg)
      {
        avoidance_side_ = "port";
      }
      else {
        avoidance_side_ = "starboard";
      }

      avoidance_target_ =
        offsetGeoPointRelativeToBoat(
        current_boat_position_,
        current_boat_heading_,
        avoidance_side_,
        collision_avoidance_offset_m_);

      avoidance_target_ready_ = true;

      RCLCPP_WARN(
        get_logger(),
        "Collision avoidance target generated: "
        "obstacle_id=%u range=%.2f bearing=%.2f side=%s "
        "target=(%.8f, %.8f)",
        collision_obstacle_id_,
        collision_obstacle_range_m_,
        collision_obstacle_bearing_deg_,
        avoidance_side_.c_str(),
        avoidance_target_.latitude,
        avoidance_target_.longitude);

      return BT::NodeStatus::SUCCESS;
    });

  factory_.registerSimpleAction(
    "RequestAvoidance",
    [this](BT::TreeNode &) {
      if (!avoidance_target_ready_) {
        return BT::NodeStatus::FAILURE;
      }

      if (
        collision_obstacle_id_ != 0 &&
        collision_obstacle_id_ == last_avoidance_request_id_ &&
        !requestCooldownExpired(last_avoidance_request_time_))
      {
        return BT::NodeStatus::SUCCESS;
      }

      const bool requested =
        sendBypassRequest(
        avoidance_target_,
        "collision_avoidance_relative_risk");

      if (!requested) {
        return BT::NodeStatus::FAILURE;
      }

      last_avoidance_request_id_ =
        collision_obstacle_id_;

      last_avoidance_request_time_ =
        now();

      return BT::NodeStatus::SUCCESS;
    });

  // -----------------------------------------------------------------------
  // Mission lifecycle
  // -----------------------------------------------------------------------

  BT::NodeBuilder mission_monitor_builder =
    [this](
    const std::string & name,
    const BT::NodeConfig & config)
    {
      return std::make_unique<boat_bt::MissionMonitor>(
        name,
        config,
        [this]() {
          return mission_status_received_;
        },
        [this]() {
          return mission_state_;
        });
    };

  factory_.registerBuilder<boat_bt::MissionMonitor>(
    "MissionMonitor",
    mission_monitor_builder);

  // -----------------------------------------------------------------------
  // Utility actions retained from earlier development/testing
  // -----------------------------------------------------------------------

  factory_.registerSimpleAction(
    "MoveForward",
    [this](BT::TreeNode & node) {
      double speed = 0.5;

      if (auto value = node.getInput<double>("speed")) {
        speed = value.value();
      }

      geometry_msgs::msg::Twist cmd;
      cmd.linear.x = speed;

      cmd_pub_->publish(cmd);

      RCLCPP_INFO(
        get_logger(),
        "MoveForward: speed=%.2f",
        speed);

      return BT::NodeStatus::SUCCESS;
    },
    {
      BT::InputPort<double>("speed")
    });

  factory_.registerSimpleAction(
    "StopBoat",
    [this](BT::TreeNode &) {
      geometry_msgs::msg::Twist cmd;

      cmd_pub_->publish(cmd);

      RCLCPP_INFO(
        get_logger(),
        "StopBoat");

      return BT::NodeStatus::SUCCESS;
    });
}
