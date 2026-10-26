"""Everything the stack must keep is written under /data, and /data is mounted.

The container runs with ``--rm``. Anything written outside the mounted data
directory is lost on every restart — which is what happened to the Setup
page's file, written to ``~/.ros`` inside the container.

Plain text checks: the defaults live in a Containerfile, YAML and node
parameter declarations, none of which need ROS to read.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"


def _text(path: Path) -> str:
    return path.read_text()


def test_the_setup_file_is_moved_under_data_in_the_image():
    containerfile = _text(ROOT / "Containerfile")
    assert "ENV ASKET_SETUP_FILE=/data/setup/asket_setup.yaml" in containerfile


def test_every_written_default_is_under_data():
    recorder_yaml = _text(SRC / "mission_recorder" / "config" / "recorder.yaml")
    assert re.search(r'missions_root:\s*"/data/missions"', recorder_yaml)

    system_test = _text(SRC / "system_test" / "system_test" / "system_test_node.py")
    assert '"history_path", "/data/system_test.jsonl"' in system_test

    gui = _text(SRC / "gui_backend" / "launch" / "gui.launch.py")
    assert 'default_value="/data/maps/survey.mbtiles"' in gui
    assert 'default_value="/data/maps/tile-cache.sqlite"' in gui


def test_the_jetson_unit_mounts_a_host_directory_on_data():
    unit = _text(ROOT / "deploy" / "jetson" / "asket.service")
    assert "-v ${DATA}:/data" in unit
    assert "Environment=DATA=/srv/asket/data" in unit
    # live /dev, so udev links survive a re-plug (deploy/udev/README.md)
    assert "-v /dev:/dev" in unit
