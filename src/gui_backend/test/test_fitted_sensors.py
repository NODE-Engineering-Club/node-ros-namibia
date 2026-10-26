"""A sensor the boat does not carry is "not fitted", never an empty reading.

The boat has no lidar and no USB camera (docs/HARDWARE_DECISIONS.md). An
obstacle panel fed by nothing says "nothing in range", which is a claim about
the water the vessel cannot make. The backend therefore tells the client which
sensors are aboard, and serves nothing for those that are not.

No ROS here: the methods under test touch only ``self.config`` and
``self.latest``.
"""

from pathlib import Path

import yaml
from gui_backend.core.ros_source import RosSource

PACKAGE = Path(__file__).resolve().parents[1]
GUI_SRC = PACKAGE.parent / "asket_gui" / "src"


def _source(config: dict) -> RosSource:
    source = object.__new__(RosSource)
    source.config = config
    source.latest = {}
    return source


def test_shipped_config_says_no_lidar_and_no_camera():
    config = yaml.safe_load((PACKAGE / "config" / "topics.yaml").read_text())
    assert config["fitted"] == {"lidar": False, "camera": False}
    assert _source(config).describe()["fitted"] == {"lidar": False, "camera": False}


def test_a_config_without_the_section_keeps_every_panel():
    assert _source({}).describe()["fitted"] == {"lidar": True, "camera": True}


def test_no_lidar_sample_when_the_lidar_is_not_fitted():
    source = _source({"fitted": {"lidar": False}})
    assert source.snapshot("lidar", "full") is None


def test_the_cockpit_reads_fitted_and_withdraws_the_streams():
    app = (GUI_SRC / "App.jsx").read_text()
    assert "isFitted(state, 'lidar')" in app
    assert "isFitted(state, 'camera')" in app
    assert "connection.unsubscribe(absent)" in app
    assert "<NotFitted" in app


def test_launch_files_default_to_not_fitted():
    njord = (PACKAGE.parent / "bringup" / "launch" / "njord.launch.py").read_text()
    gui = (PACKAGE / "launch" / "gui.launch.py").read_text()
    for arg in ("enable_lidar", "enable_usb_camera"):
        assert f'DeclareLaunchArgument("{arg}",' in njord
    assert '"lidar_fitted": LaunchConfiguration("enable_lidar")' in njord
    assert '"camera_fitted": LaunchConfiguration("enable_usb_camera")' in njord
    for arg in ("lidar_fitted", "camera_fitted"):
        assert f'"{arg}",\n            default_value="false"' in gui
