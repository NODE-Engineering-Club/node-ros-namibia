#!/usr/bin/env python3
"""Pure YAML/JSON waypoint-file parsing, kept free of any rclpy/ROS import
— same reasoning as src/comms/cloud_bridge/cloud_bridge/geo_utils.py: this
is what makes it testable in an environment with no ROS 2 install at all
(test/test_route_parser.py).
"""
import json
import os

import yaml


class RouteParseError(Exception):
    pass


def parse_route_file(path: str):
    """Returns (name, [(lat, lon), ...]). Raises RouteParseError with a
    human-readable reason on any problem — never returns a partially-valid
    result silently.

    Accepts either a wrapped object:
        {"name": "...", "waypoints": [[lat, lon], {"lat": .., "lon": ..}, ...]}
    or a bare top-level list of waypoints (name defaults to the filename).
    """
    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as f:
        if ext in (".yaml", ".yml"):
            data = yaml.safe_load(f)
        elif ext == ".json":
            data = json.load(f)
        else:
            raise RouteParseError(f"unsupported extension '{ext}' (use .yaml, .yml, or .json)")

    if isinstance(data, list):
        name = os.path.basename(path)
        raw_waypoints = data
    elif isinstance(data, dict):
        name = str(data.get("name", os.path.basename(path)))
        raw_waypoints = data.get("waypoints")
        if raw_waypoints is None:
            raise RouteParseError("top-level object has no 'waypoints' key")
    else:
        raise RouteParseError(f"top level must be a list or an object with 'waypoints', got {type(data).__name__}")

    if not isinstance(raw_waypoints, list) or len(raw_waypoints) == 0:
        raise RouteParseError("'waypoints' must be a non-empty list")

    waypoints = []
    errors = []
    for i, entry in enumerate(raw_waypoints):
        try:
            if isinstance(entry, dict):
                lat, lon = float(entry["lat"]), float(entry["lon"])
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                lat, lon = float(entry[0]), float(entry[1])
            else:
                raise ValueError(f"expected [lat, lon] or {{lat, lon}}, got {entry!r}")
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
                raise ValueError(f"lat/lon out of range: ({lat}, {lon})")
            waypoints.append((lat, lon))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"waypoint[{i}]: {exc}")

    if errors:
        raise RouteParseError("; ".join(errors))

    return name, waypoints
