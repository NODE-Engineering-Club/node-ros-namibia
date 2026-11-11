from cloud_bridge.onboard_health import (
    parse_iw_link, read_max_temp_c, read_proc_wireless, summarize_sonar_points,
)

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   58.  -52.  -256        0      0      0      0      3        0
 wlan1: 0000   20.  190.  -256        0      0      0      0      0        0
"""


def test_wifi_dbm(tmp_path):
    p = tmp_path / "wireless"
    p.write_text(WIRELESS)
    w = read_proc_wireless("wlan0", str(p))
    assert w == {"interface": "wlan0", "rssi_dbm": -52.0, "quality_pct": 82.9}


def test_wifi_unsigned_level_converted(tmp_path):
    p = tmp_path / "wireless"
    p.write_text(WIRELESS)
    assert read_proc_wireless("wlan1", str(p))["rssi_dbm"] == -66.0


def test_wifi_missing(tmp_path):
    p = tmp_path / "wireless"
    p.write_text(WIRELESS)
    assert read_proc_wireless("eth0", str(p)) is None
    assert read_proc_wireless("wlan0", str(tmp_path / "nope")) is None


def test_temp_ignores_implausible(tmp_path):
    for name, val in [("a", "45500"), ("b", "-40000"), ("c", "51250")]:
        d = tmp_path / name
        d.mkdir()
        (d / "temp").write_text(val)
    assert read_max_temp_c(str(tmp_path / "*" / "temp")) == 51.2
    assert read_max_temp_c(str(tmp_path / "none*" / "temp")) is None


def test_sonar_summary_keeps_strongest():
    pts = [(0.0, float(i), 1.0, float(i)) for i in range(300)] + [(0.0, float("nan"), 1.0, 5.0)]
    s = summarize_sonar_points(pts, max_points=10)
    assert s["n_points"] == 300
    assert len(s["points"]) == 10
    assert s["points"][0] == [299.0, 1.0, 1.0]
    assert s["range_min_m"] == 1.0


def test_sonar_summary_empty():
    assert summarize_sonar_points([])["points"] == []


def test_sonar_summary_is_json_safe_with_numpy():
    import json
    import numpy as np
    pts = [tuple(np.float32(v) for v in (0.0, 1.0, 2.0, 3.0))]
    json.dumps(summarize_sonar_points(pts))


def test_proc_level_on_driver_scale_is_not_dbm(tmp_path):
    # The Jetson's card: level 61 while iw reported -38 dBm.
    p = tmp_path / "wireless"
    p.write_text(WIRELESS.splitlines()[0] + "\n" + WIRELESS.splitlines()[1]
                 + "\n wlan0: 0000   57.   61.    0.  0 0 0 0 0 0\n")
    assert read_proc_wireless("wlan0", str(p))["rssi_dbm"] is None


IW = """Connected to 04:bc:9f:2c:c9:54 (on wlan0)
\tSSID: Kelp Blue
\tfreq: 5680
\tRX: 123 bytes (4 packets)
\tsignal: -38 dBm
\ttx bitrate: 400.0 MBit/s
"""


def test_iw_link():
    w = parse_iw_link(IW, "wlan0")
    assert w == {"interface": "wlan0", "ssid": "Kelp Blue", "freq_mhz": 5680,
                 "rssi_dbm": -38.0, "tx_mbps": 400.0, "quality_pct": 100.0}
    assert parse_iw_link("Not connected.", "wlan0") is None
