"""Publishes the survey geofence as a Nav2 KeepoutFilter mask.

The polygon is geographic (``geofence_file``, default
``/data/setup/geofence.yaml``; format in ``asket_common.geofence``). The
``map`` frame is anchored to the Pixhawk's home position by ``datum_sync``,
so this node waits for the same ``/mavros/home_position/home``, takes the
first one exactly as ``datum_sync`` does, and only then rasterises the fence
into that frame:

* ``/geofence/mask`` (``nav_msgs/OccupancyGrid``, latched): 0 inside the
  fence, 100 (lethal) outside it and in a ring around it;
* ``/geofence/filter_info`` (``nav2_msgs/CostmapFilterInfo``, latched): what
  the ``geofence_layer`` (``nav2_costmap_2d::KeepoutFilter``) in
  ``bringup/config/nav2_params_survey.yaml`` subscribes to.

On ``/diagnostics`` as ``geofence``: OK with the fence's name and area, or
ERROR when there is no usable fence — in which case **Nav2 has no limit at
all**, and the pre-flight's ``nav.geofence`` check says so.
"""
from pathlib import Path

import rclpy
import yaml
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from mavros_msgs.msg import HomePosition
from nav2_msgs.msg import CostmapFilterInfo
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from asket_common.geofence import GeofenceError, load_geofence, rasterize, to_map_frame


class GeofenceMaskNode(Node):
    def __init__(self):
        super().__init__("geofence_mask_node")
        self.declare_parameter("geofence_file", "/data/setup/geofence.yaml")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("resolution_m", 1.0)
        self.declare_parameter("margin_m", 30.0)
        self.declare_parameter("mask_topic", "/geofence/mask")
        self.declare_parameter("filter_info_topic", "/geofence/filter_info")

        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                             reliability=ReliabilityPolicy.RELIABLE)
        self._mask_topic = self.get_parameter("mask_topic").value
        self._pub_mask = self.create_publisher(OccupancyGrid, self._mask_topic, latched)
        self._pub_info = self.create_publisher(
            CostmapFilterInfo, self.get_parameter("filter_info_topic").value, latched)
        self._pub_diag = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

        self._fence = None
        self._problem = ""
        self._origin = None
        self._mask = None
        self._load()

        self.create_subscription(HomePosition, "/mavros/home_position/home", self._on_home, latched)
        self.create_timer(1.0, self._publish_diagnostics)

    def _load(self):
        path = Path(self.get_parameter("geofence_file").value)
        try:
            self._fence = load_geofence(yaml.safe_load(path.read_text()))
            self.get_logger().info(
                f"geofence '{self._fence.name}' loaded from {path} "
                f"({len(self._fence.polygon)} corners); waiting for the home position")
        except FileNotFoundError:
            self._problem = f"no geofence file at {path}"
        except (OSError, yaml.YAMLError, GeofenceError) as e:
            self._problem = f"{path}: {e}"
        if self._problem:
            self.get_logger().error(
                f"{self._problem} — Nav2 has NO area limit. See "
                f"src/bringup/config/geofence.example.yaml.")

    def _on_home(self, msg):
        if self._fence is None or self._origin is not None:
            return    # same rule as datum_sync: the first home anchors the map frame
        self._origin = (msg.geo.latitude, msg.geo.longitude)
        polygon = to_map_frame(self._fence, *self._origin)
        self._mask = rasterize(
            polygon,
            resolution_m=float(self.get_parameter("resolution_m").value),
            margin_m=float(self.get_parameter("margin_m").value),
        )
        self._publish()
        self.get_logger().info(
            f"geofence mask published: {self._mask.width}x{self._mask.height} cells "
            f"at {self._mask.resolution_m:.1f} m, {self._mask.area_m2 / 1e4:.1f} ha inside")

    def _publish(self):
        frame = self.get_parameter("map_frame").value
        stamp = self.get_clock().now().to_msg()
        grid = OccupancyGrid()
        grid.header.frame_id = frame
        grid.header.stamp = stamp
        grid.info.resolution = float(self._mask.resolution_m)
        grid.info.width = self._mask.width
        grid.info.height = self._mask.height
        grid.info.origin.position.x = float(self._mask.origin_x_m)
        grid.info.origin.position.y = float(self._mask.origin_y_m)
        grid.info.origin.orientation.w = 1.0
        grid.data = self._mask.data
        self._pub_mask.publish(grid)

        info = CostmapFilterInfo()
        info.header.frame_id = frame
        info.header.stamp = stamp
        info.type = 0            # keep-out
        info.filter_mask_topic = self._mask_topic
        info.base = 0.0
        info.multiplier = 1.0
        self._pub_info.publish(info)

    def _publish_diagnostics(self):
        status = DiagnosticStatus()
        status.name = "geofence"
        status.hardware_id = "geofence_mask_node"
        if self._fence is None:
            status.level = DiagnosticStatus.ERROR
            status.message = self._problem
        elif self._mask is None:
            status.level = DiagnosticStatus.WARN
            status.message = "fence loaded, waiting for the home position (/mavros/home_position/home)"
        else:
            status.level = DiagnosticStatus.OK
            status.message = f"'{self._fence.name}', {self._mask.area_m2 / 1e4:.1f} ha"
        status.values.append(KeyValue(key="loaded", value="1" if self._mask is not None else "0"))
        if self._fence is not None:
            status.values.append(KeyValue(key="name", value=self._fence.name))
        arr = DiagnosticArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        arr.status.append(status)
        self._pub_diag.publish(arr)


def main(args=None):
    rclpy.init(args=args)
    node = GeofenceMaskNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
