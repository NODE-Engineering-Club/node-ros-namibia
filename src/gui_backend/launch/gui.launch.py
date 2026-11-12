"""The mission GUI, on its own.

Includable from ``njord.launch.py`` behind ``enable_gui`` (off by default), and
runnable alone:

    ros2 launch gui_backend gui.launch.py

Independent on purpose. The GUI is a **separate process** from ``pico_bridge``
and must stay one: that node owns the serial link and runs a 20 Hz heartbeat,
and 600 ms of silence drops the Pico to MANUAL on its own. Nothing the GUI does
— a slow client, a large export, a stalled WebSocket — may starve it.
"""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import (
    EnvironmentVariable,
    LaunchConfiguration,
    PathJoinSubstitution,
)
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
import os


def generate_launch_description():
    gui_share = get_package_share_directory("gui_backend")

    topics_config = PathJoinSubstitution([gui_share, "config", "topics.yaml"])
    link_config = PathJoinSubstitution([gui_share, "config", "link_profiles.yaml"])
    static_dir = os.path.join(gui_share, "static")

    args = [
        DeclareLaunchArgument(
            "gui_port",
            default_value="8090",
            description=(
                "HTTP/WebSocket port. Clear of foxglove_bridge (8765), "
                "rosbridge (9090) and web_video_server (8080)."
            ),
        ),
        DeclareLaunchArgument(
            "gui_host",
            default_value="0.0.0.0",
            description=(
                "Bind address. 0.0.0.0 so the laptop ashore can reach it; "
                "127.0.0.1 makes the GUI unreachable, which defeats the point."
            ),
        ),
        DeclareLaunchArgument(
            "tiles_path",
            default_value="/data/maps/survey.mbtiles",
            description=(
                "Offline basemap file. Now a fallback: the map prefers the "
                "cache and then the internet. Missing is survivable."
            ),
        ),
        DeclareLaunchArgument(
            "tile_cache_path",
            default_value="/data/maps/tile-cache.sqlite",
            description=(
                "Where downloaded map tiles are kept, so the map keeps working "
                "when the link drops. One file — copy it between machines or "
                "keep it between deployments."
            ),
        ),
        DeclareLaunchArgument(
            "online_tiles",
            default_value="true",
            description=(
                "Fetch map tiles from the internet (OpenFreeMap, OpenSeaMap; "
                "keyless). false restores the offline-only behaviour."
            ),
        ),
        DeclareLaunchArgument("use_sim", default_value="false"),
        # Whether the boat carries the sensor. False shows the panel as "not
        # fitted" instead of an empty plot or a blank picture, which would
        # read as "clear water" / "camera broken". njord.launch.py passes
        # enable_lidar / enable_usb_camera through.
        DeclareLaunchArgument(
            "lidar_fitted",
            default_value="false",
            description="A lidar is aboard and its driver is running.",
        ),
        DeclareLaunchArgument(
            "geofence_expected",
            default_value="false",
            description="The Nav2 survey profile runs, so the pre-flight requires its geofence.",
        ),
        DeclareLaunchArgument(
            "min_disk_free_gb",
            default_value="10.0",
            description="Pre-flight NO-GO below this much free space on /data.",
        ),
        DeclareLaunchArgument(
            "autopilot",
            default_value="ardupilot",
            description="The Pixhawk's firmware the stack was started for (njord.launch.py autopilot).",
        ),
        DeclareLaunchArgument(
            "mavros_expected",
            default_value="false",
            description="MAVROS is started, so the pre-flight expects its node.",
        ),
        DeclareLaunchArgument(
            "geofence_file",
            default_value="/data/setup/geofence.yaml",
            description="The geofence polygon drawn on the map (the file Nav2's keep-out is built from).",
        ),
        DeclareLaunchArgument(
            "camera_fitted",
            default_value="false",
            description="A camera the GUI can show is aboard and running.",
        ),
        DeclareLaunchArgument(
            "enable_sonar",
            default_value="false",
            description=(
                "Start omniscan_bridge. Off by default: the Omniscan is survey "
                "hardware and is not on the boat for competition work."
            ),
        ),
        DeclareLaunchArgument(
            "sonar_host",
            # Measured on the hardware on 2026-10-28: the sonar answers on
            # 192.168.2.21 and nowhere else. The boat LAN plan
            # (10.10.20.0/24, docs/HARDWARE_DECISIONS.md §2) is unchanged and
            # the sonar has not been moved onto it — the two conflict, and that
            # is a network decision nobody has taken.
            default_value=EnvironmentVariable(
                "ASKET_SONAR_HOST", default_value="192.168.2.21"
            ),
            description=(
                "Omniscan address. Default 192.168.2.21, where the sonar was "
                "measured; ASKET_SONAR_HOST overrides it. The boat LAN plan "
                "says 10.10.20.31, which the sonar is not on "
                "(docs/HARDWARE_DECISIONS.md §2)."
            ),
        ),
        DeclareLaunchArgument(
            "sonar_transport",
            default_value="tcp",
            description=(
                "tcp (measured, the deployed default) or udp. Pass udp only to "
                "run against asket_sim's fake sonar, which is a UDP socket. "
                "Editing omniscan.yaml for a bench test is how a bench setting "
                "reaches the boat."
            ),
        ),
    ]

    sim_time = {"use_sim_time": LaunchConfiguration("use_sim")}

    nodes = [
        Node(
            package="gui_backend",
            executable="gui_backend_node",
            name="gui_backend",
            output="screen",
            parameters=[
                {
                    "host": LaunchConfiguration("gui_host"),
                    "port": LaunchConfiguration("gui_port"),
                    "topics_config": topics_config,
                    "link_profiles_config": link_config,
                    "tiles_path": LaunchConfiguration("tiles_path"),
                    "tile_cache_path": LaunchConfiguration("tile_cache_path"),
                    "online_tiles": LaunchConfiguration("online_tiles"),
                    "static_dir": static_dir,
                    "lidar_fitted": LaunchConfiguration("lidar_fitted"),
                    "camera_fitted": LaunchConfiguration("camera_fitted"),
                    "geofence_file": LaunchConfiguration("geofence_file"),
                    "autopilot": LaunchConfiguration("autopilot"),
                },
                sim_time,
            ],
        ),
        Node(
            package="system_test",
            executable="system_test_node",
            name="system_test",
            output="screen",
            parameters=[
                {
                    "mounting_path": PathJoinSubstitution(
                        [get_package_share_directory("omniscan_bridge"),
                         "config", "mounting.yaml"]
                    ),
                    # The competition stack's own nodes, so a missing one is
                    # reported rather than silently absent from the verdict.
                    "expected_nodes": ["pico_bridge", "gui_backend", "mission_recorder"],
                    # Plus mavros when it is started, and the Nav2 survey
                    # nodes when geofence_expected (system_test_node adds them).
                    "mavros_expected": LaunchConfiguration("mavros_expected"),
                    "autopilot_expected": LaunchConfiguration("autopilot"),
                    "min_disk_free_gb": ParameterValue(
                        LaunchConfiguration("min_disk_free_gb"), value_type=float),
                    # Equipment absent on purpose is "not checked", not a
                    # NO-GO: no lidar unless lidar_fitted, and the sonar is
                    # watched only when omniscan_bridge is started.
                    "lidar_fitted": LaunchConfiguration("lidar_fitted"),
                    "sonar_monitored": LaunchConfiguration("enable_sonar"),
                    "geofence_expected": LaunchConfiguration("geofence_expected"),
                },
                sim_time,
            ],
        ),
        Node(
            package="mission_recorder",
            executable="mission_recorder_node",
            name="mission_recorder",
            output="screen",
            parameters=[
                PathJoinSubstitution(
                    [get_package_share_directory("mission_recorder"),
                     "config", "recorder.yaml"]
                ),
                sim_time,
            ],
        ),
        Node(
            package="omniscan_bridge",
            executable="omniscan_bridge_node",
            name="omniscan_bridge",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_sonar")),
            parameters=[
                PathJoinSubstitution(
                    [get_package_share_directory("omniscan_bridge"),
                     "config", "omniscan.yaml"]
                ),
                {
                    "host": LaunchConfiguration("sonar_host"),
                    "transport": LaunchConfiguration("sonar_transport"),
                    "mounting_path": PathJoinSubstitution(
                        [get_package_share_directory("omniscan_bridge"),
                         "config", "mounting.yaml"]
                    ),
                },
                sim_time,
            ],
        ),
    ]

    return LaunchDescription(args + nodes)
