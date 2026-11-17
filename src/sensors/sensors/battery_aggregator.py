"""Combine the BMS reports of packs wired in parallel into /battery/state.

The boat has two 4S LiPo packs in parallel, probably one BQ76920 BMS each
(docs/HARDWARE_DECISIONS.md). Each BMS has its own ``bms_reader`` instance
publishing on its own topic (``/battery/pack1``, ``/battery/pack2``). Everything
downstream — the mission GUI, the pre-flight, the cloud relay — reads ONE
battery on ``/battery/state``; this node is what produces it.

The combination rules live in ``asket_common.battery.combine_parallel_packs``
(ROS-free, tested): mean voltage, summed current, the lowest pack's state of
charge, the hottest pack's temperature, and the weakest pack's cells. A pack
that has not reported for ``stale_s`` is left out and named in /diagnostics,
so one pack silently dropping off the bus is visible rather than averaged away.

Nothing is published while no pack is reporting: an absent battery reading is
"not sent", never a zeroed one.
"""
import math
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from sensor_msgs.msg import BatteryState

from asket_common.battery import PackReading, combine_parallel_packs


class BatteryAggregator(Node):
    def __init__(self):
        super().__init__("battery_aggregator")

        self.declare_parameter("pack_topics", ["/battery/pack1", "/battery/pack2"])
        self.declare_parameter("output_topic", "/battery/state")
        self.declare_parameter("stale_s", 5.0)
        self.declare_parameter("rate_hz", 1.0)

        self._stale_s = float(self.get_parameter("stale_s").value)
        self._latest: dict[str, tuple[BatteryState, float]] = {}
        self._topics = list(self.get_parameter("pack_topics").value)

        for topic in self._topics:
            self.create_subscription(BatteryState, topic, self._make_cb(topic), 10)

        self._pub = self.create_publisher(
            BatteryState, self.get_parameter("output_topic").value, 10)
        self._diag_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.create_timer(1.0 / float(self.get_parameter("rate_hz").value), self._tick)
        self.get_logger().info(f"combining {', '.join(self._topics)} (parallel packs)")

    def _make_cb(self, topic):
        def cb(msg):
            self._latest[topic] = (msg, time.monotonic())
        return cb

    def _readings(self) -> list[PackReading]:
        now = time.monotonic()
        readings = []
        for topic in self._topics:
            name = topic.rstrip("/").rsplit("/", 1)[-1]
            entry = self._latest.get(topic)
            if entry is None:
                readings.append(PackReading(name))
                continue
            msg, at = entry
            readings.append(PackReading(
                name=name,
                voltage=float(msg.voltage),
                current=float(msg.current),
                percentage=float(msg.percentage),
                temperature=float(msg.temperature),
                cells=[float(c) for c in msg.cell_voltage],
                age_s=now - at,
            ))
        return readings

    def _tick(self):
        combined = combine_parallel_packs(self._readings(), stale_s=self._stale_s)
        self._publish_diagnostics(combined)
        if combined is None:
            return

        out = BatteryState()
        out.header.stamp = self.get_clock().now().to_msg()
        out.voltage = combined.voltage
        out.current = combined.current
        out.percentage = combined.percentage
        out.temperature = combined.temperature
        out.cell_voltage = combined.cells
        out.present = True
        out.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LIPO
        out.location = "+".join(combined.packs_used)
        self._pub.publish(out)

    def _publish_diagnostics(self, combined):
        status = DiagnosticStatus()
        status.name = "battery: parallel packs"
        status.hardware_id = "battery_aggregator"
        if combined is None:
            status.level = DiagnosticStatus.ERROR
            status.message = "No pack is reporting"
        else:
            status.level = DiagnosticStatus.WARN if combined.warnings else DiagnosticStatus.OK
            status.message = "; ".join(combined.warnings) or "OK"
            status.values.append(KeyValue(key="packs used", value=", ".join(combined.packs_used)))
            soc = combined.percentage
            status.values.append(KeyValue(
                key="SoC est. (%)",
                value=f"{soc * 100:.0f}" if math.isfinite(soc) else "?"))
            if combined.imbalance_v is not None:
                status.values.append(KeyValue(
                    key="pack imbalance (V)", value=f"{combined.imbalance_v:.2f}"))
        arr = DiagnosticArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        arr.status.append(status)
        self._diag_pub.publish(arr)


def main(args=None):
    rclpy.init(args=args)
    node = BatteryAggregator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
