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


def test_battery_values_come_from_setup_when_answered(tmp_path):
    path = tmp_path / "asket_setup.yaml"
    source = _source(path)
    source.config["vessel"] = {"battery_capacity_wh": 200.0}
    # Not answered: topics.yaml wins over the field default.
    assert source._setup_or_config("vessel.battery.capacity_wh", 200.0) == 200.0
    assert source._setup_or_config("vessel.battery.hotel_load_w", None) == 85.0
    _save(path, vessel__battery__capacity_wh=300.0, vessel__battery__hotel_load_w=60.0)
    source.setup.refresh(force=True)
    assert source._setup_or_config("vessel.battery.capacity_wh", 200.0) == 300.0
    assert source._setup_or_config("vessel.battery.hotel_load_w", None) == 60.0


def test_the_shore_station_comes_from_setup_once_position_and_bearing_are_in(tmp_path):
    from gui_backend.core.ros_source import _radio_from_config, _station_config

    path = tmp_path / "asket_setup.yaml"
    values = SetupValues(path)
    config = {"shore_station": {"configured": False}}
    assert _radio_from_config(config, values) == (None, None)

    # A position without a bearing is not a station: it would draw a sector
    # pointing north.
    _save(path, deployment__station__lat=-22.95, deployment__station__lon=14.5)
    values.refresh(force=True)
    assert _radio_from_config(config, values) == (None, None)

    _save(
        path,
        deployment__station__lat=-22.95, deployment__station__lon=14.5,
        deployment__station__boresight_deg=270.0, deployment__station__height_m=4.0,
        deployment__sea__state="glassy", deployment__radio__channel_width_mhz="20",
        vessel__radio__antenna_height_m=1.5,
    )
    values.refresh(force=True)
    station = _station_config(config, values)
    assert station["configured"] is True
    assert station["wave_height_m"] == 0.02
    assert station["channel_width_mhz"] == 20.0
    radio, origin = _radio_from_config(config, values)
    assert (origin.lat_deg, origin.lon_deg) == (-22.95, 14.5)
    assert radio.station.boresight_deg == 270.0
    assert radio.station.height_m == 4.0
    assert radio.vessel.height_m == 1.5
