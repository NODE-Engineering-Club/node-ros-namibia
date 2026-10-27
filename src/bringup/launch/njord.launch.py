import os
import xacro
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, GroupAction, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node, SetParameter
from launch_ros.substitutions import FindPackageShare
from launch_ros.descriptions import ParameterFile
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    cfg = get_package_share_directory("bringup") + "/config"
    # nav2_profile picks the Nav2 parameter file. An unknown profile is a
    # KeyError at launch rather than a silent fallback.
    nav2_params = ParameterFile(
        RewrittenYaml(
            source_file=PythonExpression([
                "{'survey': '", cfg, "/nav2_params_survey.yaml', 'full': '", cfg,
                "/nav2_params.yaml'}['", LaunchConfiguration("nav2_profile"), "']",
            ]),
            param_rewrites={}, convert_types=True,
        ),
        allow_substs=True,
    )
    desc_share = get_package_share_directory("description")
    urdf = xacro.process_file(desc_share + "/urdf/asket.urdf.xacro").toxml()
    # Write URDF next to the meshes/ directory so Gazebo resolves relative mesh paths
    urdf_path = os.path.join(desc_share, "asket.urdf")
    with open(urdf_path, "w") as f:
        f.write(urdf)
    # robot_state_publisher's robot_description parameter is what actually
    # reaches TF/Foxglove/RViz — those tools don't get the "written next to
    # meshes/" trick above (that only works for Gazebo, which loads the file
    # from disk via -file below). They need package:// mesh URIs to resolve
    # anything, or they render TF frames with no hull mesh (bare relative
    # paths like "meshes/hull_visual.obj" carry no location info once this
    # is just a string on the wire). Only this copy is rewritten — the file
    # written above for Gazebo's spawn is untouched, so its already-working
    # resolution isn't at risk.
    urdf_for_ros = urdf.replace('filename="meshes/', 'filename="package://description/meshes/')
    worlds_dir = os.path.join(desc_share, "worlds")

    # fmt: off
    args = [
        DeclareLaunchArgument("world",                default_value="basicWorld.sdf",
                              description="World file name under description/worlds/ to load in Gazebo "
                                          "(e.g. collisionAvoidanceWorld.sdf for buoy + moving-vessel obstacles)"),
        DeclareLaunchArgument("enable_mavros",       default_value="true"),
        DeclareLaunchArgument("enable_localization",  default_value="true"),
        DeclareLaunchArgument("enable_nav2",          default_value="true"),
        # Phase B (f). survey: no obstacle layer (nothing aboard detects
        # obstacles), a geofence keep-out and a speed limit
        # (config/nav2_params_survey.yaml). full: config/nav2_params.yaml, for
        # the SAM2/fusion obstacle pipeline, which needs sensors not fitted.
        DeclareLaunchArgument("nav2_profile",         default_value="survey",
                              description="Nav2 parameters: 'survey' (geofence + speed limit, no "
                                          "obstacle layer) or 'full' (SAM2/fusion obstacles)."),
        DeclareLaunchArgument("geofence_file",        default_value="/data/setup/geofence.yaml",
                              description="Survey-area polygon (lat/lon) for the survey profile. "
                                          "Example: config/geofence.example.yaml."),
        DeclareLaunchArgument("enable_sensors",       default_value="true"),
        DeclareLaunchArgument("enable_perception",    default_value="true"),
        DeclareLaunchArgument("enable_geo_fusion",    default_value="true"),
        DeclareLaunchArgument("enable_control",       default_value="true"),
        DeclareLaunchArgument("enable_mission",       default_value="true"),
        DeclareLaunchArgument("enable_boat_bt",       default_value="true"),
        DeclareLaunchArgument("enable_vision",        default_value="true"),
        DeclareLaunchArgument("vision_confidence",    default_value="0.5"),
        DeclareLaunchArgument("camera_device",        default_value="/dev/video0"),
        DeclareLaunchArgument("lidar_device",         default_value="/dev/ttyUSB0"),
        # Off by default: no lidar (RPLidar/Livox) is aboard and the USB camera
        # was replaced by the OAK-D-LR, which has no driver here yet
        # (docs/HARDWARE_DECISIONS.md). Starting a driver for a device that is
        # not there only produces a crash-looping node and an empty panel.
        DeclareLaunchArgument("enable_lidar",         default_value="false",
                              description="Start the RPLIDAR driver (sllidar_node) on lidar_device. "
                                          "No lidar is fitted on the current boat."),
        DeclareLaunchArgument("enable_usb_camera",    default_value="false",
                              description="Start camera_driver on camera_device (USB/V4L2). "
                                          "The boat now carries an OAK-D-LR (PoE), not a USB camera."),
        # The OAK-D-LR (PoE, 10.10.20.21) through Luxonis' depthai_ros_driver.
        # Off by default: the driver is NOT in the image yet
        # (ros-jazzy-depthai-ros-driver, plus ros-jazzy-compressed-image-
        # transport for the GUI's JPEG topic). See docs/CAMERA_OAK.md.
        DeclareLaunchArgument("enable_oak",           default_value="false",
                              description="Start depthai_ros_driver for the OAK-D-LR (config/oak.yaml). "
                                          "The package must be installed; it is not in the image yet."),
        DeclareLaunchArgument("use_sim",         default_value="false"),
        DeclareLaunchArgument(
            "headless",
            default_value="true",
            description=(
                "Run Gazebo server-only. Set false only when graphical "
                "rendering is known to work."
            ),
        ),
        DeclareLaunchArgument("enable_foxglove",      default_value="true"),
        # Phase B (d): MAVROS straight to the Pixhawk over USB, no BlueOS.
        # /dev/pixhawk is the udev link (deploy/udev/99-asket.rules); the
        # baud rate is ignored on a USB CDC port but MAVROS's URL needs one.
        DeclareLaunchArgument("fcu_url",              default_value="/dev/pixhawk:115200",
                              description="MAVROS link to the flight controller. Ignored with "
                                          "use_blueos:=true."),
        DeclareLaunchArgument("use_blueos",           default_value="false",
                              description="Go through BlueOS's MAVLink router (tcp://localhost:5777) "
                                          "instead of fcu_url, as before Phase B (d)."),
        # PX4 or ArduPilot: not yet known for this Pixhawk 4. Selects
        # config/mavros_<autopilot>.yaml and whether MAVROS asks for stream
        # rates; the pre-flight's autopilot.match check fails if the Pixhawk
        # turns out to run the other one. ArduPilot by default: the
        # set_stream_rate workaround below was confirmed against this
        # Pixhawk, and REQUEST_DATA_STREAM is an ArduPilot-only request.
        DeclareLaunchArgument("autopilot",            default_value="ardupilot",
                              description="Firmware on the Pixhawk: 'ardupilot' or 'px4'."),
        DeclareLaunchArgument("gcs_url",              default_value="udp://@localhost:14556"),
        # Phase B (a): the ESCs are wired to the Pico, so the Pico is the motor
        # path by default. false selects actuator_driver (MAVROS RC override
        # through the Pixhawk) instead; the two never run together
        # (system_test/test/test_motor_path_exclusive.py).
        DeclareLaunchArgument("use_pico_bridge",      default_value="true",
                              description="Drive the thrusters through the Pico (pico_bridge). "
                                          "false = actuator_driver via MAVROS/Pixhawk."),
        # /dev/pico is the udev link (deploy/udev/). /dev/ttyACM0 was the old
        # default: behind the hub that number belongs to whichever device woke
        # first, which can be a BMS bridge.
        DeclareLaunchArgument("pico_port",            default_value="/dev/pico"),
        # pico_bridge drives REAL thrusters. With use_sim:=true it used to start
        # anyway (bench rig: simulated mission, real Pico). Now that the bridge
        # is on by default, a plain simulation must not reach real hardware, so
        # that rig needs this argument on purpose. Boat on a stand, props off.
        DeclareLaunchArgument("pico_bridge_in_sim",   default_value="false",
                              description="Also start pico_bridge when use_sim:=true (bench only: "
                                          "real thrusters follow the simulated mission)."),
        DeclareLaunchArgument("enable_bms",           default_value="false",
                              description="BMS readers (one per pack) + battery_aggregator -> /battery/state"),
        # Stable names from deploy/udev/99-asket.rules (the BMS boards sit
        # behind a USB hub, so ttyACMn numbers are not stable).
        DeclareLaunchArgument("bms_port",             default_value="/dev/bms_pack1",
                              description="Serial port of the pack 1 BMS (MCP2221)"),
        # false while only one BMS is wired (team decision, Phase B). With one
        # reader the aggregator combines one pack; set true once the second
        # BMS is fitted and /dev/bms_pack2 exists.
        DeclareLaunchArgument("enable_bms2",          default_value="false",
                              description="Second BMS (pack 2). Only read when enable_bms:=true. "
                                          "false while only one BMS is wired."),
        DeclareLaunchArgument("bms2_port",            default_value="/dev/bms_pack2",
                              description="Serial port of the pack 2 BMS (MCP2221)"),
        DeclareLaunchArgument("enable_cloud_bridge",  default_value="false",
                              description="Outbound-only cloud telemetry relay (src/comms/cloud_bridge). "
                                          "Off by default — needs cloud_ws_url configured for a real "
                                          "deployment, see config/cloud_bridge_params.yaml."),
        DeclareLaunchArgument("enable_sonar_bridge",  default_value="false",
                              description="SonarLink -> /sonar/decimated bridge (src/comms/cloud_bridge). "
                                          "Independent of enable_cloud_bridge so it can be bench-tested "
                                          "against a real Omniscan without the cloud link up."),
        DeclareLaunchArgument("lidar_camera_extrinsic", default_value="",
                              description="Path to lidar_camera_extrinsic.yaml; "
                                          "empty = use URDF nominal TF for lidar→front_camera"),
        # Mission GUI (ported from node-ros-2026 1606eb7). OFF by default:
        # launching without options behaves exactly as before. See
        # src/gui_backend/launch/gui.launch.py and docs/gui/PORTING_NOTES.md.
        DeclareLaunchArgument("enable_gui",           default_value="false",
                              description="Start the mission GUI backend on http://<host>:<gui_port> "
                                          "(also starts system_test and mission_recorder)"),
        DeclareLaunchArgument("gui_port",             default_value="8090",
                              description="Mission GUI HTTP/WebSocket port. Clear of foxglove_bridge (8765)."),
    ]
    # fmt: on

    sim_time = {"use_sim_time": LaunchConfiguration("use_sim")}

    nodes = [
        # Robot description — publishes TF frames from URDF
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": urdf_for_ros}, sim_time],
        ),
        # MAVROS — FCU bridge
        Node(
            package="mavros",
            executable="mavros_node",
            # No `name=` here — deliberately. Setting it makes launch_ros inject
            # a global `-r __node:=mavros` remap, and mavros's own plugin.cpp
            # creates each plugin's topics on a dynamically-constructed
            # sub-node (`rclcpp::Node::make_shared(subnode, ...)`) that is
            # supposed to be immune to that via `use_global_arguments(false)`
            # (see plugin.cpp's own comment on exactly this hazard). In the
            # mavros build bundled for Jazzy that protection doesn't hold: the
            # global remap leaks into every plugin sub-node, renaming all of
            # them to "mavros" and collapsing every plugin's topics onto the
            # single namespace /mavros/mavros/<leaf> instead of
            # /mavros/<plugin>/<leaf>. Most of the time that only causes
            # cosmetic doubling + harmless QoS-mismatch warnings between
            # plugins that happen to share a leaf name (e.g. local_position's
            # and mocap_pose_estimate's "pose"), but rc_io's "in"/"out" (its
            # actual topic names, not namespaced under "rc" at all once
            # collapsed) collide fatally with a differently-typed entity and
            # SIGABRT the whole node — 100% reproducible, not a startup race.
            # mavros_node's own compiled-in default node name is already
            # "mavros", so dropping the redundant name= keeps every topic
            # this repo depends on (/mavros/state, /mavros/imu/data, ...)
            # unchanged while leaving plugin sub-nodes unremapped and correctly
            # namespaced. Confirmed against the real Pixhawk: connects and
            # stays up indefinitely with no crash once this remap is gone.
            respawn=True,
            respawn_delay=2.0,
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_mavros"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true'"
            ])),
            arguments=[
                "--ros-args",
                "-p", ["fcu_url:=", PythonExpression([
                    "'tcp://localhost:5777' if '", LaunchConfiguration("use_blueos"),
                    "' == 'true' else '", LaunchConfiguration("fcu_url"), "'",
                ])],
                "-p", ["gcs_url:=", LaunchConfiguration("gcs_url")],
                "-p", "tgt_system:=1",
                "-p", "tgt_component:=1",
                "-p", "local_position.frame_id:=odom",
                "-p", "local_position.tf.child_frame_id:=base_link",
                "-p", "local_position.rate:=30.0",
                "--log-level", "mavros:=warn",
                "--log-level", "rcl.logging_rosout:=error",
            ],
            # No sim_time param here: this node's condition already excludes
            # use_sim, and adding it back (even as a single-key dict) forces
            # launch_ros to emit a second --params-file, which alone is enough
            # to reproduce the race described above.
            # One file, chosen by autopilot (see config/mavros_ardupilot.yaml).
            parameters=[
                PythonExpression([
                    "'", cfg, "/mavros_' + {'px4': 'px4', 'ardupilot': 'ardupilot'}['",
                    LaunchConfiguration("autopilot"), "'] + '.yaml'",
                ]),
            ],
        ),
        # This particular FCU/link doesn't auto-stream position or extended-
        # status message groups on connect (RAW_SENSORS group — IMU, raw GPS —
        # comes through fine, but GLOBAL_POSITION_INT/BATTERY_STATUS never do
        # without this) — confirmed against the real Pixhawk: /mavros/battery
        # and /mavros/global_position/raw/fix stay completely silent (no
        # publisher ever calls publish(), not a QoS mismatch) until
        # /mavros/set_stream_rate is called explicitly. Requesting STREAM_ALL
        # once, a few seconds after mavros_node starts (long enough to be past
        # plugin loading and have an FCU connection), fixes it for the rest of
        # the session. This is a workaround for FCU/link stream-rate config,
        # not a bug in this repo's code.
        #
        # ArduPilot only: REQUEST_DATA_STREAM (what set_stream_rate sends) is
        # an ArduPilot message, which PX4 ignores — PX4's rates come from its
        # own MAVLink instance settings (docs/HARDWARE_DECISIONS.md §4c).
        TimerAction(
            period=10.0,
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_mavros"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true' and '",
                LaunchConfiguration("autopilot"), "' == 'ardupilot'"
            ])),
            actions=[
                ExecuteProcess(
                    cmd=[
                        "ros2", "service", "call", "/mavros/set_stream_rate",
                        "mavros_msgs/srv/StreamRate",
                        "{stream_id: 0, message_rate: 10, on_off: true}",
                    ],
                    output="log",
                ),
            ],
        ),
        # Localization — EKF + NavSat transform
        Node(
            package="robot_localization",
            executable="ekf_node",
            name="ekf_node",
            condition=IfCondition(LaunchConfiguration("enable_localization")),
            parameters=[cfg + "/ekf.yaml", sim_time],
        ),
        Node(
            package="robot_localization",
            executable="navsat_transform_node",
            name="navsat_transform_node",
            condition=IfCondition(LaunchConfiguration("enable_localization")),
            remappings=[("gps/fix", "/gps_driver/gps_raw"), ("imu/data", "/imu_driver/imu_raw")],
            parameters=[cfg + "/navsat.yaml", sim_time],
        ),
        # Anchors navsat_transform_node's GPS datum to the FC's own local-position
        # origin, so GPS waypoints (/fromLL) and odom-frame position agree on
        # where (0, 0) is — see sensors/datum_sync.py for why this matters.
        Node(
            package="sensors",
            executable="datum_sync",
            name="datum_sync",
            condition=IfCondition(LaunchConfiguration("enable_localization")),
        ),
        # The geofence keep-out mask for the survey profile, in the map frame
        # anchored by datum_sync (same home position).
        Node(
            package="sensors",
            executable="geofence_mask_node",
            name="geofence_mask_node",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_nav2"), "' == 'true' and '",
                LaunchConfiguration("nav2_profile"), "' == 'survey'"
            ])),
            parameters=[{"geofence_file": LaunchConfiguration("geofence_file")}, sim_time],
        ),
        # Nav2 — individual nodes so we can omit opennav_docking (unsupported on USV)
        GroupAction(
            condition=IfCondition(LaunchConfiguration("enable_nav2")),
            actions=[
                SetParameter("use_sim_time", LaunchConfiguration("use_sim")),
            ] + [
                Node(package=pkg, executable=exe, name=name, output="screen",
                     parameters=[nav2_params],
                     remappings=[("/tf", "tf"), ("/tf_static", "tf_static")] + extra_remaps)
                for pkg, exe, name, extra_remaps in [
                    # controller_server/behavior_server's raw output must not
                    # land on the shared cmd_vel name: it's pre-smoothing,
                    # pre-collision-check, and (once twist_mux is added below)
                    # would otherwise also collide with the final arbitrated
                    # /cmd_vel. Route it into velocity_smoother instead.
                    ("nav2_controller",      "controller_server",  "controller_server", [("cmd_vel", "cmd_vel_nav")]),
                    ("nav2_smoother",        "smoother_server",    "smoother_server",   []),
                    ("nav2_planner",         "planner_server",     "planner_server",    []),
                    ("nav2_route",           "route_server",       "route_server",      []),
                    ("nav2_behaviors",       "behavior_server",    "behavior_server",   [("cmd_vel", "cmd_vel_nav")]),
                    ("nav2_bt_navigator",    "bt_navigator",       "bt_navigator",       []),
                    ("nav2_waypoint_follower","waypoint_follower",  "waypoint_follower", []),
                    ("nav2_velocity_smoother","velocity_smoother", "velocity_smoother",  [("cmd_vel", "cmd_vel_nav")]),
                    ("nav2_collision_monitor","collision_monitor",  "collision_monitor",  []),
                ]
            ] + [
                Node(
                    package="nav2_lifecycle_manager",
                    executable="lifecycle_manager",
                    name="lifecycle_manager_navigation",
                    output="screen",
                    parameters=[{
                        "autostart": True,
                        "node_names": [
                            "controller_server", "smoother_server", "planner_server",
                            "route_server", "behavior_server", "velocity_smoother",
                            "collision_monitor", "bt_navigator", "waypoint_follower",
                        ],
                    }],
                ),
            ],
        ),
        # Sensors
        Node(
            package="sensors",
            executable="camera_driver",
            name="camera_driver",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_sensors"), "' == 'true' and '",
                LaunchConfiguration("enable_usb_camera"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true'"
            ])),
            parameters=[{
                "device":          LaunchConfiguration("camera_device"),
                "frame_id":        "front_camera",
                "topic":           "/front_camera_driver/image_raw",
                "camera_info_url": "package://bringup/config/front_camera.yaml",
            }, sim_time],
        ),
        # OAK-D-LR. FindPackageShare is resolved only when the include runs,
        # so with enable_oak:=false a missing depthai_ros_driver costs nothing.
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                PathJoinSubstitution(
                    [FindPackageShare("depthai_ros_driver"), "launch", "camera.launch.py"]
                )
            ),
            launch_arguments={
                "name": "oak",
                "params_file": PathJoinSubstitution(
                    [FindPackageShare("bringup"), "config", "oak.yaml"]
                ),
            }.items(),
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_oak"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true'"
            ])),
        ),
        # RPLIDAR S2M1-R2L via Slamtec's official driver (vendored as the
        # src/sllidar_ros2 submodule). The A-series custom driver that used to
        # live here (sensors/lidar_driver) spoke the old 115200-baud/PWM-motor
        # protocol and doesn't apply to the S2's 1 Mbps express-scan protocol.
        Node(
            package="sllidar_ros2",
            executable="sllidar_node",
            name="lidar_driver",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_sensors"), "' == 'true' and '",
                LaunchConfiguration("enable_lidar"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true'"
            ])),
            remappings=[("scan", "/lidar_driver/scan_raw")],
            parameters=[{
                "channel_type":      "serial",
                "serial_port":       LaunchConfiguration("lidar_device"),
                "serial_baudrate":   1000000,
                "frame_id":          "lidar",
                "inverted":          False,
                "angle_compensate":  True,
                "scan_mode":         "DenseBoost",
            }, sim_time],
        ),
        Node(
            package="sensors",
            executable="imu_gps_driver",
            name="imu_gps_driver",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_sensors"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true'"
            ])),
            parameters=[sim_time],
        ),
        # Perception
        Node(
            package="perception",
            executable="lidar_obstacle_node",
            name="lidar_obstacle_node",
            condition=IfCondition(LaunchConfiguration("enable_perception")),
            parameters=[sim_time],
        ),
        # Calibrated LiDAR→camera TF (only when extrinsic YAML is provided)
        Node(
            package="calibration",
            executable="extrinsic_tf_publisher",
            name="lidar_camera_extrinsic_tf",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("lidar_camera_extrinsic"), "' != ''"
            ])),
            parameters=[{
                "extrinsic_yaml": LaunchConfiguration("lidar_camera_extrinsic"),
                "parent_frame":   "lidar",
                "child_frame":    "front_camera_cal",
            }],
        ),
        Node(
            package="perception",
            executable="fusion_node",
            name="fusion_node",
            condition=IfCondition(LaunchConfiguration("enable_perception")),
            parameters=[{
                "lidar_frame":  "lidar",
                "camera_frame": PythonExpression([
                    "'front_camera_cal' if '",
                    LaunchConfiguration("lidar_camera_extrinsic"),
                    "' != '' else 'front_camera'",
                ]),
            }, sim_time],
        ),
        # Geo-referenced fusion — labelled obstacles in the global GPS frame on
        # /obstacles/global (runs alongside fusion_node for comparison).
        Node(
            package="fusion",
            executable="geo_fusion_node",
            name="geo_fusion_node",
            condition=IfCondition(LaunchConfiguration("enable_geo_fusion")),
            parameters=[{
                "lidar_frame": "lidar",
                "base_frame":  "base_link",
                "map_frame":   "map",
                "camera_frame": PythonExpression([
                    "'front_camera_cal' if '",
                    LaunchConfiguration("lidar_camera_extrinsic"),
                    "' != '' else 'front_camera'",
                ]),
            }, sim_time],
        ),
        # Control
        Node(
            package="control",
            executable="nav_to_pid",
            name="nav_to_pid",
            condition=IfCondition(LaunchConfiguration("enable_control")),
            parameters=[sim_time],
        ),
        Node(
            package="control",
            executable="pid_controller",
            name="pid_controller",
            condition=IfCondition(LaunchConfiguration("enable_control")),
            parameters=[sim_time],
        ),
        Node(
            package="control",
            executable="actuator_driver",
            name="actuator_driver",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_control"), "' == 'true' and '",
                LaunchConfiguration("use_sim"), "' != 'true' and '",
                LaunchConfiguration("use_pico_bridge"), "' != 'true'"
            ])),
            parameters=[sim_time],
        ),
        # Arbitrates between Nav2's own /cmd_vel output (via collision_monitor,
        # remapped to nav2/cmd_vel) and boat_bt's direct commands
        # (boat_bt/cmd_vel). Without it the two race on the shared /cmd_vel that
        # nav_to_pid/ros_gz_bridge consume, because Nav2's pipeline keeps
        # publishing (e.g. collision_monitor's safety-stop heartbeat) even with
        # no active goal. See bringup/config/twist_mux.yaml for priorities.
        Node(
            package="twist_mux",
            executable="twist_mux",
            name="twist_mux",
            remappings=[("/cmd_vel_out", "/cmd_vel")],
            parameters=[
                cfg + "/twist_mux.yaml",
                sim_time,
            ],
        ),

        # Pico bridge — alternative actuation path: motor commands over serial
        # to a Raspberry Pi Pico, bypassing MAVROS/the Pixhawk for motor control.
        # mavros is still used for GPS/IMU sensing in this mode.
        #
        # Allowed to run with use_sim:=true only when pico_bridge_in_sim:=true
        # is also given (unlike actuator_driver above, which stays
        # sim-excluded since it targets mavros/the Pixhawk): this lets a bench
        # test drive REAL motors via
        # the real Pico off of Gazebo-simulated perception/mission logic —
        # the boat physically can't move (elevated/on a stand), but the
        # thrusters WILL spin in response to real /control/effort commands.
        # Never enable this combination with the boat in the water.
        Node(
            package="control",
            executable="pico_bridge",
            name="pico_bridge",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_control"), "' == 'true' and '",
                LaunchConfiguration("use_pico_bridge"), "' == 'true' and ('",
                LaunchConfiguration("use_sim"), "' != 'true' or '",
                LaunchConfiguration("pico_bridge_in_sim"), "' == 'true')"
            ])),
            parameters=[{"port": LaunchConfiguration("pico_port")}, sim_time],
        ),
        # BMS readers — each the sole owner of the serial link to one battery
        # management system (MCP2221 USB-UART bridge). Each publishes
        # sensor_msgs/BatteryState with an estimated state of charge and the
        # full raw report (per-cell voltages, FET states) on its own topics.
        #
        # Deliberately allowed to run even when use_sim:=true, same reasoning
        # as pico_bridge above: real battery telemetry has nothing to do with
        # whether the boat's motion is simulated, so a hybrid bench test (real
        # hardware, Gazebo-simulated perception/mission) should still surface
        # the real pack's voltage/cell balance.
        #
        # Two packs in parallel, one BMS each (docs/HARDWARE_DECISIONS.md): one
        # reader per BMS on its own topic, and battery_aggregator combining
        # them into the single /battery/state everything else reads. Only one
        # BMS is wired today, so enable_bms2 defaults to false; the aggregator
        # also leaves out (and names in /diagnostics) a pack that stops
        # reporting.
        Node(
            package="sensors",
            executable="bms_reader",
            name="bms_reader_pack1",
            condition=IfCondition(LaunchConfiguration("enable_bms")),
            parameters=[{
                "port": LaunchConfiguration("bms_port"),
                "pack_name": "pack1",
                "state_topic": "/battery/pack1",
                "raw_topic": "/battery/pack1/status_raw",
            }, sim_time],
        ),
        Node(
            package="sensors",
            executable="bms_reader",
            name="bms_reader_pack2",
            condition=IfCondition(PythonExpression([
                "'", LaunchConfiguration("enable_bms"), "' == 'true' and '",
                LaunchConfiguration("enable_bms2"), "' == 'true'"
            ])),
            parameters=[{
                "port": LaunchConfiguration("bms2_port"),
                "pack_name": "pack2",
                "state_topic": "/battery/pack2",
                "raw_topic": "/battery/pack2/status_raw",
            }, sim_time],
        ),
        Node(
            package="sensors",
            executable="battery_aggregator",
            name="battery_aggregator",
            condition=IfCondition(LaunchConfiguration("enable_bms")),
            parameters=[{
                "pack_topics": PythonExpression([
                    "['/battery/pack1', '/battery/pack2'] if '",
                    LaunchConfiguration("enable_bms2"),
                    "' == 'true' else ['/battery/pack1']",
                ]),
                "output_topic": "/battery/state",
            }, sim_time],
        ),
        # Cloud telemetry relay — outbound-only, never touches MAVROS/
        # ArduPilot control (see src/comms/cloud_bridge's module docstring
        # for the fixes applied relative to the original design note,
        # notably: the default cloud_ws_url must carry ?role=asv or the
        # paired Cloudflare Worker treats the boat as a browser listener
        # and relays nothing).
        Node(
            package="cloud_bridge",
            executable="cloud_telemetry_node",
            name="cloud_telemetry_bridge_node",
            condition=IfCondition(LaunchConfiguration("enable_cloud_bridge")),
            parameters=[
                get_package_share_directory("cloud_bridge") + "/config/cloud_bridge_params.yaml",
                sim_time,
            ],
            respawn=True,
            respawn_delay=2.0,
            output="screen",
        ),
        # SonarLink -> /sonar/decimated. Independently enabled: useful to
        # bench-test against a real Omniscan without the cloud link up, and
        # cloud_telemetry_node degrades gracefully (omits "sonar" from the
        # payload) if this isn't running.
        Node(
            package="cloud_bridge",
            executable="sonar_bridge_node",
            name="sonar_bridge_node",
            condition=IfCondition(LaunchConfiguration("enable_sonar_bridge")),
            parameters=[
                get_package_share_directory("cloud_bridge") + "/config/cloud_bridge_params.yaml",
                sim_time,
            ],
            respawn=True,
            respawn_delay=2.0,
            output="screen",
        ),
        # Mission
        #
        # respawn=True: mission_manager holds no state that survives a crash
        # (waypoint list / progress index are plain in-memory attributes), so
        # a respawn always restarts a *new* mission at waypoint 0 unless the
        # caller resends a /mission/start whose waypoints match an on-disk
        # checkpoint from a previous run (see mission_manager.py) — respawn
        # only guarantees the service comes back, not that progress survives.
        Node(
            package="mission",
            executable="mission_manager",
            name="mission_manager",
            condition=IfCondition(LaunchConfiguration("enable_mission")),
            parameters=[sim_time],
            respawn=True,
            respawn_delay=2.0,
            output="screen",
        ),

        # Safety Behavior Tree (GlobalSafety obstacle bypass + MissionMonitor).
        #
        # respawn=True: boat_bt holds only transient per-tick state (latest
        # obstacle snapshot, last bypass request), so a respawn simply resumes
        # the reflex on the next /obstacles/global message.
        TimerAction(
            period=3.0,
            actions=[
                Node(
                    package="boat_bt",
                    executable="boat_bt_node",
                    name="boat_bt",
                    condition=IfCondition(
                        LaunchConfiguration("enable_boat_bt")
                    ),
                    respawn=True,
                    respawn_delay=2.0,
                    parameters=[
                        sim_time,
                        # Buoy class ids for the currently loaded model
                        # (yolo26n-seg-navier.onnx, embedded names dict:
                        # {0: 'green', 1: 'red', ...}). Update these when the
                        # model is retrained for Kelp farm demarcation buoys.
                        {
                            "buoy_green_class_id": "0",
                            "buoy_red_class_id": "1",
                            "buoy_min_standoff_m": 1.0,
                        },
                    ],
                    output="screen",
                ),
            ],
        ),

        # Vision
        Node(
            package="vision",
            executable="vision_node",
            name="vision_node",
            condition=IfCondition(LaunchConfiguration("enable_vision")),
            parameters=[
                {"confidence": LaunchConfiguration("vision_confidence")},
                sim_time,
            ],
        ),
        # Telemetry — rosbridge WebSocket (port 9090) + MJPEG video server (port 8080)
        # Node(
        #     package="rosbridge_server",
        #     executable="rosbridge_websocket",
        #     name="rosbridge_websocket",
        #     parameters=[{"port": 9090}, sim_time],
        # ),
        # Node(
        #     package="web_video_server",
        #     executable="web_video_server",
        #     name="web_video_server",
        #     parameters=[{"port": 8080}, sim_time],
        # ),
        Node(
            package="foxglove_bridge",
            executable="foxglove_bridge",
            name="foxglove_bridge",
            condition=IfCondition(LaunchConfiguration("enable_foxglove")),
        ),
        # Static map→odom identity TF.
        # ArduPilot's onboard EKF (or Gazebo in sim) gives us a globally-anchored
        # odom frame — odom origin == GPS home / Gazebo world origin. So map and
        # odom are coincident and we publish identity. /fromLL still works because
        # navsat_transform_node establishes its own datum from the first GPS fix.
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="static_map_odom_tf",
            arguments=["0", "0", "0", "0", "0", "0", "map", "odom"],
        ),
        # Sim-only: Gazebo names sensor frames with the full scoped model path
        # (e.g. "asket/base_link/Lidar_sensor") while the TF tree only has the URDF link "lidar".
        # This static identity TF bridges the gap so collision_monitor can look up the transform.
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="static_lidar_sensor_tf",
            arguments=["0", "0", "0", "0", "0", "0", "lidar", "asket/base_link/Lidar_sensor"],
            condition=IfCondition(LaunchConfiguration("use_sim")),
        ),
        # Sim-only: Gazebo publishes NavSatFix with the scoped sensor frame
        # "asket/base_link/GPS_sensor", while robot_state_publisher exposes
        # the URDF GPS link as "GPS". Publish the real URDF GPS offset under
        # the scoped Gazebo sensor name so navsat_transform_node can transform
        # GPS measurements into base_link and initialize /fromLL and /toLL.
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="static_gps_sensor_tf",
            arguments=[
                "-0.18827", "0", "0.174775",
                "0", "0", "0",
                "base_link",
                "asket/base_link/GPS_sensor",
            ],
            condition=IfCondition(LaunchConfiguration("use_sim")),
        ),
        Node(
            package="ros_gz_bridge",
            executable="parameter_bridge",
            name="ros_gz_bridge",
            output="screen",
            parameters=[{"config_file": cfg + "/gz_bridge.yaml"}],
            condition=IfCondition(LaunchConfiguration("use_sim")),
        ),
        # Gazebo simulation — server-only by default.
        #
        # Do not infer GUI availability from DISPLAY alone. Dev containers
        # may expose DISPLAY through Xvfb or VS Code while having no usable
        # GPU/rendering device, causing graphical Gazebo to exit immediately.
        ExecuteProcess(
            cmd=[
                "gz",
                "sim",
                "-s",
                "-r",
                PathJoinSubstitution(
                    [worlds_dir, LaunchConfiguration("world")]
                ),
            ],
            output="screen",
            condition=IfCondition(
                PythonExpression([
                    "'", LaunchConfiguration("use_sim"),
                    "' == 'true' and '",
                    LaunchConfiguration("headless"),
                    "' == 'true'",
                ])
            ),
        ),
        ExecuteProcess(
            cmd=[
                "gz",
                "sim",
                "-r",
                PathJoinSubstitution(
                    [worlds_dir, LaunchConfiguration("world")]
                ),
            ],
            output="screen",
            condition=IfCondition(
                PythonExpression([
                    "'", LaunchConfiguration("use_sim"),
                    "' == 'true' and '",
                    LaunchConfiguration("headless"),
                    "' != 'true'",
                ])
            ),
        ),
        # Spawn robot — delayed to allow Gazebo to finish loading the world
        TimerAction(
            period=5.0,
            actions=[
                ExecuteProcess(
                    cmd=[
                        "ros2", "run", "ros_gz_sim", "create",
                        "-world", "default",
                        "-file", urdf_path,
                        "-name", "asket",
                        "-x", "0", "-y", "0", "-z", "0.1",
                    ],
                    output="screen",
                ),
            ],
            condition=IfCondition(LaunchConfiguration("use_sim")),
        ),
        # Mission GUI — its own launch file, included rather than inlined, so it
        # stays runnable on its own (ros2 launch gui_backend gui.launch.py).
        # It is a separate process from pico_bridge: nothing in the GUI can
        # block or starve the Pico's serial heartbeat. Only included when
        # enable_gui:=true. The sonar bridge it can start (omniscan_bridge) is
        # off unless enable_sonar:=true is also given.
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                PathJoinSubstitution(
                    [FindPackageShare("gui_backend"), "launch", "gui.launch.py"]
                )
            ),
            launch_arguments={
                "gui_port": LaunchConfiguration("gui_port"),
                "use_sim": LaunchConfiguration("use_sim"),
                # The GUI shows the lidar and camera panels as "not fitted"
                # rather than empty when their driver is not started.
                "lidar_fitted": LaunchConfiguration("enable_lidar"),
                # The panel's picture is the OAK's compressed RGB topic
                # (topics.yaml camera_image); the USB driver publishes raw
                # images only, so it fills the panel only once a compressed
                # republisher is added.
                "camera_fitted": PythonExpression([
                    "'true' if '", LaunchConfiguration("enable_oak"), "' == 'true' or '",
                    LaunchConfiguration("enable_usb_camera"), "' == 'true' else 'false'",
                ]),
                "geofence_file": LaunchConfiguration("geofence_file"),
                "mavros_expected": PythonExpression([
                    "'true' if '", LaunchConfiguration("enable_mavros"), "' == 'true' and '",
                    LaunchConfiguration("use_sim"), "' != 'true' else 'false'",
                ]),
                "geofence_expected": PythonExpression([
                    "'true' if '", LaunchConfiguration("enable_nav2"), "' == 'true' and '",
                    LaunchConfiguration("nav2_profile"), "' == 'survey' else 'false'",
                ]),
            }.items(),
            condition=IfCondition(LaunchConfiguration("enable_gui")),
        ),
    ]

    return LaunchDescription(args + nodes)
