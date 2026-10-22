#!/usr/bin/env python3
"""Rasterizes tracked kelp-canopy footprints into a Nav2 KeepOutFilter mask.

Fixes a real wiring bug in the original design note: nav2_costmap_2d's
KeepOutFilter layer reads its no-go mask from a `nav_msgs/OccupancyGrid`
(named via a `nav2_msgs/CostmapFilterInfo` message on `filter_info_topic`),
not from a `geometry_msgs/PolygonStamped`. Pointing the layer at a
PolygonStamped-typed topic does not raise an error — ROS 2 topics only
connect pub/sub pairs with matching types — it just means the layer's
subscription never receives anything from this pipeline, and the kelp
keep-out zone silently never engages. That is exactly the kind of failure
that looks fine in logs and only shows up as "the boat drove straight
through the canopy" in the water.

This node is the missing piece: it subscribes to geo_fusion_tracker_node's
per-detection `/fusion/canopy_polygon` events, keeps a short-lived set of
currently-active canopy footprints (new/refreshed polygons replace the
nearest existing entry within `match_radius_m`; entries not refreshed
within `ttl_sec` are dropped), and on a timer rasterizes all of them into
one `nav_msgs/OccupancyGrid` on `/fusion/canopy_mask`, with a matching
`nav2_msgs/CostmapFilterInfo` published once (transient-local) on
`/costmap_filter_info` so KeepOutFilter can discover the mask topic.

CAVEAT, stated plainly rather than hidden: the exact declared-parameter
names on `nav2_costmap_2d::KeepOutFilter` were not verified against the
live Nav2 Jazzy source in this environment (no network/source access here).
This wiring matches the documented Costmap Filters pattern as best known;
confirm the parameter names against your installed `nav2_costmap_2d` before
trusting the keep-out zone in the water.
"""
import time

import numpy as np
import rclpy
from geometry_msgs.msg import PolygonStamped
from nav2_msgs.msg import CostmapFilterInfo
from nav_msgs.msg import MapMetaData, OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile

_LETHAL = 100
_UNKNOWN = -1


class _Footprint:
    __slots__ = ("cx", "cy", "min_x", "min_y", "max_x", "max_y", "last_seen")

    def __init__(self, points, now):
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        self.min_x, self.max_x = min(xs), max(xs)
        self.min_y, self.max_y = min(ys), max(ys)
        self.cx = (self.min_x + self.max_x) / 2.0
        self.cy = (self.min_y + self.max_y) / 2.0
        self.last_seen = now


class KelpKeepoutCostmapNode(Node):
    def __init__(self):
        super().__init__("kelp_keepout_costmap_node")

        self.declare_parameter("map_frame", "map")
        self.declare_parameter("resolution_m", 0.25)
        self.declare_parameter("max_window_m", 200.0)
        self.declare_parameter("padding_m", 10.0)
        self.declare_parameter("match_radius_m", 3.0)
        self.declare_parameter("ttl_sec", 6.0)
        self.declare_parameter("publish_period_sec", 0.5)
        self.declare_parameter("mask_topic", "/fusion/canopy_mask")
        self.declare_parameter("filter_info_topic", "/costmap_filter_info")

        self._map_frame = self.get_parameter("map_frame").value
        self._resolution = float(self.get_parameter("resolution_m").value)
        self._max_window = float(self.get_parameter("max_window_m").value)
        self._padding = float(self.get_parameter("padding_m").value)
        self._match_radius = float(self.get_parameter("match_radius_m").value)
        self._ttl = float(self.get_parameter("ttl_sec").value)
        self._mask_topic = self.get_parameter("mask_topic").value

        self._footprints: list[_Footprint] = []

        self.create_subscription(PolygonStamped, "/fusion/canopy_polygon", self._polygon_cb, 10)
        self.pub_mask = self.create_publisher(OccupancyGrid, self._mask_topic, 10)

        info_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_info = self.create_publisher(CostmapFilterInfo, self.get_parameter("filter_info_topic").value, info_qos)
        self._publish_filter_info()

        period = float(self.get_parameter("publish_period_sec").value)
        self.create_timer(period, self._publish_mask)

        self.get_logger().info(
            f"kelp_keepout_costmap_node ready: mask={self._mask_topic}, "
            f"resolution={self._resolution}m, ttl={self._ttl}s."
        )

    def _publish_filter_info(self):
        info = CostmapFilterInfo()
        info.header.frame_id = self._map_frame
        info.header.stamp = self.get_clock().now().to_msg()
        info.type = 0  # binary keep-out mask (see module docstring caveat)
        info.base = 0.0
        info.multiplier = 1.0
        info.filter_mask_topic = self._mask_topic
        self.pub_info.publish(info)

    def _polygon_cb(self, msg: PolygonStamped):
        if not msg.polygon.points:
            return
        now = time.monotonic()
        fp = _Footprint(msg.polygon.points, now)

        for existing in self._footprints:
            if abs(existing.cx - fp.cx) <= self._match_radius and abs(existing.cy - fp.cy) <= self._match_radius:
                existing.min_x, existing.max_x = fp.min_x, fp.max_x
                existing.min_y, existing.max_y = fp.min_y, fp.max_y
                existing.cx, existing.cy = fp.cx, fp.cy
                existing.last_seen = now
                return

        self._footprints.append(fp)

    def _publish_mask(self):
        now = time.monotonic()
        self._footprints = [f for f in self._footprints if (now - f.last_seen) <= self._ttl]

        grid = OccupancyGrid()
        grid.header.frame_id = self._map_frame
        grid.header.stamp = self.get_clock().now().to_msg()

        if not self._footprints:
            # Nothing active: publish a minimal, all-unknown (no-op) grid so
            # a previously-published lethal area is correctly cleared rather
            # than left stale in whatever the layer last received.
            grid.info = MapMetaData()
            grid.info.resolution = self._resolution
            grid.info.width = 4
            grid.info.height = 4
            grid.info.origin.position.x = 0.0
            grid.info.origin.position.y = 0.0
            grid.data = [_UNKNOWN] * 16
            self.pub_mask.publish(grid)
            return

        min_x = min(f.min_x for f in self._footprints) - self._padding
        max_x = max(f.max_x for f in self._footprints) + self._padding
        min_y = min(f.min_y for f in self._footprints) - self._padding
        max_y = max(f.max_y for f in self._footprints) + self._padding

        width_m = min(max_x - min_x, self._max_window)
        height_m = min(max_y - min_y, self._max_window)
        width_cells = max(1, int(width_m / self._resolution))
        height_cells = max(1, int(height_m / self._resolution))

        data = np.full((height_cells, width_cells), _UNKNOWN, dtype=np.int8)
        for f in self._footprints:
            c0 = max(0, int((f.min_x - min_x) / self._resolution))
            c1 = min(width_cells, int((f.max_x - min_x) / self._resolution) + 1)
            r0 = max(0, int((f.min_y - min_y) / self._resolution))
            r1 = min(height_cells, int((f.max_y - min_y) / self._resolution) + 1)
            if c1 > c0 and r1 > r0:
                data[r0:r1, c0:c1] = _LETHAL

        grid.info = MapMetaData()
        grid.info.resolution = self._resolution
        grid.info.width = width_cells
        grid.info.height = height_cells
        grid.info.origin.position.x = min_x
        grid.info.origin.position.y = min_y
        grid.data = data.flatten().tolist()
        self.pub_mask.publish(grid)


def main(args=None):
    rclpy.init(args=args)
    node = KelpKeepoutCostmapNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
