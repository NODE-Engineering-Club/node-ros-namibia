#!/usr/bin/env python3
"""Fly a GeoJSON waypoint route in the sea-state sim and say whether the boat coped.

Runs INSIDE the ROS container, next to
  ros2 launch bringup njord.launch.py use_sim:=true world:=openSeaWorld.sdf sea_state:=<preset> ...
(scripts/sim_sea.sh starts both). It waits for GPS, Nav2 and /fromLL, sends
the route to /mission/start (the same service and mission_manager as on the
boat), then records /sim/sea/metrics and /mission/status until the mission
ends or --timeout runs out.

Output, in boat_data/sim/<route>_<sea>_<time>/:
  track.csv     every metric sample (position, SOG, speed through water,
                thrust per T200, saturation, roll, pitch, wave elevation)
  summary.txt   the verdict and the numbers behind it (also printed)

  python3 scripts/sim_sea_trial.py "Navigation mission/Navigation_towards_shore_(0 to 30).geojson" --sea rough
  python3 scripts/sim_sea_trial.py --write-geofence boat_data/sim/geofence_sea.yaml <route>...   # and exit
"""
import argparse
import csv
import json
import math
import os
import sys
import time

DATUM = (-26.636211201212554, 15.103348697313484)   # openSeaWorld.sdf spherical_coordinates


def load_route(path):
    with open(path) as f:
        gj = json.load(f)
    feats = sorted(gj["features"], key=lambda ft: int(ft["properties"].get("id", 0)))
    return [(ft["geometry"]["coordinates"][1], ft["geometry"]["coordinates"][0]) for ft in feats]


def to_enu(lat, lon):
    r = 6378137.0
    x = math.radians(lon - DATUM[1]) * r * math.cos(math.radians(DATUM[0]))
    y = math.radians(lat - DATUM[0]) * r
    return x, y


def write_geofence(path, routes, margin_m=60.0):
    lats = [p[0] for r in routes for p in r]
    lons = [p[1] for r in routes for p in r]
    dlat = margin_m / 111320.0
    dlon = margin_m / (111320.0 * math.cos(math.radians(DATUM[0])))
    s, n = min(lats) - dlat, max(lats) + dlat
    w, e = min(lons) - dlon, max(lons) + dlon
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("# SIM ONLY (scripts/sim_sea_trial.py): box round the Navigation mission routes + "
                f"{margin_m:.0f} m.\n# Never copy this to /data/setup/geofence.yaml.\n")
        f.write("geofence:\n  name: \"sim - Navigation mission box\"\n  polygon:\n")
        for lat, lon in ((n, w), (n, e), (s, e), (s, w)):
            f.write(f"    - [{lat:.7f}, {lon:.7f}]\n")


def cross_track(px, py, pts):
    best = float("inf")
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("routes", nargs="+", help="GeoJSON point files, flown one after the other")
    ap.add_argument("--sea", default="?", help="sea-state name, for the report only")
    ap.add_argument("--timeout", type=float, default=1800.0, help="sim seconds per route")
    ap.add_argument("--out", default="/workspace/boat_data/sim")
    ap.add_argument("--write-geofence", metavar="PATH")
    a = ap.parse_args()

    routes = [load_route(p) for p in a.routes]
    if a.write_geofence:
        write_geofence(a.write_geofence, routes)
        print(f"geofence written: {a.write_geofence}")
        return 0

    import rclpy
    from geographic_msgs.msg import GeoPoint
    from nav2_msgs.action import NavigateToPose
    from njord_msgs.msg import MissionStatus
    from njord_msgs.srv import StartMission
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import NavSatFix
    from std_msgs.msg import Float64MultiArray
    from robot_localization.srv import FromLL, SetDatum

    sys.path.insert(0, "/workspace/src/sim")
    from sim.sea_state_node import METRICS_FIELDS

    rclpy.init()
    node = Node("sim_sea_trial", parameter_overrides=[
        Parameter("use_sim_time", Parameter.Type.BOOL, True)])
    state = {"gps": None, "status": None, "rows": []}
    node.create_subscription(NavSatFix, "/gps_driver/gps_raw", lambda m: state.__setitem__("gps", m), 10)
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
    node.create_subscription(MissionStatus, "/mission/status", lambda m: state.__setitem__("status", m), qos)
    node.create_subscription(Float64MultiArray, "/sim/sea/metrics", lambda m: state["rows"].append(list(m.data)), 50)
    start_cli = node.create_client(StartMission, "/mission/start")
    fromll = node.create_client(FromLL, "/fromLL")
    nav = ActionClient(node, NavigateToPose, "navigate_to_pose")

    def spin_until(cond, wall_timeout, what):
        t0 = time.time()
        while time.time() - t0 < wall_timeout:
            rclpy.spin_once(node, timeout_sec=0.2)
            if cond():
                return True
        print(f"!! timed out waiting for {what}")
        return False

    print("waiting for the sim: GPS fix, /fromLL, Nav2, /mission/start ...")
    # On the boat datum_sync sets navsat's datum from the Pixhawk's home; in
    # sim there is no MAVROS, so set it to the world origin = spawn point.
    datum_cli = node.create_client(SetDatum, "/datum")
    if not spin_until(lambda: datum_cli.service_is_ready(), 180, "/datum (navsat_transform)"):
        return 2
    dreq = SetDatum.Request()
    dreq.geo_pose.position.latitude, dreq.geo_pose.position.longitude = DATUM
    dreq.geo_pose.orientation.w = 1.0
    f = datum_cli.call_async(dreq)
    rclpy.spin_until_future_complete(node, f, timeout_sec=10)
    print(f"navsat datum set to the world origin {DATUM[0]:.7f}, {DATUM[1]:.7f}")
    ok = (spin_until(lambda: state["gps"] is not None, 180, "GPS (/gps_driver/gps_raw)")
          and spin_until(lambda: fromll.service_is_ready(), 180, "/fromLL")
          and spin_until(lambda: nav.server_is_ready(), 240, "Nav2 navigate_to_pose")
          and spin_until(lambda: start_cli.service_is_ready(), 60, "/mission/start")
          and spin_until(lambda: len(state["rows"]) > 0, 60, "/sim/sea/metrics (sea_state_node)"))
    if not ok:
        return 2
    g = state["gps"]
    print(f"GPS fix {g.latitude:.7f}, {g.longitude:.7f}")
    time.sleep(5.0)   # let the EKF / datum settle

    results = []
    for path, route in zip(a.routes, routes):
        name = os.path.splitext(os.path.basename(path))[0]
        name_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        out = os.path.join(a.out, f"{name_safe}_{a.sea}_{time.strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(out, exist_ok=True)
        req = StartMission.Request()
        req.waypoints = [GeoPoint(latitude=lat, longitude=lon, altitude=0.0) for lat, lon in route]
        state["rows"].clear()
        state["status"] = None
        fut = start_cli.call_async(req)
        rclpy.spin_until_future_complete(node, fut, timeout_sec=30)
        resp = fut.result()
        print(f"\n== {name}: {len(route)} waypoints, sea '{a.sea}' -> /mission/start: "
              f"{resp.success if resp else None} {resp.message if resp else 'no answer'}")
        if not resp or not resp.success:
            results.append((name, "NOT STARTED", out))
            continue
        t_start = node.get_clock().now().nanoseconds * 1e-9
        last_print = 0.0
        final = None
        while True:
            rclpy.spin_once(node, timeout_sec=0.2)
            st = state["status"]
            t = node.get_clock().now().nanoseconds * 1e-9 - t_start
            if st is not None and st.state in (MissionStatus.SUCCEEDED, MissionStatus.FAILED, MissionStatus.ABORTED):
                final = st
                break
            if t > a.timeout:
                break
            if t - last_print > 15 and state["rows"]:
                r = dict(zip(METRICS_FIELDS, state["rows"][-1]))
                wp = f"{st.current_waypoint + 1}/{st.total_waypoints}" if st else "?"
                print(f"  t={t:6.0f}s wp {wp:>6}  SOG {r['sog']:.2f} m/s  thw {r['stw']:+.2f}  "
                      f"T {r['thrust_left_n']:5.1f}/{r['thrust_right_n']:5.1f} N  roll {r['roll_deg']:+5.1f}  "
                      f"pitch {r['pitch_deg']:+5.1f}  eta {r['eta']:+.2f} m")
                last_print = t
        results.append(summarise(name, a.sea, route, state["rows"], final, t, a.timeout, out, METRICS_FIELDS))
        if final is None or final.state != MissionStatus.SUCCEEDED:
            print("   (mission did not succeed; aborting it before the next route)")
            from std_srvs.srv import Trigger
            ab = node.create_client(Trigger, "/mission/abort")
            if ab.wait_for_service(timeout_sec=5):
                f2 = ab.call_async(Trigger.Request())
                rclpy.spin_until_future_complete(node, f2, timeout_sec=10)
            time.sleep(3)

    print("\n================ SEA TRIAL ================")
    for name, verdict, out in results:
        print(f"{verdict:<12} {name}   ({out})")
    node.destroy_node()
    rclpy.shutdown()
    return 0 if all(v == "PASS" for _, v, _ in results) else 1


def summarise(name, sea, route, rows, final, t_end, timeout, out, fields):
    with open(os.path.join(out, "track.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)
    pts = [to_enu(lat, lon) for lat, lon in route]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    if not rows:
        verdict = "NO DATA"
        lines = [f"{name}: no metrics recorded"]
    else:
        R = [dict(zip(fields, r)) for r in rows]
        xte = [cross_track(r["x"], r["y"], pts) for r in R]
        sog = [r["sog"] for r in R]
        moving = [r for r in R if abs(r["effort_x"]) > 0.05]
        sat = sum(r["saturated"] for r in moving) / max(1, len(moving))
        roll = max(abs(r["roll_deg"]) for r in R)
        pitch = max(abs(r["pitch_deg"]) for r in R)
        eta = [r["eta"] for r in R]
        dist = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(R, R[1:]))
        dur = R[-1]["t"] - R[0]["t"]
        xte_sorted = sorted(xte)
        p95 = xte_sorted[int(0.95 * (len(xte_sorted) - 1))]
        st = final.state if final else None
        done = f"{final.current_waypoint}/{final.total_waypoints}" if final else "?"
        if st == 2:
            verdict = "PASS" if p95 < 10.0 else "MARGINAL"
        elif final is None:
            verdict = "TIMEOUT"
        else:
            verdict = "FAIL"
        lines = [
            f"route        {name}  ({len(route)} waypoints, {length:.0f} m)",
            f"sea state    {sea}",
            f"mission      {['IDLE','RUNNING','SUCCEEDED','FAILED','ABORTED'][st] if st is not None else 'no end state (timeout ' + str(int(timeout)) + ' s)'}"
            f"  waypoint {done}  {final.message if final else ''}",
            f"time         {dur:.0f} s sim  (calm-water estimate at 0.8 m/s: {length / 0.8:.0f} s)",
            f"distance     {dist:.0f} m sailed for {length:.0f} m of route",
            f"speed        SOG mean {sum(sog) / len(sog):.2f} m/s, max {max(sog):.2f}",
            f"cross-track  mean {sum(xte) / len(xte):.1f} m, 95% {p95:.1f} m, max {max(xte):.1f} m",
            f"thrust       saturated {100 * sat:.0f}% of the time under way "
            f"(T200 limit {max(max(abs(r['thrust_left_n']), abs(r['thrust_right_n'])) for r in R):.0f} N reached)",
            f"motion       roll max {roll:.0f} deg, pitch max {pitch:.0f} deg, "
            f"wave height seen {max(eta) - min(eta):.1f} m (crest to trough)",
            f"VERDICT      {verdict}",
        ]
        if sat > 0.5:
            lines.append("note         thrusters flat out most of the time: little margin left for this sea")
        if roll > 25:
            lines.append("note         roll over 25 deg: check stability / capsize margin on the real hull")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(text + "\n")
    return (name, verdict, out)


if __name__ == "__main__":
    sys.exit(main())
