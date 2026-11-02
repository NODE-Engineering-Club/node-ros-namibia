#!/usr/bin/env python3
"""Loads a human-authored YAML/JSON waypoint file and publishes it as a
njord_msgs/PlannedRoute for display — a route line on the map for a human
pilot to drive along, NOT an autonomous mission. Nothing here calls
/mission/start or touches Nav2; this node only ever reads a file and
publishes a message. (Feeding the same parsed waypoint list into
mission_manager's StartMission service for autonomous execution later is a
one-line addition elsewhere — deliberately not done here, see
PlannedRoute.msg's own comment.)

Expected file format (YAML or JSON, by extension):

    name: "Survey lane 1"          # optional; defaults to the filename
    waypoints:
      - [-26.6385, 15.1528]        # [lat, lon] ...
      - {lat: -26.6390, lon: 15.1532}   # ... or {lat, lon} — both accepted
      - [-26.6395, 15.1529]

A bare top-level list (no "name"/"waypoints" wrapper) is also accepted:

    - [-26.6385, 15.1528]
    - [-26.6390, 15.1532]

Workflow: point `route_file` at a path (absolute, or `~` is expanded), drop
or edit that file at any time — this node polls its mtime and re-publishes
automatically. A malformed edit is logged loudly and the PREVIOUS valid
route keeps publishing (keeps being shown on the map) rather than being
cleared to nothing — a typo mid-operation shouldn't make the pilot's
reference line vanish.
"""
import os

import rclpy
from geographic_msgs.msg import GeoPoint
from njord_msgs.msg import PlannedRoute
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import Trigger

from mission.route_parser import RouteParseError, parse_route_file


class WaypointRouteLoaderNode(Node):
    def __init__(self):
        super().__init__("waypoint_route_loader")

        self.declare_parameter("route_file", "")
        self.declare_parameter("poll_interval_sec", 2.0)

        self._route_file = os.path.expanduser(self.get_parameter("route_file").value or "")
        self._last_mtime = None
        self._have_published_once = False

        route_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL, depth=1,
        )
        self.pub = self.create_publisher(PlannedRoute, "/mission/planned_route", route_qos)

        self.create_service(Trigger, "/mission/reload_planned_route", self._reload_cb)

        poll_sec = self.get_parameter("poll_interval_sec").get_parameter_value().double_value
        self.create_timer(poll_sec, self._poll)

        if not self._route_file:
            self.get_logger().warn(
                "No route_file configured — set the 'route_file' parameter to a "
                ".yaml/.yml/.json waypoint file. Waiting idle until it's set and reloaded "
                "(ros2 param set /waypoint_route_loader route_file <path>, then call "
                "/mission/reload_planned_route)."
            )
        else:
            self._try_load(force=True)

    def _reload_cb(self, request, response):
        self._route_file = os.path.expanduser(self.get_parameter("route_file").value or "")
        ok, detail = self._try_load(force=True)
        response.success = ok
        response.message = detail
        return response

    def _poll(self):
        if not self._route_file or not os.path.exists(self._route_file):
            return
        mtime = os.path.getmtime(self._route_file)
        if mtime == self._last_mtime:
            return
        self._try_load(force=False)

    def _try_load(self, force: bool):
        if not self._route_file:
            return False, "no route_file configured"
        if not os.path.exists(self._route_file):
            if force:
                self.get_logger().error(f"route_file does not exist: {self._route_file}")
            return False, f"file not found: {self._route_file}"

        try:
            name, waypoints = parse_route_file(self._route_file)
        except RouteParseError as exc:
            self.get_logger().error(
                f"Failed to parse {self._route_file}: {exc}. "
                + ("Keeping the previously published route." if self._have_published_once else "Nothing published yet.")
            )
            return False, str(exc)
        except OSError as exc:
            self.get_logger().error(f"Could not read {self._route_file}: {exc}")
            return False, str(exc)

        self._last_mtime = os.path.getmtime(self._route_file)

        msg = PlannedRoute()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = name
        msg.source_path = self._route_file
        msg.waypoints = [GeoPoint(latitude=lat, longitude=lon, altitude=0.0) for lat, lon in waypoints]
        self.pub.publish(msg)
        self._have_published_once = True

        self.get_logger().info(f"Published planned route '{name}': {len(waypoints)} waypoint(s) from {self._route_file}")
        return True, f"loaded {len(waypoints)} waypoint(s)"


def main(args=None):
    rclpy.init(args=args)
    node = WaypointRouteLoaderNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
