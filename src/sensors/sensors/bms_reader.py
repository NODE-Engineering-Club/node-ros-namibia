"""BMS serial reader.

Owns the serial link to the battery management system (connected via an
MCP2221 USB-UART bridge) and republishes its periodic text report as ROS
topics: a standard sensor_msgs/BatteryState (voltage/cells/temp/current —
plottable, gauge-able, but no dedicated "battery" panel in Foxglove), a
diagnostic_msgs/DiagnosticArray on /diagnostics (renders natively in
Foxglove's Diagnostics panel — a colored OK/ERROR summary plus every field
as key/value, ERROR if a fault flag trips or DSG drops), and a raw string
with everything the BMS reports verbatim.

Expected report: one CSV row every 500 ms (asket_common.battery.parse_bms_csv
holds the column list). The header and lines starting with # are skipped. The
raw topic carries the CSV row verbatim, which is what the cockpit's per-pack
view reads. With comms=0 the BMS chip is not answering: that is reported on
/diagnostics and the raw topic, and no BatteryState is published, because its
zeros are not measurements.

State of charge: the BMS reports none, so ``percentage`` is estimated from
the weakest cell's voltage with a LiPo curve (asket_common.battery). It was
previously left at 0, which any consumer reading it as a state of charge
would show as an empty battery. It is NaN (unknown) when the cells cannot
be read, never 0.

One instance per BMS. The boat has two 4S packs in parallel, so the launch
file starts two (``pack1``, ``pack2``) on their own ports and topics, and
``battery_aggregator`` combines them into /battery/state. The defaults
below keep the single-BMS behaviour of a bare ``ros2 run``.
"""
import rclpy
import serial
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

from asket_common.battery import pack_state_of_charge, parse_bms_csv

SERIAL_PORT = "/dev/ttyACM3"
BAUD_RATE = 115200


class BmsReader(Node):
    def __init__(self):
        super().__init__("bms_reader")

        self.declare_parameter("port", SERIAL_PORT)
        self.declare_parameter("baud", BAUD_RATE)
        # Where this BMS's readings go, and what it is called in /diagnostics.
        # Two packs need two of each; see the module docstring.
        self.declare_parameter("state_topic", "/battery/state")
        self.declare_parameter("raw_topic", "/battery/status_raw")
        self.declare_parameter("pack_name", "")
        # Series cell count. A report with fewer readable cells gives an
        # unknown state of charge rather than one computed from part of the pack.
        self.declare_parameter("cell_count", 4)
        # Per-pack capacity, for BatteryState.design_capacity and an estimated
        # charge (Gens ace 4S 8000 mAh).
        self.declare_parameter("design_capacity_ah", 8.0)
        port = self.get_parameter("port").get_parameter_value().string_value
        baud = self.get_parameter("baud").get_parameter_value().integer_value
        self._pack_name = self.get_parameter("pack_name").value
        self._cell_count = int(self.get_parameter("cell_count").value)
        self._design_ah = float(self.get_parameter("design_capacity_ah").value)

        try:
            self._ser = serial.Serial(port, baud, timeout=0)  # non-blocking
            self.get_logger().info(f"BMS connected on {port} at {baud} baud")
        except serial.SerialException as e:
            self.get_logger().warn(f"Cannot open {port}: {e} — bms_reader idle, no data")
            self._ser = None

        self._rx = b""

        self._state_pub = self.create_publisher(
            BatteryState, self.get_parameter("state_topic").value, 10)
        self._raw_pub = self.create_publisher(
            String, self.get_parameter("raw_topic").value, 10)
        self._diag_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

        self.create_timer(0.2, self._tick)

    def _tick(self):
        if self._ser is None:
            return
        try:
            n = self._ser.in_waiting
            if n:
                self._rx += self._ser.read(n)
        except serial.SerialException as e:
            self.get_logger().warn(f"BMS serial read error: {e}", throttle_duration_sec=5.0)
            return

        while b"\n" in self._rx:
            raw, self._rx = self._rx.split(b"\n", 1)
            line = raw.decode(errors="replace").strip()
            if not line or line.startswith("#") or line.startswith("t_ms"):
                continue
            row = parse_bms_csv(line)
            if row is None:
                self.get_logger().warn(f"Unparsed BMS line: {line!r}", throttle_duration_sec=5.0)
                continue
            self._publish_row(row, line)

    def _publish_row(self, row, line):
        self._raw_pub.publish(String(data=line))
        soc = None
        if row["comms"]:
            soc = pack_state_of_charge(row["cells_v"], expected_cells=self._cell_count)
            self._publish_state(row, soc)
        self._publish_diagnostics(row, soc)

    def _publish_state(self, row, soc):
        state = BatteryState()
        state.header.stamp = self.get_clock().now().to_msg()
        state.voltage = row["pack_v"]
        state.current = row["current_a"]
        state.temperature = row["temp_c"] if row["temp_c"] is not None else float("nan")
        state.cell_voltage = row["cells_v"]
        state.present = True
        state.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LIPO
        # 0..1 per sensor_msgs/BatteryState. NaN = unknown, never 0.
        state.percentage = soc if soc is not None else float("nan")
        state.design_capacity = self._design_ah
        state.charge = soc * self._design_ah if soc is not None else float("nan")
        state.location = self._pack_name
        # From the measured current: a closed CHG FET does not mean charging.
        if row["current_a"] > 0.1:
            state.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_CHARGING
        elif row["current_a"] < -0.1:
            state.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        else:
            state.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_NOT_CHARGING
        self._state_pub.publish(state)

    def _publish_diagnostics(self, row, soc):
        fault = not row["comms"] or bool(row["faults"]) or not row["dsg"]
        status = DiagnosticStatus()
        status.name = f"bms: BQ76920 {self._pack_name}".strip()
        status.hardware_id = "bq76920"
        status.level = DiagnosticStatus.ERROR if fault else DiagnosticStatus.OK
        status.message = ("BMS not answering" if not row["comms"]
                          else "Fault" if fault else "OK")

        def kv(key, value):
            status.values.append(KeyValue(key=key, value=str(value)))

        kv("Pack (V)", f"{row['pack_v']:.2f}" if row["pack_v"] is not None else "?")
        kv("SoC est. (%)", f"{soc * 100:.0f}" if soc is not None else "?")
        for i, v in enumerate(row["cells_v"], start=1):
            kv(f"Cell {i} (V)", f"{v:.3f}")
        kv("Temp (C)", f"{row['temp_c']:.1f}" if row["temp_c"] is not None else "?")
        kv("Current (A)", f"{row['current_a']:.2f}" if row["current_a"] is not None else "?")
        kv("CHG", "ON" if row["chg"] else "OFF")
        kv("DSG", "ON" if row["dsg"] else "OFF")
        kv("Faults", ", ".join(row["faults"]) or "none")
        kv("Retries", row["retries"])

        arr = DiagnosticArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        arr.status.append(status)
        self._diag_pub.publish(arr)

    def destroy_node(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = BmsReader()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
