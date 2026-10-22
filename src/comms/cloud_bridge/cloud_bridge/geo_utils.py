#!/usr/bin/env python3
"""Pure logic used by cloud_telemetry_node.py and sonar_bridge_node.py,
deliberately kept free of any rclpy/ROS message imports so it — and its
tests in test/test_telemetry_math.py and test/test_sonar_math.py — can run
anywhere Python runs, not just inside a ROS 2 workspace. This environment
has no ROS 2 install at all, so this split is what makes any of this
package's logic actually testable here, not just syntax-checkable.
"""
import math

WGS84_A = 6378137.0  # earth equatorial radius (m) — local-tangent-plane approximation,
                      # same constant and formula as fusion/fusion/geo_fusion_node.py's _to_geo()

# geo_fusion_tracker_node's real output labels -> the cloud payload schema's
# enum. Anything not listed falls back to FLOTSAM rather than failing
# schema validation or silently passing an internal name the dashboard
# doesn't recognise.
CLASS_MAP = {
    "KELP_CANOPY": "KELP_CANOPY",
    "BUOY_OBSTACLE": "BUOY",
    "unknown": "FLOTSAM",
}

# mavros_msgs/GPSRAW.fix_type (mirrors MAVLink GPS_FIX_TYPE) -> the cloud
# payload schema's nav.rtk_mode enum (0 none, 1 single, 2 float, 3 fixed).
FIX_NO_GPS = 0
FIX_NO_FIX = 1
FIX_2D = 2
FIX_3D = 3
FIX_DGPS = 4
FIX_RTK_FLOAT = 5
FIX_RTK_FIXED = 6


def map_rtk_mode(fix_type: int) -> int:
    if fix_type >= FIX_RTK_FIXED:
        return 3
    if fix_type == FIX_RTK_FLOAT:
        return 2
    if fix_type >= FIX_2D:
        return 1
    return 0


def parse_class_and_id(class_id: str):
    """'KELP_CANOPY:7' -> ('KELP_CANOPY', 7). Falls back to (class_id, 0) if
    there's no ':' suffix (e.g. a message from a node that hasn't adopted
    the "<CLASS>:<id>" convention yet) rather than raising."""
    if ":" in class_id:
        name, _, id_str = class_id.rpartition(":")
        try:
            return name, int(id_str)
        except ValueError:
            return class_id, 0
    return class_id, 0


def enu_offset_to_latlon(east_m: float, north_m: float, lat0: float, lon0: float):
    """Local-tangent-plane approximation, same formula as the already-
    tested fusion/fusion/geo_fusion_node.py's _to_geo()."""
    lat = lat0 + math.degrees(north_m / WGS84_A)
    lon = lon0 + math.degrees(east_m / (WGS84_A * math.cos(math.radians(lat0))))
    return lat, lon


def peak_hold_decimate(samples, n_bins: int = 100, window: int = 6):
    """600(ish) u16 samples -> n_bins u8 bins, max-of-`window` per bin,
    scaled down by 256 and clamped to 255 (Omniscan peak-hold waterfall
    decimation, design note §3.4)."""
    out = []
    for k in range(n_bins):
        chunk = samples[k * window:(k + 1) * window]
        if not chunk:
            out.append(0)
            continue
        out.append(min(255, max(v // 256 for v in chunk)))
    return out
