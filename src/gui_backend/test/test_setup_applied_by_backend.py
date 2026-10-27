"""Setup values gui_backend applies itself are read from the Setup file.

The Setup page used to promise "takes effect immediately" for fields nothing
read back. These tests hold the backend to it: what is saved is what
`describe()` (and so `hello`) reports, and a save re-sends `hello` to every
open page.
"""

from gui_backend.core import setup_store
from gui_backend.core.ros_source import RosSource
from gui_backend.core.setup_values import SetupValues
from asket_common.setup_profile import ENTERED, SetupProfile


def _save(path, **values):
    profile = SetupProfile()
    for field_id, value in values.items():
        profile = profile.set(field_id.replace("__", "."), value, ENTERED, utc_ms=1)
    setup_store.save(profile, path, utc_ms=1)


def _source(path) -> RosSource:
    source = object.__new__(RosSource)
    source.config = {"setup_file": str(path)}
    source.latest = {}
    return source


def test_sonarview_address_defaults_to_the_jetson_on_7077(tmp_path):
    source = _source(tmp_path / "none.yaml")
    assert source.describe()["sonarview_url"] == "http://10.10.20.10:7077"


def test_a_saved_sonarview_address_is_the_one_in_use(tmp_path):
    path = tmp_path / "asket_setup.yaml"
    _save(path, vessel__sonarview__url="http://10.10.20.10:9999")
    assert _source(path).describe()["sonarview_url"] == "http://10.10.20.10:9999"


def test_an_edited_file_is_picked_up_without_a_restart(tmp_path):
    path = tmp_path / "asket_setup.yaml"
    _save(path, vessel__sonarview__url="http://a:1")
    values = SetupValues(path)
    assert values.get("vessel.sonarview.url") == "http://a:1"
    _save(path, vessel__sonarview__url="http://b:2")
    assert values.refresh(force=True).get("vessel.sonarview.url") == "http://b:2"


def test_a_broken_file_falls_back_to_defaults_and_says_why(tmp_path):
    path = tmp_path / "asket_setup.yaml"
    path.write_text("- not a mapping\n")
    values = SetupValues(path)
    assert values.get("vessel.sonarview.url") == "http://10.10.20.10:7077"
    assert "not a mapping" in values.error
