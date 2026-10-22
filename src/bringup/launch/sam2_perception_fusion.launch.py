"""Launch the SAM2/Kelp perception+fusion subsystem (ASKET-SPEC-PERCEPTION-
SAM2-FUSION-v1.0). Jetson-only: sam2_vision requires TensorRT + CUDA + the
engines built by sam2_vision/scripts/build_trt_engines.sh.

Differences from the design note's own launch file, and why:

  * `executable='sam2_prompt_node.py'` / `'sam2_inference_node.py'` named
    files, not entry points. ROS 2 ament_python packages expose
    `console_scripts` entry points (see setup.py) with no ".py" suffix —
    launching the literal filename is a different, non-existent executable
    and fails at runtime with "executable not found". Fixed here.
  * Added two things the design note's diagram and prose promise but whose
    launch wiring was never written: placeholder static TF publishers for
    livox_frame/oak_rgb_camera_optical_frame (see
    asket_sensor_fusion/config/extrinsics.yaml — UNCALIBRATED, see that
    file's own warning), and kelp_keepout_costmap_node (without it,
    KeepOutFilter's mask topic receives nothing — see that node's
    docstring).
  * Every node gated behind its own `enable_*` argument, matching this
    repo's existing bringup/launch/njord.launch.py convention, so this can
    be launched standalone for bench testing one node at a time.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    sam2_cfg = get_package_share_directory("sam2_vision") + "/config"
    fusion_cfg = get_package_share_directory("asket_sensor_fusion") + "/config"

    args = [
        DeclareLaunchArgument("enable_water_filter", default_value="true"),
        DeclareLaunchArgument("enable_sam2_prompt", default_value="true"),
        DeclareLaunchArgument("enable_sam2_inference", default_value="true"),
        DeclareLaunchArgument("enable_obstacle_fusion", default_value="true"),
        DeclareLaunchArgument("enable_geo_tracker", default_value="true"),
        DeclareLaunchArgument("enable_kelp_keepout", default_value="true"),
        DeclareLaunchArgument(
            "publish_placeholder_extrinsics", default_value="true",
            description="Publish the UNCALIBRATED static TFs from "
                         "extrinsics.yaml so the pipeline is testable before "
                         "real calibration exists. Set false once a real "
                         "calibration publishes these frames instead.",
        ),
    ]

    nodes = [
        Node(
            package="asket_sensor_fusion",
            executable="lidar_water_filter_node",
            name="lidar_water_filter_node",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_water_filter")),
            parameters=[fusion_cfg + "/fusion_params.yaml"],
        ),
        Node(
            package="sam2_vision",
            executable="sam2_prompt_node",
            name="sam2_prompt_generator_node",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_sam2_prompt")),
            parameters=[sam2_cfg + "/sam2_params.yaml"],
        ),
        Node(
            package="sam2_vision",
            executable="sam2_inference_node",
            name="sam2_inference_node",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_sam2_inference")),
            parameters=[sam2_cfg + "/sam2_params.yaml"],
        ),
        Node(
            package="asket_sensor_fusion",
            executable="sensor_fusion_obstacle_node",
            name="sensor_fusion_obstacle_node",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_obstacle_fusion")),
            parameters=[fusion_cfg + "/fusion_params.yaml"],
        ),
        Node(
            package="asket_sensor_fusion",
            executable="geo_fusion_tracker_node",
            name="geo_fusion_tracker_node",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_geo_tracker")),
            parameters=[fusion_cfg + "/fusion_params.yaml"],
        ),
        Node(
            package="fusion",
            executable="kelp_keepout_costmap_node",
            name="kelp_keepout_costmap_node",
            output="screen",
            condition=IfCondition(LaunchConfiguration("enable_kelp_keepout")),
        ),
    ]

    # Placeholder static TFs, read from extrinsics.yaml so the launch file
    # and the file humans actually edit can't silently drift apart.
    try:
        import yaml

        with open(os.path.join(fusion_cfg, "extrinsics.yaml")) as f:
            extrinsics = yaml.safe_load(f)
        for frame, spec in extrinsics.items():
            x, y, z = spec["xyz"]
            roll, pitch, yaw = spec["rpy"]
            nodes.append(
                Node(
                    package="tf2_ros",
                    executable="static_transform_publisher",
                    name=f"static_{frame}_tf",
                    arguments=[
                        str(x), str(y), str(z), str(yaw), str(pitch), str(roll),
                        "base_link", frame,
                    ],
                    condition=IfCondition(LaunchConfiguration("publish_placeholder_extrinsics")),
                ),
            )
    except (OSError, ImportError, KeyError) as exc:
        # Not fatal: the rest of the pipeline degrades gracefully (see each
        # node's own TF-missing handling) if extrinsics.yaml can't be read.
        print(f"sam2_perception_fusion.launch.py: could not load extrinsics.yaml ({exc}); "
              "no placeholder static TFs will be published.")

    return LaunchDescription(args + nodes)
