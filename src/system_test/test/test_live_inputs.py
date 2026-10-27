"""The pre-flight reads the real vessel's topics, not only the simulator's.

See ``system_test.core.live_inputs`` for the critical checks that could never
pass on the boat before this.
"""

import math

import pytest
from asket_common.heading import compass_deg_from_enu_quaternion, heading_from_odometry
from system_test.core.checks import FAIL, PASS, SKIPPED, WARN, Thresholds, run_checks
from system_test.core.live_inputs import gps_time_facts, heading_facts, pico_facts

#: A line as pico-node_v5 emits it (field order from the sketch).
V5_LINE = (
    "STATE ver=5 mode=2 armed=1 relay=1 wantauto=0 link=1 estoplatch=0 "
    "thr=991 yaw=991 ch7=1500 ch8=1200 sbusok=1000 sbusbad=0 sbusfs=0 sbuslost=0 "
    "rclatch=0 txdrop=0"
)


def _one(state, check_id):
    return next(i for i in run_checks(state, Thresholds()).items if i.id == check_id)


def test_a_v4_state_line_feeds_the_pico_checks():
    facts = pico_facts(V5_LINE, age_s=0.3)
    assert facts["pico_firmware_version"] == 5
    assert facts["rc_link_ok"] is True
    assert facts["rc_channel8_raw"] == 1200
    assert facts["pico_state_parsed"] is True
    assert _one(facts, "pico.link").status == PASS
    assert _one(facts, "pico.firmware_version").status == PASS
    assert _one(facts, "rc.link").status == PASS


def test_nothing_from_the_pico_is_unknown_not_passed():
    assert pico_facts(None, None) == {}
    assert _one({}, "pico.link").status == SKIPPED


@pytest.mark.parametrize("yaw_enu_deg,compass", [(90, 0), (0, 90), (-90, 180), (180, 270)])
def test_enu_yaw_becomes_a_compass_bearing(yaw_enu_deg, compass):
    half = math.radians(yaw_enu_deg) / 2
    assert compass_deg_from_enu_quaternion(0, 0, math.sin(half), math.cos(half)) == \
        pytest.approx(compass % 360, abs=1e-6)


def test_fresh_ekf_odometry_is_a_valid_heading():
    facts = heading_facts(heading_from_odometry(45.0, age_s=0.2, body_vx=1.5))
    assert facts["heading_valid"] is True
    assert facts["heading_source"] == "ekf"
    assert facts["heading_divergence_deg"] == pytest.approx(0.0)
    assert facts["heading_divergence_meaningful"] is True
    assert _one(facts, "heading.valid").status in (PASS, WARN)


@pytest.mark.parametrize("age", [10.0, None])
def test_stale_or_absent_odometry_is_not_a_heading(age):
    facts = heading_facts(heading_from_odometry(45.0, age_s=age))
    assert facts["heading_valid"] is False
    assert _one(facts, "heading.valid").status == FAIL


def test_sideways_drift_shows_as_divergence():
    # forward and to the left at equal speed: the course is 45 deg to port
    estimate = heading_from_odometry(90.0, age_s=0.1, body_vx=1.0, body_vy=1.0)
    assert estimate.cog_deg == pytest.approx(45.0)
    assert abs(estimate.divergence_deg) == pytest.approx(45.0)


def test_gps_time_reference_gives_the_clock_offset():
    facts = gps_time_facts(measured_at_s=1000.250, gps_time_s=1000.0, age_s=1.0)
    assert facts == {"gps_time_offset_ms": 250, "gps_time_age_s": 1.0}
    assert _one({**facts, "gnss_fix_type": 3}, "clock.gps_offset").status == PASS


def test_no_gps_time_is_no_key():
    assert gps_time_facts(None, None, None) == {}
    assert gps_time_facts(1.0, 0.0, 1.0) == {}


# -- equipment that is absent on purpose ------------------------------------


def _healthy_boat():
    """Everything the real boat reports when all is well — no lidar, no sonar."""
    return {
        **pico_facts(V5_LINE, age_s=0.2),
        **heading_facts(heading_from_odometry(10.0, age_s=0.1)),
        **gps_time_facts(1000.05, 1000.0, 1.0),
        "gnss_fix_type": 3, "num_sats": 14, "hdop": 0.8,
        "roll_deg": 0.5, "pitch_deg": 0.3,
        "disk_free_bytes": 200 * 1024**3, "disk_write_mbps": 80.0,
        "state_of_charge": 0.9, "battery_voltage": 16.2,
        "geofence_level": "OK", "geofence_message": "'test block', 72.0 ha",
    }


def test_without_absent_equipment_declared_the_sonar_grounds_the_boat():
    report = run_checks(_healthy_boat())
    assert not report.go
    assert "Sonar" in report.summary


def test_absent_lidar_and_sonar_do_not_ground_the_boat():
    report = run_checks(_healthy_boat(), absent={"lidar", "sonar_bridge"})
    assert report.go, report.summary
    by_id = {i.id: i for i in report.items}
    for check_id in ("lidar.spin", "sonar.link", "sonar.clock", "sonar.mounting"):
        assert by_id[check_id].status == SKIPPED
        assert by_id[check_id].message.startswith("Not checked:")


def test_absent_equipment_does_not_excuse_a_silent_critical_check():
    state = _healthy_boat()
    del state["state_of_charge"], state["battery_voltage"]
    report = run_checks(state, absent={"lidar", "sonar_bridge"})
    assert not report.go
    assert "Battery" in report.summary


# -- the geofence (Nav2 survey profile) --------------------------------------


@pytest.mark.parametrize("level,status", [("OK", PASS), ("WARN", FAIL), ("ERROR", FAIL)])
def test_the_geofence_check_follows_the_node(level, status):
    state = {"geofence_level": level, "geofence_message": "test"}
    assert _one(state, "nav.geofence").status == status


def test_no_geofence_report_with_the_survey_profile_is_no_go():
    state = _healthy_boat()
    del state["geofence_level"]
    report = run_checks(state, absent={"lidar", "sonar_bridge"})
    assert not report.go
    assert "Geofence" in report.summary


def test_without_the_survey_profile_the_geofence_is_not_checked():
    report = run_checks(_healthy_boat(), absent={"lidar", "sonar_bridge", "survey_nav"})
    by_id = {i.id: i for i in report.items}
    assert by_id["nav.geofence"].message.startswith("Not checked:")
    assert report.go, report.summary
