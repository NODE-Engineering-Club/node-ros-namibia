"""Phase B (d): MAVROS straight to the Pixhawk, PX4 or ArduPilot by argument.

Read from the launch file and the two parameter files, without ROS. The real
launch (both autopilots, a fake heartbeat) is run in the ROS image; see the
PR description.
"""

import re
from pathlib import Path

import yaml

BRINGUP = Path(__file__).resolve().parents[2] / "bringup"
NJORD = (BRINGUP / "launch" / "njord.launch.py").read_text()


def _default(arg: str) -> str:
    match = re.search(rf'DeclareLaunchArgument\("{arg}",\s+default_value="([^"]*)"', NJORD)
    assert match, f"{arg} is not declared"
    return match.group(1)


def test_mavros_opens_the_pixhawk_by_default_not_blueos():
    assert _default("fcu_url") == "/dev/pixhawk:115200"
    assert _default("use_blueos") == "false"
    # BlueOS's router is still one argument away.
    assert "'tcp://localhost:5777' if '" in NJORD


def test_the_autopilot_is_an_argument():
    assert _default("autopilot") == "ardupilot"


def test_one_parameter_file_per_autopilot_with_the_same_denylist():
    files = {name: yaml.safe_load((BRINGUP / "config" / f"mavros_{name}.yaml").read_text())
             for name in ("ardupilot", "px4")}
    deny = {name: set(doc["/**"]["ros__parameters"]["plugin_denylist"])
            for name, doc in files.items()}
    assert deny["px4"] - deny["ardupilot"] == {"rangefinder"}
    for doc in files.values():
        assert doc["/**/time"]["ros__parameters"]["time_ref_source"] == "fcu"
    assert not (BRINGUP / "config" / "mavros_denylist.yaml").exists()


def test_stream_rates_are_requested_from_ardupilot_only():
    timer = NJORD.split("TimerAction(", 1)[1].split("actions=[", 1)[0]
    assert 'LaunchConfiguration("autopilot"), "\' == \'ardupilot\'"' in timer


def test_the_qgroundcontrol_relay_is_off_by_default():
    assert _default("enable_gcs_relay") == "false"
    assert _default("gcs_url") == "udp-b://@14550"
    gcs = NJORD.split('"-p", ["gcs_url:=",', 1)[1].split("])],", 1)[0]
    assert 'LaunchConfiguration("enable_gcs_relay")' in gcs
    assert "else ''" in gcs
