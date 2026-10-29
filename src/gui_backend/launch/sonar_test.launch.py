"""Sonar bench test: the GUI and the sonar health bridge, and nothing else.

What this is for
================

The first time the Omniscan 3D is plugged into the Jetson, the question is
narrow: **does the sonar answer, and does the GUI see it?** Everything else on
the boat is noise at that moment, and worse than noise — a Pico that is not
connected, a Pixhawk that is not connected and a Nav2 stack with no map all
produce their own alarms, and an operator then has to work out which red light
is the one that matters.

So this starts four nodes and no others:

* ``gui_backend``     — the interface itself
* ``omniscan_bridge`` — sonar health (is it alive, what ping rate, clock offset)
* ``system_test``     — the pre-flight, which will say NO-GO; see below
* ``mission_recorder``— so a recording can be made if the sonar does work

No ``pico_bridge``, no MAVROS, no Nav2, no lidar, no camera. It is
``gui.launch.py`` with the sonar turned on, which is already the smallest thing
that can show a sonar — this file exists so that is one short command at night
rather than three arguments remembered correctly.

**The pre-flight will report NO-GO, and that is the correct answer.** There is
no Pico, no GNSS fix and no heading, so the vessel genuinely is not fit to go
anywhere. Read the Sonar panel, not the verdict.

The command
===========

The sonar answers on **192.168.2.21** — measured on 2026-10-28, not assumed —
so that is this file's default::

    ros2 launch gui_backend sonar_test.launch.py

For any other address, without editing anything::

    ASKET_SONAR_HOST=192.168.2.30 ros2 launch gui_backend sonar_test.launch.py
    ros2 launch gui_backend sonar_test.launch.py sonar_host:=192.168.2.30

Then open ``http://<jetson>:8090`` from the laptop.

**Expect the Sonar panel to stay silent until the transducer is in water.**
The 2026-10-28 session got as far as a TCP connection on 62312 and no further,
because a dry transducer produces no soundings. The panel saying "sonar
silent" with the link up is the honest report of that state, not a fault in
this launch file.
"""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EnvironmentVariable,
    LaunchConfiguration,
    PathJoinSubstitution,
)


def generate_launch_description():
    return LaunchDescription([
        # The measured address, which is now also the deployed default in
        # omniscan.yaml and gui.launch.py — so this file no longer differs
        # from them, and the bench and the boat agree. ASKET_SONAR_HOST
        # overrides it for a device that has been moved.
        DeclareLaunchArgument(
            "sonar_host",
            default_value=EnvironmentVariable(
                "ASKET_SONAR_HOST", default_value="192.168.2.21"
            ),
            description=(
                "Omniscan address. Default 192.168.2.21, measured on the "
                "hardware on 2026-10-28; ASKET_SONAR_HOST overrides it."
            ),
        ),
        DeclareLaunchArgument(
            "gui_port",
            default_value="8090",
            description="Mission GUI port, as on the boat.",
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                PathJoinSubstitution([
                    get_package_share_directory("gui_backend"), "launch", "gui.launch.py",
                ])
            ),
            launch_arguments={
                "enable_sonar": "true",
                "sonar_host": LaunchConfiguration("sonar_host"),
                "gui_port": LaunchConfiguration("gui_port"),
                # Nothing else is running, so the pre-flight must not expect a
                # Pixhawk or a geofence and report their absence as a surprise.
                # It will still say NO-GO — correctly — on the Pico and the fix.
                "mavros_expected": "false",
                "geofence_expected": "false",
                "lidar_fitted": "false",
                "camera_fitted": "false",
            }.items(),
        ),
    ])
