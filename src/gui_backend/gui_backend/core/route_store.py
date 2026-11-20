"""Mission routes kept on the GUI server: the Repo screen's library.

A route is a named list of WGS84 waypoints, uploaded as JSON from the browser
(the redesign's ``asket-mission`` export format). They live as one file each in
a folder on the machine that runs gui_backend, so every laptop connected to it
sees the same list, and a route uploaded yesterday is still there today.

Deploying a route sends its waypoints to ``mission_manager``'s
``/mission/start``. What was last deployed is recorded here too
(``_deployed.json``), because ``/mission/status`` reports *progress* — the
index of the waypoint being steered to — and nothing about *which* waypoints.
Drawing "waypoint 3 of 8 passed" needs the 8, and the 8 must be the ones that
were actually sent, not whatever is open in the browser.

The deployment record says what this server sent. Whether the vessel accepted
and is following it is only ever read from ``/mission/status``.

Validation is strict and the reasons are sentences: a file that would put a
waypoint at latitude 140 must be refused here, with the waypoint named, and not
discovered by a navigation stack on the water.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from asket_common.setup_profile import default_setup_path

#: More than any survey this boat runs; a bound so a bad file cannot fill a disk
#: or a service request.
MAX_WAYPOINTS = 1000
MAX_NAME_LEN = 80
DEPLOYED_FILE = "_deployed.json"
SEEDED_FILE = "_seeded.json"
FORMAT = "asket-mission"


class RouteError(ValueError):
    """A route that will not be stored or deployed, and why, in words."""


def default_routes_dir() -> Path:
    """``ASKET_ROUTES_DIR``, else a folder next to the setup file.

    Next to the setup file because that is already the place on the Jetson
    that survives a container restart (``ASKET_SETUP_FILE`` points into the
    persistent volume), and routes need the same lifetime.
    """
    override = os.environ.get("ASKET_ROUTES_DIR")
    if override:
        return Path(override)
    return default_setup_path().parent / "asket_routes"


def default_seed_dir() -> Path | None:
    """Routes shipped with the package (``config/routes/*.json``): the
    Shearwater Bay plot loops, so Repo is not empty on a fresh install.
    ``ASKET_ROUTE_SEEDS`` overrides; installed share dir, else source tree."""
    override = os.environ.get("ASKET_ROUTE_SEEDS")
    if override:
        return Path(override)
    try:
        from ament_index_python.packages import get_package_share_directory
        share = Path(get_package_share_directory("gui_backend")) / "config" / "routes"
        if share.is_dir():
            return share
    except Exception:   # no ROS, or the package is not installed
        pass
    source = Path(__file__).resolve().parents[2] / "config" / "routes"
    return source if source.is_dir() else None


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40] or "route"


def fingerprint(waypoints: list[dict]) -> str:
    """Identity of a route's geometry: same points in the same order."""
    canon = json.dumps(
        [[round(float(w["lat"]), 7), round(float(w["lon"]), 7)] for w in waypoints],
        separators=(",", ":"),
    )
    return hashlib.sha1(canon.encode()).hexdigest()[:12]


def validate(doc: dict) -> dict:
    """Return the route to store, or raise :class:`RouteError`."""
    if not isinstance(doc, dict):
        raise RouteError("the file is not a JSON object")
    name = str(doc.get("name") or "").strip()
    if not name:
        raise RouteError("the route has no name")
    if len(name) > MAX_NAME_LEN:
        raise RouteError(f"the name is longer than {MAX_NAME_LEN} characters")
    raw = doc.get("waypoints")
    if not isinstance(raw, list) or not raw:
        raise RouteError("the route has no waypoints")
    if len(raw) > MAX_WAYPOINTS:
        raise RouteError(f"{len(raw)} waypoints; the limit is {MAX_WAYPOINTS}")

    waypoints = []
    for i, w in enumerate(raw, start=1):
        try:
            lat = float(w["lat"])
            lon = float(w["lon"])
        except (KeyError, TypeError, ValueError):
            raise RouteError(f"waypoint {i} has no numeric lat/lon") from None
        if not (math.isfinite(lat) and -90.0 <= lat <= 90.0):
            raise RouteError(f"waypoint {i}: latitude {lat} is outside -90..90")
        if not (math.isfinite(lon) and -180.0 <= lon <= 180.0):
            raise RouteError(f"waypoint {i}: longitude {lon} is outside -180..180")
        waypoints.append({"lat": lat, "lon": lon})

    speed = doc.get("cruise_speed_ms")
    try:
        speed = float(speed) if speed is not None else None
    except (TypeError, ValueError):
        speed = None
    if speed is not None and not (math.isfinite(speed) and speed > 0):
        speed = None

    return {
        "format": FORMAT,
        "version": 1,
        "name": name,
        "crs": "WGS84",
        "cruise_speed_ms": speed,
        "obstacle_policy": "avoid" if doc.get("obstacle_policy") == "avoid" else "stop",
        "sensors": doc.get("sensors") if isinstance(doc.get("sensors"), dict) else {},
        "waypoints": waypoints,
        "file_name": str(doc.get("file_name") or "")[:200],
    }


class RouteStore:
    """One JSON file per route in ``root``."""

    def __init__(self, root: Path | str | None = None, clock=None) -> None:
        self.root = Path(root) if root is not None else default_routes_dir()
        self._clock = clock or (lambda: int(time.time() * 1000))

    # -- files ---------------------------------------------------------------

    def _path(self, route_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,64}", route_id or ""):
            raise RouteError(f"not a route id: {route_id!r}")
        return self.root / f"{route_id}.json"

    def _write(self, path: Path, doc: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        tmp.replace(path)   # atomic: a reader never sees half a route

    # -- library -------------------------------------------------------------

    def list(self) -> list[dict]:
        """Every stored route, newest first, waypoints included.

        Whole documents because the Repo draws each course and its length;
        a thousand-waypoint route is ~40 kB, and this is an HTTP call made
        when the page opens, not a stream.
        """
        out = []
        if not self.root.is_dir():
            return out
        for path in self.root.glob("*.json"):
            if path.name.startswith("_"):    # _deployed.json, _seeded.json
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                out.append({"id": path.stem, "name": path.stem, "error": "unreadable file"})
                continue
            out.append({**doc, "id": doc.get("id") or path.stem})
        out.sort(key=lambda r: r.get("saved_utc_ms") or 0, reverse=True)
        return out

    def get(self, route_id: str) -> dict:
        path = self._path(route_id)
        if not path.is_file():
            raise RouteError(f"no route {route_id!r}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RouteError(f"route {route_id!r} cannot be read: {exc}") from None

    def save(self, doc: dict, route_id: str | None = None) -> dict:
        """Store a route. With ``route_id``, replace that route."""
        route = validate(doc)
        route["fingerprint"] = fingerprint(route["waypoints"])
        if route_id:
            if not self._path(route_id).is_file():
                raise RouteError(f"no route {route_id!r}")
        else:
            route_id = f"{_slug(route['name'])}-{os.urandom(3).hex()}"
        route["id"] = route_id
        route["saved_utc_ms"] = self._clock()
        self._write(self._path(route_id), route)
        return route

    def delete(self, route_id: str) -> None:
        path = self._path(route_id)
        if not path.is_file():
            raise RouteError(f"no route {route_id!r}")
        path.unlink()

    # -- shipped routes ----------------------------------------------------------

    def seed(self, seed_dir: Path | str | None) -> list[str]:
        """Copy shipped routes into the library, each one once.

        A seed is added under its own ``id`` only if it was never seeded here
        before (``_seeded.json``), so a route the operator edited is never
        overwritten and one they deleted does not come back on restart.
        Returns the ids added. A bad seed file is skipped, not fatal.
        """
        if seed_dir is None or not Path(seed_dir).is_dir():
            return []
        marker = self.root / SEEDED_FILE
        try:
            done = set(json.loads(marker.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            done = set()
        added = []
        for path in sorted(Path(seed_dir).glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                route_id = doc.get("id") or path.stem
                if route_id in done:
                    continue
                target = self._path(route_id)
                if not target.is_file():
                    route = validate(doc)
                    route.update(id=route_id, fingerprint=fingerprint(route["waypoints"]),
                                 saved_utc_ms=self._clock())
                    self._write(target, route)
                    added.append(route_id)
                done.add(route_id)
            except (OSError, ValueError):
                continue
        if added or not marker.is_file():
            self._write(marker, sorted(done))
        return added

    # -- deployment ------------------------------------------------------------

    def record_deployment(self, route: dict, utc_ms: int) -> dict:
        record = {
            "route_id": route.get("id"),
            "name": route.get("name"),
            "fingerprint": route.get("fingerprint") or fingerprint(route["waypoints"]),
            "waypoint_count": len(route["waypoints"]),
            "waypoints": route["waypoints"],
            "deployed_utc_ms": utc_ms,
        }
        self._write(self.root / DEPLOYED_FILE, record)
        return record

    def deployed(self) -> dict | None:
        """What this server last sent to ``/mission/start``, or ``None``."""
        path = self.root / DEPLOYED_FILE
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def deployed_summary(self) -> dict | None:
        rec = self.deployed()
        if not rec:
            return None
        return {k: v for k, v in rec.items() if k != "waypoints"}
