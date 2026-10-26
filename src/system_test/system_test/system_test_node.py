"""ROS 2 node: built-in test.

Aggregates ``diagnostic_msgs/DiagnosticArray`` from every node — each one
publishes its own health continuously — and adds the checks that need a view
across the whole system: is the sonar's clock disciplined, is there enough
disk, does the heading agree with the course.

Runs **automatically at boot**, so a result is waiting as soon as a client
connects. Nobody is aboard the vessel; an operator on the beach should not have
to ask for the first answer.

Active tests are not here. The motor test gate lives in
``system_test.core.motor_test`` and is deliberately not wired to a service:
wiring it belongs to the day a human is standing next to the boat.
"""

from __future__ import annotations

import math
import os
import tempfile
import time

import rclpy
from asket_interfaces.msg import (
    HeadingStatus,
    LinkStatus,
    MissionState,
    PicoStatus,
    SonarStatus,
    SystemTestItem,
    SystemTestReport,
)
from asket_interfaces.srv import RunSystemTest
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from rclpy.node import Node
from nav_msgs.msg import Odometry
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import BatteryState, Imu, LaserScan, NavSatFix, TimeReference
from std_msgs.msg import String, UInt32

from asket_common.heading import compass_deg_from_enu_quaternion, heading_from_odometry
from asket_common.mode_arbitration import ESTOP_FEEDBACK_ENABLED
from omniscan_bridge.core.mounting import mounting_state_from_keyvalues
from system_test.core.checks import FAIL, PASS, SKIPPED, WARN, Thresholds, run_checks
from system_test.core.history import PreflightHistory
from system_test.core.live_inputs import gps_time_facts, heading_facts, pico_facts

SENSOR_QOS = QoSProfile(depth=5, reliability=QoSReliabilityPolicy.BEST_EFFORT)
#: Latched, so a client connecting later still gets the boot-time verdict.
LATCHED_QOS = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)

_STATUS_VALUES = {
    PASS: SystemTestItem.PASS,
    WARN: SystemTestItem.WARN,
    FAIL: SystemTestItem.FAIL,
    SKIPPED: SystemTestItem.SKIPPED,
}


def _endurance_s(battery) -> float | None:
    """Seconds left at the present draw, or None.

    ``BatteryState`` carries current as a negative number when discharging, and
    publishes NaN for anything the BMS does not report. Both have to be ruled
    out before dividing, because the answer is quoted at the operator by the
    disk check and a fabricated one there is worse than a missing one.
    """
    if battery is None:
        return None
    charge, current = float(battery.charge), float(battery.current)
    if math.isnan(charge) or math.isnan(current):
        return None
    draw = abs(current)
    if draw < 0.1 or charge <= 0.0:
        return None
    return charge / draw * 3600.0


class SystemTestNode(Node):
    def __init__(self) -> None:
        super().__init__("system_test")

        self.declare_parameter("history_path", "/data/system_test.jsonl")
        self.declare_parameter("run_at_boot", True)
        self.declare_parameter("boot_delay_s", 15.0)
        self.declare_parameter("periodic_interval_s", 0.0)
        self.declare_parameter("disk_path", "/data")
        self.declare_parameter("measure_disk_speed", True)
        self.declare_parameter(
            "expected_nodes",
            ["omniscan_bridge", "mission_recorder", "gui_backend"],
        )
        # What the real boat publishes (docs/HARDWARE_DECISIONS.md). The
        # previous fixed names were the simulator's: /scan, /mavros/battery,
        # /pico/status as PicoStatus. On the boat the Pico line is a raw
        # std_msgs/String, the battery is the BMS aggregate and the lidar
        # scan, when there is one, is /lidar_driver/scan_raw.
        self.declare_parameter("battery_topic", "/battery/state")
        self.declare_parameter("scan_topic", "/lidar_driver/scan_raw")
        # "state_line": std_msgs/String from pico_bridge (the boat).
        # "pico_status": asket_interfaces/PicoStatus (asket_sim's sim_node).
        self.declare_parameter("pico_status_format", "state_line")
        # Equipment that is not aboard or not watched by this stack. Its
        # checks are listed as "not checked" and do not ground the boat.
        self.declare_parameter("lidar_fitted", False)
        self.declare_parameter("sonar_monitored", False)
        # The Nav2 survey profile is running, so its geofence must be active.
        self.declare_parameter("geofence_expected", False)

        self.history = PreflightHistory(self.get_parameter("history_path").value)
        self.thresholds = Thresholds()

        self._fix: NavSatFix | None = None
        self._imu: Imu | None = None
        self._battery: BatteryState | None = None
        self._scan: LaserScan | None = None
        self._heading: HeadingStatus | None = None
        self._sonar: SonarStatus | None = None
        self._pico: PicoStatus | None = None
        self._pico_line: str | None = None
        self._pico_at: float = 0.0
        self._heading_at: float = 0.0
        self._odom: Odometry | None = None
        self._odom_at: float = 0.0
        self._sats: int | None = None
        self._time_ref: TimeReference | None = None
        self._time_ref_at: float = 0.0
        self._link: LinkStatus | None = None
        self._mission: MissionState | None = None
        self._disk_write_mbps: float | None = None

        self.create_subscription(NavSatFix, "/mavros/global_position/global",
                                 self._set("_fix"), SENSOR_QOS)
        self.create_subscription(Imu, "/mavros/imu/data", self._set("_imu"), SENSOR_QOS)
        self.create_subscription(BatteryState, self.get_parameter("battery_topic").value,
                                 self._set("_battery"), SENSOR_QOS)
        self.create_subscription(LaserScan, self.get_parameter("scan_topic").value,
                                 self._set("_scan"), SENSOR_QOS)
        # The simulator's heading; on the boat nothing publishes it and the
        # EKF odometry below is the heading.
        self.create_subscription(HeadingStatus, "/asket/heading", self._on_heading, 10)
        self.create_subscription(Odometry, "/odometry/filtered", self._on_odom, SENSOR_QOS)
        self.create_subscription(UInt32, "/mavros/global_position/raw/satellites",
                                 self._on_sats, SENSOR_QOS)
        self.create_subscription(TimeReference, "/mavros/time_reference",
                                 self._on_time_ref, SENSOR_QOS)
        self.create_subscription(SonarStatus, "/sonar/status", self._set("_sonar"), 10)
        self.create_subscription(LinkStatus, "/gui/link_status", self._set("_link"), 10)
        self.create_subscription(MissionState, "/mission/state", self._set("_mission"), 10)
        if self.get_parameter("pico_status_format").value == "pico_status":
            self.create_subscription(PicoStatus, "/pico/status", self._on_pico, 10)
        else:
            self.create_subscription(String, "/pico/status", self._on_pico_line, 10)
        self.create_subscription(DiagnosticArray, "/diagnostics", self._on_diagnostics, 10)

        self.pub_report = self.create_publisher(SystemTestReport, "/system_test/report",
                                                LATCHED_QOS)
        self.create_service(RunSystemTest, "~/run", self._on_run)

        self._node_diagnostics: dict[str, DiagnosticStatus] = {}

        if self.get_parameter("run_at_boot").value:
            # Delayed: everything else needs a moment to come up, and a
            # pre-flight run against a half-started system would report a
            # dozen faults that do not exist.
            delay = float(self.get_parameter("boot_delay_s").value)
            self._boot_timer = self.create_timer(delay, self._run_at_boot)

        interval = float(self.get_parameter("periodic_interval_s").value)
        if interval > 0:
            self.create_timer(interval, lambda: self.run(None))

        self.get_logger().info("built-in test ready")

    # -- inputs -----------------------------------------------------------

    def _set(self, attribute: str):
        def callback(msg) -> None:
            setattr(self, attribute, msg)

        return callback

    def _on_pico(self, msg: PicoStatus) -> None:
        self._pico = msg
        self._pico_at = time.monotonic()

    def _on_pico_line(self, msg: String) -> None:
        self._pico_line = str(msg.data)
        self._pico_at = time.monotonic()

    def _on_heading(self, msg: HeadingStatus) -> None:
        self._heading = msg
        self._heading_at = time.monotonic()

    def _on_odom(self, msg: Odometry) -> None:
        self._odom = msg
        self._odom_at = time.monotonic()

    def _on_sats(self, msg: UInt32) -> None:
        self._sats = int(msg.data)

    def _on_time_ref(self, msg: TimeReference) -> None:
        self._time_ref = msg
        self._time_ref_at = time.monotonic()

    def _absent(self) -> set[str]:
        absent = set()
        if not self.get_parameter("lidar_fitted").value:
            absent.add("lidar")
        if not self.get_parameter("sonar_monitored").value:
            absent.add("sonar_bridge")
        if not self.get_parameter("geofence_expected").value:
            absent.add("survey_nav")
        return absent

    def _real_vessel_facts(self) -> dict:
        """Keys from the boat's own topics, laid over the simulator's.

        Only what actually arrived: a key that is absent stays as the
        simulator path left it (None), so its check reports unknown.
        """
        now = time.monotonic()
        facts: dict = {}
        if self._pico_line is not None:
            facts.update(pico_facts(self._pico_line, now - self._pico_at))
        sim_heading_fresh = self._heading is not None and now - self._heading_at < 2.0
        if self._odom is not None and not sim_heading_fresh:
            q = self._odom.pose.pose.orientation
            twist = self._odom.twist.twist.linear
            facts.update(heading_facts(heading_from_odometry(
                compass_deg_from_enu_quaternion(q.x, q.y, q.z, q.w),
                now - self._odom_at, twist.x, twist.y,
            )))
        if self._sats is not None:
            facts["num_sats"] = self._sats
        if self._time_ref is not None:
            ref = self._time_ref
            facts.update(gps_time_facts(
                ref.header.stamp.sec + ref.header.stamp.nanosec * 1e-9,
                ref.time_ref.sec + ref.time_ref.nanosec * 1e-9,
                now - self._time_ref_at,
            ))
        return facts

    def _on_diagnostics(self, msg: DiagnosticArray) -> None:
        for status in msg.status:
            self._node_diagnostics[status.name] = status

    # -- state assembly ---------------------------------------------------

    def _measure_disk_speed(self) -> float | None:
        """Write a few megabytes and time it.

        The sonar produces several GB per hour; a slow card drops data
        mid-survey, and it does so silently. Measuring beats assuming.
        """
        if not self.get_parameter("measure_disk_speed").value:
            return None
        path = self.get_parameter("disk_path").value
        payload = os.urandom(4 * 1024 * 1024)
        try:
            with tempfile.NamedTemporaryFile(dir=path, delete=True) as handle:
                start = time.monotonic()
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                elapsed = time.monotonic() - start
        except OSError as exc:
            self.get_logger().warn(f"could not measure disk speed: {exc}")
            return None
        return (len(payload) / 1024**2) / elapsed if elapsed > 0 else None

    def _state(self) -> dict:
        import shutil

        fix, imu, battery, scan = self._fix, self._imu, self._battery, self._scan
        heading, sonar, pico, link = self._heading, self._sonar, self._pico, self._link

        roll = pitch = None
        if imu is not None:
            q = imu.orientation
            roll = math.degrees(math.atan2(
                2 * (q.w * q.x + q.y * q.z), 1 - 2 * (q.x * q.x + q.y * q.y)))
            pitch = math.degrees(math.asin(
                max(-1.0, min(1.0, 2 * (q.w * q.y - q.z * q.x)))))

        lidar_points = None
        if scan is not None:
            lidar_points = sum(
                1 for r in scan.ranges if not math.isinf(r) and not math.isnan(r)
            )

        try:
            disk_free = shutil.disk_usage(self.get_parameter("disk_path").value).free
        except OSError:
            disk_free = None

        expected = list(self.get_parameter("expected_nodes").value)
        # The ROS graph first: a node that publishes no /diagnostics of its
        # own (gui_backend, for one) is running all the same, and reporting
        # it missing turned a working stack NO-GO.
        graph = set(self.get_node_names())
        present = [
            n for n in expected
            if n in graph or any(n in name for name in self._node_diagnostics)
        ]

        return {
            "pico_age_s": (time.monotonic() - self._pico_at) if self._pico_at else None,
            "rc_link_ok": bool(pico.rc_link_ok) if pico else None,
            "rc_channel8_raw": int(pico.rc_channel8_raw) if pico else None,
            "rc_channel7_raw": int(pico.rc_channel7_raw) if pico else None,
            "pico_firmware_version": (int(pico.firmware_version) or None) if pico else None,
            # A firmware compile flag, not a wire field: the Pico cannot
            # report whether its own feedback was compiled in. Mirrored in
            # asket_common and pinned to the sketch by the cross-check test.
            "pico_estop_feedback_enabled": ESTOP_FEEDBACK_ENABLED,
            "num_sats": None,
            "gnss_fix_type": (int(fix.status.status) + 3) if fix else None,
            "hdop": (math.sqrt(fix.position_covariance[0]) / 2.5)
            if fix is not None and fix.position_covariance[0] > 0 else None,
            "heading_valid": bool(heading.valid) if heading else None,
            "heading_source": heading.source if heading else None,
            "heading_accuracy_deg": float(heading.accuracy_deg) if heading else None,
            "heading_divergence_deg": float(heading.divergence_deg) if heading else None,
            "heading_divergence_meaningful": bool(heading.divergence_meaningful)
            if heading else None,
            "roll_deg": roll,
            "pitch_deg": pitch,
            "lidar_rotation_hz": (1.0 / scan.scan_time)
            if scan is not None and scan.scan_time > 0 else None,
            "lidar_points_per_revolution": lidar_points,
            "sonar_connected": bool(sonar.connected) if sonar else None,
            "sonar_discovered": None,
            "sonar_ping_rate_hz": float(sonar.actual_ping_rate_hz) if sonar else None,
            "sonar_points_per_ping": int(sonar.points_per_ping) if sonar else None,
            "clock_offset_ms": int(sonar.clock_offset_ms) if sonar else None,
            "disk_free_bytes": disk_free,
            "disk_write_mbps": self._disk_write_mbps,
            "state_of_charge": float(battery.percentage) if battery else None,
            "battery_voltage": float(battery.voltage) if battery else None,
            # Charge remaining over present draw. Reported only when both are
            # real numbers: an invented endurance is worse than none, because
            # the disk check would quote it (docs/open_questions.md Q5).
            "battery_endurance_s": _endurance_s(battery),
            "link_rtt_ms": float(link.rtt_ms) if link else None,
            "link_active": link.active_link if link else None,
            "expected_nodes": expected,
            "present_nodes": present,
            "mounting": self._mounting(),
            **self._geofence(),
            **self._real_vessel_facts(),
        }

    def _geofence(self) -> dict:
        """geofence_mask_node's own report, from /diagnostics."""
        status = self._node_diagnostics.get("geofence")
        if status is None:
            return {}
        # DiagnosticStatus.level is a one-byte `bytes` in rclpy, not an int.
        level = status.level[0] if isinstance(status.level, bytes) else int(status.level)
        return {
            "geofence_level": {0: "OK", 1: "WARN"}.get(level, "ERROR"),
            "geofence_message": status.message,
        }

    def _mounting(self) -> dict | None:
        """The mounting geometry ``omniscan_bridge`` is actually applying.

        Taken from the bridge's own diagnostics rather than by re-reading
        ``mounting.yaml``: somebody who measures the vessel and edits the file
        has not changed the geometry until the bridge is restarted, and a check
        that went green in between would be worse than no check at all.
        """
        for name, status in self._node_diagnostics.items():
            if "omniscan" not in name:
                continue
            kv = {v.key: v.value for v in status.values}
            # Parsed by the module that writes it, not here. The GUI backend
            # needs the same values to confirm a setup reload, and two
            # hand-written parsers would be a reload that confirms in one place
            # and not the other.
            state = mounting_state_from_keyvalues(kv)
            if state is None:
                continue
            return state
        return None

    # -- running ----------------------------------------------------------

    def _run_at_boot(self) -> None:
        self._boot_timer.cancel()
        report = self.run(None)
        level = self.get_logger().info if report.go else self.get_logger().error
        level(f"boot pre-flight: {report.summary}")

    def run(self, only: list[str] | None):
        started = time.monotonic()
        self._disk_write_mbps = self._measure_disk_speed()

        report = run_checks(
            self._state(), self.thresholds, only=only,
            run_utc_ms=self.get_clock().now().nanoseconds // 1_000_000,
            absent=self._absent(),
        )
        report.duration_s = time.monotonic() - started
        self.history.append(report)
        self.pub_report.publish(self._to_msg(report))
        return report

    def _to_msg(self, report) -> SystemTestReport:
        msg = SystemTestReport()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.go = report.go
        msg.summary = report.summary
        msg.duration_s = float(report.duration_s)
        msg.run_utc_ms = int(report.run_utc_ms)
        for result in report.items:
            item = SystemTestItem()
            item.id = result.id
            item.name = result.name
            item.status = _STATUS_VALUES[result.status]
            item.message = result.message
            item.remedy = result.remedy
            item.measured_value = (
                0.0 if isinstance(result.measured_value, float)
                and math.isnan(result.measured_value)
                else float(result.measured_value)
            )
            item.units = result.units
            item.active = result.active
            item.setup_field = result.setup_field
            msg.items.append(item)
        return msg

    def _on_run(self, request, response):
        if request.include_active:
            # Safety rule 6. Refused here rather than gated further down, so
            # there is no path through this service that can reach a thruster.
            response.accepted = False
            response.message = (
                "Active tests are not available through this service. A motor "
                "test needs an operator standing next to the vessel, an "
                "explicit acknowledgement that it is out of the water or "
                "securely moored, and a deliberate action at the boat."
            )
            return response

        report = self.run(list(request.only) or None)
        response.accepted = True
        response.message = report.summary
        return response


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SystemTestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
