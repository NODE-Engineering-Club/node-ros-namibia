"""Small-distance geographic offset helpers (equirectangular approximation).

Valid for offsets on the order of tens of metres — the error from ignoring
Earth's ellipsoidal shape is negligible at that scale.
"""
import math

EARTH_RADIUS_M = 6378137.0  # WGS84 equatorial radius


def offset_latlon(lat_deg, lon_deg, north_m=0.0, east_m=0.0):
    """Return (lat, lon) shifted by north_m/east_m metres from (lat_deg, lon_deg)."""
    lat_rad = math.radians(lat_deg)
    dlat = north_m / EARTH_RADIUS_M
    dlon = east_m / (EARTH_RADIUS_M * math.cos(lat_rad))
    return lat_deg + math.degrees(dlat), lon_deg + math.degrees(dlon)


def step_towards(from_x, from_y, to_x, to_y, max_step_m):
    """The next goal on the way to (to_x, to_y): ``(x, y, is_intermediate)``.

    Nav2's global costmap is a rolling 150 m square, so a goal more than 75 m
    away is off the map and cannot be planned to. A long leg is sailed in steps.
    """
    dx, dy = to_x - from_x, to_y - from_y
    distance = math.hypot(dx, dy)
    if distance <= max_step_m:
        return to_x, to_y, False
    scale = max_step_m / distance
    return from_x + dx * scale, from_y + dy * scale, True
