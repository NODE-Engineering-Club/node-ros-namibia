"""Omniscan 3D -> ROS 2 -> RViz, for a bench with nothing else connected.

    ros2 launch omniscan_bridge sonar_rviz.launch.py

Listen-only: this file NEVER tells the sonar to ping. With the transducer in
water, a person starts it with:

    ros2 service call /omniscan_bridge/start_pinging std_srvs/srv/Trigger

and stops it with ``/omniscan_bridge/stop_pinging``.

Against asket_sim's fake sonar, which is UDP:

    python3 -m asket_sim.core.fake_sonar_server --port 62312 &
    ros2 launch omniscan_bridge sonar_rviz.launch.py \
        sonar_host:=127.0.0.1 sonar_transport:=udp

Differences from the boat's configuration, all because this is a bench view:

* ``publish_all_points`` is on. The boat publishes seabed detections only, and
  a bucket has no seabed, so that filter could leave RViz empty on good data.
* The mounting comes from ``mounting.yaml`` alone, ignoring the Setup page, and
  vessel attitude is off because there is no flight controller. Positions in
  RViz are therefore provisional: do not read them as real geometry.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    share = get_package_share_directory("omniscan_bridge")

    return LaunchDescription([
        DeclareLaunchArgument(
            "sonar_host",
            default_value=EnvironmentVariable(
                "ASKET_SONAR_HOST", default_value="192.168.2.21"
            ),
            description="Omniscan address; ASKET_SONAR_HOST overrides the default.",
        ),
        DeclareLaunchArgument(
            "sonar_transport",
            default_value="tcp",
            description="tcp (measured) or udp (asket_sim's fake sonar only).",
        ),
        DeclareLaunchArgument(
            "publish_all_points",
            default_value="true",
            description="Publish every sonar point, not only seabed detections.",
        ),
        DeclareLaunchArgument(
            "rviz",
            default_value="true",
            description="Start RViz; false runs the bridge alone.",
        ),
        Node(
            package="omniscan_bridge",
            executable="omniscan_bridge_node",
            name="omniscan_bridge",
            output="screen",
            parameters=[
                os.path.join(share, "config", "omniscan.yaml"),
                {
                    "host": LaunchConfiguration("sonar_host"),
                    "transport": LaunchConfiguration("sonar_transport"),
                    "publish_all_points": ParameterValue(
                        LaunchConfiguration("publish_all_points"), value_type=bool
                    ),
                    "mounting_path": os.path.join(share, "config", "mounting.yaml"),
                    "setup_path": "none",
                    "use_vessel_attitude": False,
                },
            ],
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            arguments=["-d", os.path.join(share, "rviz", "sonar.rviz")],
            condition=IfCondition(LaunchConfiguration("rviz")),
        ),
    ])
