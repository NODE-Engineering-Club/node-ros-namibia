"""The survey-area geofence, as a Nav2 keep-out mask.

ROS-free: the node that publishes the mask (``sensors/geofence_mask_node``) is a
thin wrapper around :func:`load_geofence` and :func:`rasterize`.

Why a geofence is the containment on this boat
----------------------------------------------
Nothing aboard detects obstacles (docs/HARDWARE_DECISIONS.md: no lidar, the
OAK-D-LR has no driver yet). The Nav2 survey profile
(``bringup/config/nav2_params_survey.yaml``) therefore has no obstacle layer at
all, and the one thing it is told about the world is where the boat may go: a
polygon drawn around the survey area, in latitude/longitude, for the day's
site. Everything outside it is lethal in both costmaps, so the planner will
not route through it and the controller will not follow a path into it.

It is not collision avoidance. A buoy, a moored boat or a swimmer inside the
fence is invisible to the stack. The operator keeps the boat in sight with the
transmitter in hand.

Why the mask is built at run time
---------------------------------
The ``map`` frame is anchored to the Pixhawk's home position
(``sensors/datum_sync``), which is wherever the boat was at power-up — a
different point every day. A pre-drawn mask image would be in the wrong place.
So the polygon stays geographic in its file, and the mask is rasterised once
the home position is known, in that day's ``map`` frame.

File format (YAML)::

    geofence:
      name: "Walvis Bay lagoon, line block A"
      polygon:            # [lat, lon] in decimal degrees, WGS84, in order
        - [-22.9500, 14.4800]
        - [-22.9500, 14.4900]
        - [-22.9580, 14.4900]
        - [-22.9580, 14.4800]
"""

from __future__ import annotations

import math
import hashlib
from dataclasses import dataclass

from .geo import LocalOrigin

#: Mask value for "the boat may not be here": Nav2's KeepoutFilter treats an
#: OccupancyGrid value of 100 as lethal.
LETHAL = 100
FREE = 0

#: Polygons beyond these limits are almost certainly a typo (a swapped lat/lon,
#: a missing minus sign) rather than a survey area.
MAX_SPAN_M = 20_000.0
MIN_AREA_M2 = 100.0

#: The mask is capped at this many cells; a larger fence gets a coarser grid
#: rather than an out-of-memory node.
MAX_CELLS = 4_000_000


class GeofenceError(ValueError):
    """The geofence file cannot be used. The message says why, for a human."""


@dataclass(frozen=True)
class Geofence:
    name: str
    #: [(lat, lon), ...], not closed (the last point is not the first).
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Mask:
    """A row-major OccupancyGrid payload, origin at the bottom-left cell corner."""

    resolution_m: float
    origin_x_m: float
    origin_y_m: float
    width: int
    height: int
    data: list[int]
    area_m2: float


def load_geofence(document: dict) -> Geofence:
    """Validate a parsed geofence YAML document. Raises :class:`GeofenceError`."""
    if not isinstance(document, dict) or not isinstance(document.get("geofence"), dict):
        raise GeofenceError("the file has no 'geofence:' section")
    section = document["geofence"]
    raw = section.get("polygon")
    if not isinstance(raw, list) or len(raw) < 3:
        raise GeofenceError("'polygon' needs at least three [lat, lon] points")

    points = []
    for i, entry in enumerate(raw):
        try:
            lat, lon = (float(v) for v in entry)
        except (TypeError, ValueError):
            raise GeofenceError(f"point {i + 1} is not a [lat, lon] pair: {entry!r}") from None
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            raise GeofenceError(f"point {i + 1} is not a valid latitude/longitude: {entry!r}")
        points.append((lat, lon))
    if points[0] == points[-1]:
        points.pop()          # closed by hand; we close it ourselves
    if len(points) < 3:
        raise GeofenceError("'polygon' needs at least three distinct points")

    fence = Geofence(name=str(section.get("name") or "geofence"), polygon=tuple(points))
    origin = LocalOrigin(*points[0])
    enu = [origin.to_enu(lat, lon) for lat, lon in points]
    xs, ys = [p[0] for p in enu], [p[1] for p in enu]
    if max(xs) - min(xs) > MAX_SPAN_M or max(ys) - min(ys) > MAX_SPAN_M:
        raise GeofenceError(
            f"the polygon spans more than {MAX_SPAN_M / 1000:.0f} km — check for a "
            "swapped latitude/longitude or a missing minus sign")
    # Crossing first: a symmetric bow-tie has zero signed area, and "crosses
    # itself" is the diagnosis that tells somebody what to fix.
    if _self_intersects(enu):
        raise GeofenceError("the polygon crosses itself — list the corners in order around it")
    if polygon_area(enu) < MIN_AREA_M2:
        raise GeofenceError("the polygon has (almost) no area — are the points in order?")
    return fence


def polygon_area(points: list[tuple[float, float]]) -> float:
    """Shoelace area, square metres, of an ENU polygon."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def _self_intersects(points) -> bool:
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if abs(i - j) in (1, n - 1):
                continue        # neighbours share a vertex
            if _segments_cross(*edges[i], *edges[j]):
                return True
    return False


def contains(polygon_enu: list[tuple[float, float]], x: float, y: float) -> bool:
    """Even-odd point-in-polygon, ENU metres."""
    inside = False
    n = len(polygon_enu)
    for i in range(n):
        x1, y1 = polygon_enu[i]
        x2, y2 = polygon_enu[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


def signed_distance_m(polygon_latlon, lat: float, lon: float) -> float:
    """Metres from the point to the fence edge: positive inside, negative outside.

    ``polygon_latlon`` is [(lat, lon), ...] as in :class:`Geofence`. Computed
    in a local ENU frame at the point itself, which is exact enough at the
    size of a survey block (MAX_SPAN_M).
    """
    origin = LocalOrigin(lat, lon)
    enu = [origin.to_enu(p_lat, p_lon) for p_lat, p_lon in polygon_latlon]
    nearest = math.inf
    n = len(enu)
    for i in range(n):
        (x1, y1), (x2, y2) = enu[i], enu[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length2))
        nearest = min(nearest, math.hypot(x1 + t * dx, y1 + t * dy))
    return nearest if contains(enu, 0.0, 0.0) else -nearest


def to_map_frame(fence: Geofence, origin_lat: float, origin_lon: float):
    """The polygon in the ``map`` frame, metres east/north of the datum."""
    origin = LocalOrigin(origin_lat, origin_lon)
    return [origin.to_enu(lat, lon) for lat, lon in fence.polygon]


def rasterize(polygon_enu: list[tuple[float, float]], resolution_m: float = 1.0,
              margin_m: float = 30.0) -> Mask:
    """Free inside the polygon, lethal outside it, over the bounding box + margin.

    The margin is a lethal ring around the fence: beyond the mask's edge Nav2
    applies no keep-out at all, so the ring is what stops a path that would
    leave the fence and come back. Keep it wider than the inflation radius.

    Scanline fill: each row's crossings with the polygon edges give the free
    spans directly, so the cost is rows x edges, not cells x edges.
    """
    xs = [p[0] for p in polygon_enu]
    ys = [p[1] for p in polygon_enu]
    min_x, max_x = min(xs) - margin_m, max(xs) + margin_m
    min_y, max_y = min(ys) - margin_m, max(ys) + margin_m

    res = float(resolution_m)
    while math.ceil((max_x - min_x) / res) * math.ceil((max_y - min_y) / res) > MAX_CELLS:
        res *= 2.0
    width = int(math.ceil((max_x - min_x) / res))
    height = int(math.ceil((max_y - min_y) / res))

    data = [LETHAL] * (width * height)
    n = len(polygon_enu)
    for row in range(height):
        y = min_y + (row + 0.5) * res          # cell centre
        crossings = []
        for i in range(n):
            x1, y1 = polygon_enu[i]
            x2, y2 = polygon_enu[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        crossings.sort()
        for a, b in zip(crossings[0::2], crossings[1::2]):
            # cells whose centre lies in [a, b)
            first = max(0, int(math.ceil((a - min_x) / res - 0.5)))
            last = min(width - 1, int(math.ceil((b - min_x) / res - 0.5)) - 1)
            base = row * width
            for col in range(first, last + 1):
                data[base + col] = FREE

    return Mask(
        resolution_m=res, origin_x_m=min_x, origin_y_m=min_y,
        width=width, height=height, data=data, area_m2=polygon_area(polygon_enu),
    )


# -- a fence that follows the mission ------------------------------------------

#: Clear water kept around the route and the boat by an automatic fence.
ROUTE_FENCE_MARGIN_M = 60.0
#: The boat drives straight to waypoint 1 and sees no obstacles, so a start
#: from further away than this is refused: drive closer in Manual first.
MAX_APPROACH_M = 1000.0


def fence_token(polygon) -> str:
    """Identity of a polygon: how the GUI knows the mask node has applied it."""
    canon = ";".join(f"{lat:.7f},{lon:.7f}" for lat, lon in polygon)
    return hashlib.sha1(canon.encode()).hexdigest()[:12]


def _convex_hull(points):
    """Andrew's monotone chain. Anticlockwise, no repeated point."""
    pts = sorted(set(points))

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (
                (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])
            ) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]

    return half(pts) + half(reversed(pts))


def route_fence_polygon(points, margin_m: float = ROUTE_FENCE_MARGIN_M):
    """[(lat, lon), ...]: the hull of ``points`` with ``margin_m`` all round."""
    origin = LocalOrigin(*points[0])
    octagon = [(margin_m * math.cos(math.pi * k / 4), margin_m * math.sin(math.pi * k / 4))
               for k in range(8)]
    grown = []
    for lat, lon in points:
        east, north = origin.to_enu(lat, lon)
        grown.extend((east + dx, north + dy) for dx, dy in octagon)
    return [origin.to_geo(east, north) for east, north in _convex_hull(grown)]


def plan_route_fence(current: dict | None, waypoints, boat, name: str = "mission"):
    """The fence a mission needs: ``(document_to_write | None, token, problem)``.

    ``current`` is the parsed geofence file. A file marked ``auto: true`` (or
    none) follows the mission. A hand-written fence is the operator's limit
    and is never replaced: a route leaving it is refused.
    """
    points = [(float(w["lat"]), float(w["lon"])) for w in waypoints]
    if not points:
        return None, None, "the route has no waypoints"
    east, north = LocalOrigin(*boat).to_enu(*points[0])
    distance = math.hypot(east, north)
    if distance > MAX_APPROACH_M:
        return None, None, (
            f"the boat is {distance:.0f} m from waypoint 1 (limit {MAX_APPROACH_M:.0f} m). "
            "Drive closer in Manual first.")
    everything = [tuple(boat), *points]

    section = current.get("geofence") if isinstance(current, dict) else None
    if isinstance(section, dict) and not section.get("auto"):
        try:
            fence = load_geofence(current)
        except GeofenceError as exc:
            return None, None, f"the geofence file is not usable: {exc}"
        outside = sum(signed_distance_m(fence.polygon, lat, lon) <= 0 for lat, lon in everything)
        if outside:
            return None, None, (
                f"{outside} point(s) of this mission or the boat are outside the hand-written "
                f"geofence '{fence.name}'. Edit it, or add 'auto: true' to let it follow missions.")
        return None, fence_token(fence.polygon), None

    polygon = route_fence_polygon(everything)
    document = {"geofence": {
        "name": f"Auto: {name} + {ROUTE_FENCE_MARGIN_M:.0f} m"[:80],
        "auto": True,
        "polygon": [[round(lat, 7), round(lon, 7)] for lat, lon in polygon],
    }}
    try:
        fence = load_geofence(document)
    except GeofenceError as exc:
        return None, None, f"no fence can be drawn round this mission: {exc}"
    return document, fence_token(fence.polygon), None
