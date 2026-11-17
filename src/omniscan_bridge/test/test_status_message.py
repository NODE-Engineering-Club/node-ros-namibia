"""SonarStatus must be able to carry every gain the bridge can hold.

Auto gain is -1, and it is the default. A field that cannot represent it kills
the node when the status is published, taking ~/stop_pinging down with it.
"""

import pytest

serialization = pytest.importorskip("rclpy.serialization")
SonarStatus = pytest.importorskip("asket_interfaces.msg").SonarStatus


@pytest.mark.parametrize("gain", [-1, 0, 10])
def test_status_round_trips_every_valid_gain(gain):
    data = serialization.serialize_message(SonarStatus(gain_setting=gain))
    out = serialization.deserialize_message(data, SonarStatus)
    assert out.gain_setting == gain
