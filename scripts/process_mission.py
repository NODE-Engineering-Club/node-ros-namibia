#!/usr/bin/env python3
"""Merge a mission's sonar recordings with its black-box GPS into a
georeferenced point cloud you can inspect in any 3D viewer.

Inputs (both written on the boat):
  * SonarView recordings  boat_data/sonarview/Omniscan 3D/*.svlz
    (gzip of a Ping-Protocol stream: OS3D_POINT_SET 3104 + ATTITUDE_REPORT 504 ...)
  * black-box MCAPs       boat_data/blackbox/<session>/*.mcap
    (/mavros/global_position/raw/fix + /mavros/global_position/compass_hdg)

SonarView records no position (session_platform is empty), so every ping is
placed by interpolating the GPS track at the ping's utc_msec. Both clocks are
the Jetson's.

Orientation comes from the sonar's OWN gravity vector (ATTITUDE_REPORT up_vec),
not from config/mounting.yaml, which is unmeasured. Heading comes from the
autopilot compass. The two things this script cannot know are:
  --mount-yaw  device x-axis relative to the bow, degrees clockwise (default 0)
  --flip-angle mirror the beam angle sign (decides which fan edge is "up")
By default the angle sign is picked automatically: the one that puts fewer
strong returns above the water surface.

Outputs, per recording, in --out:
  <name>_cloud.ply     binary PLY, x=east y=north z=up (m, from home), intensity,
                       depth-coloured: CloudCompare / MeshLab / Blender
  <name>_seabed.ply    the same, filtered to a running seabed estimate
  <name>_seabed.csv.gz lat, lon, depth_m, power, ping, utc_ms (for GIS)
  <name>_track.csv     GPS track during the recording
  <name>_preview.png   plan view + depth profile, for a quick look
  report.json          numbers behind the quality summary

Usage (on the Jetson host):
  pip3 install --user mcap mcap-ros2-support
  python3 scripts/process_mission.py --date 2026-11-17
"""
from __future__ import annotations

import argparse
import bisect
import glob
import gzip
import json
import math
import os
import struct
import sys
from datetime import datetime, timezone

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src", "omniscan_bridge"))
from omniscan_bridge.core.ping_protocol import (  # noqa: E402
    decode_attitude_report,
    decode_point_set,
)

HOME_LAT, HOME_LON = -26.6405465, 15.1588228  # locked 2026-11-17
M_PER_DEG = 111320.0


# ----------------------------------------------------------------- sonar ---

def read_svlz(path):
    """Yield (message_id, payload) for every checksum-valid frame."""
    with gzip.open(path, "rb") as fh:
        d = fh.read()
    i, n, bad = 0, len(d), 0
    while i < n - 8:
        if d[i:i + 2] != b"BR":
            i += 1
            continue
        plen, mid = struct.unpack_from("<HH", d, i + 2)
        end = i + 8 + plen
        if end + 2 > n:
            break
        if (sum(d[i:end]) & 0xFFFF) != struct.unpack_from("<H", d, end)[0]:
            bad += 1
            i += 1
            continue
        yield mid, d[i + 8:end]
        i = end + 2
    if bad:
        print(f"  {bad} frames failed checksum", file=sys.stderr)


def load_pings(path):
    """List of (point_set, attitude_or_None). The sonar sends one 504 just
    before each 3104; the most recent one is used."""
    pings, att = [], None
    for mid, p in read_svlz(path):
        if mid == 504:
            att = decode_attitude_report(p)
        elif mid == 3104:
            pings.append((decode_point_set(p), att))
    return pings


# ------------------------------------------------------------------- gps ---

def load_gps(blackbox_dir, t0, t1):
    from mcap.records import Channel, Message, Schema
    from mcap.stream_reader import StreamReader
    from mcap_ros2.decoder import DecoderFactory

    fix, hdg = [], []
    want = {"/mavros/global_position/raw/fix", "/mavros/global_position/compass_hdg"}
    for f in sorted(glob.glob(os.path.join(blackbox_dir, "*", "*.mcap"))):
        sc, ch, dec = {}, {}, {}
        try:
            for r in StreamReader(open(f, "rb"), record_size_limit=1 << 30).records:
                if isinstance(r, Schema):
                    sc[r.id] = r
                elif isinstance(r, Channel):
                    ch[r.id] = r
                elif isinstance(r, Message):
                    c = ch[r.channel_id]
                    if c.topic not in want:
                        continue
                    ts = r.log_time / 1e9
                    if ts < t0 - 30 or ts > t1 + 30:
                        continue
                    key = sc[c.schema_id].name
                    if key not in dec:
                        dec[key] = DecoderFactory().decoder_for("cdr", sc[c.schema_id])
                    m = dec[key](r.data)
                    if c.topic.endswith("fix"):
                        fix.append((ts, m.latitude, m.longitude))
                    else:
                        hdg.append((ts, m.data))
        except Exception:
            pass  # the last chunk of a session has a truncated tail
    fix.sort()
    hdg.sort()
    return np.array(fix), np.array(hdg)


def interp_heading(hdg, t):
    s = np.sin(np.radians(hdg[:, 1]))
    c = np.cos(np.radians(hdg[:, 1]))
    return np.degrees(np.arctan2(np.interp(t, hdg[:, 0], s), np.interp(t, hdg[:, 0], c))) % 360


# -------------------------------------------------------------- geometry ---

def level_basis(att):
    """Rows: device-frame unit vectors of local forward, left, up.
    Device frame: x forward, y port, z up; boresight is -z."""
    if att is None:
        return np.eye(3)
    up = np.array([att.up_x, att.up_y, att.up_z], float)
    up /= np.linalg.norm(up) or 1.0
    fx = np.array([1.0, 0.0, 0.0]) - up[0] * up
    if np.linalg.norm(fx) < 1e-3:  # device x is vertical; fall back to y
        fx = np.array([0.0, 1.0, 0.0]) - up[1] * up
    fx /= np.linalg.norm(fx)
    return np.vstack([fx, np.cross(up, fx), up])


def georeference(pings, fix, hdg, sign, mount_yaw, mount_depth, strong_only):
    t_ping = np.array([ps.utc_msec / 1000.0 for ps, _ in pings])
    lat = np.interp(t_ping, fix[:, 0], fix[:, 1])
    lon = np.interp(t_ping, fix[:, 0], fix[:, 2])
    head = np.radians(interp_heading(hdg, t_ping) + mount_yaw)
    gap = np.array([min(abs(fix[j, 0] - t) for j in (max(0, k - 1), min(len(fix) - 1, k)))
                    for t, k in zip(t_ping, np.searchsorted(fix[:, 0], t_ping))])
    e0 = (lon - HOME_LON) * M_PER_DEG * math.cos(math.radians(HOME_LAT))
    n0 = (lat - HOME_LAT) * M_PER_DEG

    cols = []
    for k, (ps, att) in enumerate(pings):
        if gap[k] > 2.0:  # no GPS within 2 s: don't invent a position
            continue
        thr = ps.pwr_threshold_high if strong_only else -np.inf
        pts = [(p.angle_rad, p.tof_s, p.power) for p in ps.points if p.power >= thr and p.tof_s > 0]
        if not pts:
            continue
        a, tof, pw = np.array(pts).T
        r = tof * ps.speed_of_sound / 2.0
        dev = np.vstack([np.zeros_like(r), r * np.sin(sign * a), -r * np.cos(a)])
        fwd, left, up = level_basis(att) @ dev
        h = head[k]
        east = e0[k] + fwd * math.sin(h) - left * math.cos(h)
        north = n0[k] + fwd * math.cos(h) + left * math.sin(h)
        z = up - mount_depth
        cols.append(np.vstack([east, north, z, pw, np.full_like(r, ps.ping_number),
                               np.full_like(r, ps.utc_msec), r, a]))
    return np.hstack(cols).T if cols else np.zeros((0, 8))


def seabed_filter(P, window=25, band_m=1.0, max_iqr_m=2.0, min_depth_m=1.0):
    """Keep returns near a running seabed estimate.

    Per ping: median depth and inter-quartile spread of its returns. A ping
    whose returns fill the water column (spread > max_iqr_m: turns, aeration,
    the head out of the water) is dropped. The seabed is the running median of
    the remaining per-ping medians over +-window pings; points more than
    band_m from it (fish, kelp, surface, multipath) are dropped. Returns
    (seabed_points, per-ping table [utc_ms, seabed_depth_m, kept])."""
    if not len(P):
        return P, np.zeros((0, 3))
    order = np.argsort(P[:, 5], kind="stable")
    P = P[order]
    t, starts = np.unique(P[:, 5], return_index=True)
    ends = np.r_[starts[1:], len(P)]
    med = np.empty(len(t))
    ok = np.empty(len(t), bool)
    for k, (s, e) in enumerate(zip(starts, ends)):
        z = P[s:e, 2]
        q1, q2, q3 = np.percentile(z, [25, 50, 75])
        med[k], ok[k] = q2, (q3 - q1) <= max_iqr_m and e - s >= 5
    good = np.where(ok)[0]
    bed = np.full(len(t), np.nan)
    for k in range(len(t)):
        j = good[(good >= k - window) & (good <= k + window)]
        if len(j) >= 3:
            bed[k] = np.median(med[j])
    keep = np.zeros(len(P), bool)
    for k, (s, e) in enumerate(zip(starts, ends)):
        if ok[k] and not np.isnan(bed[k]):
            keep[s:e] = (np.abs(P[s:e, 2] - bed[k]) <= band_m) & (P[s:e, 2] <= -min_depth_m)
    return P[keep], np.c_[t, -bed, ok]


# --------------------------------------------------------------- outputs ---

def depth_colours(z):
    zmin, zmax = np.percentile(z, [2, 98]) if len(z) else (0, 1)
    t = np.clip((z - zmin) / max(zmax - zmin, 1e-6), 0, 1)
    # deep = dark blue, shallow = yellow (perceptually ordered)
    stops = np.array([[13, 8, 135], [84, 2, 163], [139, 10, 165], [185, 50, 137],
                      [219, 92, 104], [244, 136, 73], [254, 188, 43], [240, 249, 33]], float)
    x = t * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[:, None]
    return ((1 - f) * stops[i] + f * stops[i + 1]).astype(np.uint8)


def write_ply(path, P):
    rgb = depth_colours(P[:, 2])
    rec = np.zeros(len(P), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                  ("red", "u1"), ("green", "u1"), ("blue", "u1"),
                                  ("intensity", "<f4")])
    rec["x"], rec["y"], rec["z"] = P[:, 0], P[:, 1], P[:, 2]
    rec["red"], rec["green"], rec["blue"] = rgb.T
    rec["intensity"] = np.log10(np.maximum(P[:, 3], 1))
    hdr = ("ply\nformat binary_little_endian 1.0\n"
           f"comment metres from home {HOME_LAT},{HOME_LON}; x east y north z up\n"
           f"element vertex {len(P)}\nproperty float x\nproperty float y\nproperty float z\n"
           "property uchar red\nproperty uchar green\nproperty uchar blue\n"
           "property float intensity\nend_header\n")
    with open(path, "wb") as fh:
        fh.write(hdr.encode())
        fh.write(rec.tobytes())


def write_csv(path, P):
    lat = HOME_LAT + P[:, 1] / M_PER_DEG
    lon = HOME_LON + P[:, 0] / (M_PER_DEG * math.cos(math.radians(HOME_LAT)))
    with gzip.open(path, "wt") as fh:
        fh.write("lat,lon,depth_m,power,ping,utc_ms\n")
        for la, lo, row in zip(lat, lon, P):
            fh.write(f"{la:.7f},{lo:.7f},{-row[2]:.2f},{row[3]:.4g},{int(row[4])},{int(row[5])}\n")


def write_preview(path, P, track, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1.2, 1]})
    sub = P[:: max(1, len(P) // 200000)]
    sc = ax[0].scatter(sub[:, 0], sub[:, 1], c=-sub[:, 2], s=0.3, cmap="viridis_r",
                       vmin=np.percentile(-sub[:, 2], 2), vmax=np.percentile(-sub[:, 2], 98))
    ax[0].plot(track[:, 0], track[:, 1], "r-", lw=0.8, label="GPS track")
    ax[0].plot(0, 0, "k^", label="home")
    ax[0].set_aspect("equal")
    ax[0].set_xlabel("east of home (m)")
    ax[0].set_ylabel("north of home (m)")
    ax[0].legend(loc="best", fontsize=8)
    fig.colorbar(sc, ax=ax[0], label="depth below surface (m)")
    d = np.hypot(sub[:, 0] - track[0, 0], sub[:, 1] - track[0, 1])
    ax[1].scatter(sub[:, 5] / 1000 - sub[0, 5] / 1000, -sub[:, 2], s=0.2, c="k", alpha=0.15)
    ax[1].invert_yaxis()
    ax[1].set_xlabel("time into recording (s)")
    ax[1].set_ylabel("depth below surface (m)")
    ax[1].set_title("all strong returns vs time")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ------------------------------------------------------------------ main ---

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(REPO, "boat_data"))
    ap.add_argument("--date", default=None, help="only recordings from this day, YYYY-MM-DD")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mount-yaw", type=float, default=0.0)
    ap.add_argument("--mount-depth", type=float, default=0.3, help="transducer below waterline, m")
    ap.add_argument("--min-depth", type=float, default=1.0, help="seabed layer: ignore returns shallower than this, m")
    ap.add_argument("--flip-angle", choices=["auto", "no", "yes"], default="auto")
    ap.add_argument("--all-points", action="store_true", help="keep returns below the sonar's high threshold")
    a = ap.parse_args()

    out = a.out or os.path.join(a.data, "processed", a.date or "all")
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(a.data, "sonarview", "Omniscan 3D", "*.svlz")))
    if a.date:
        files = [f for f in files if os.path.basename(f).startswith(a.date)]
    report = {"home": [HOME_LAT, HOME_LON], "recordings": []}
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        print(f"{name}: decoding sonar")
        pings = load_pings(f)
        if not pings:
            continue
        t0, t1 = pings[0][0].utc_msec / 1000, pings[-1][0].utc_msec / 1000
        print(f"  {len(pings)} pings, loading GPS")
        fix, hdg = load_gps(os.path.join(a.data, "blackbox"), t0, t1)
        if len(fix) < 2 or len(hdg) < 2:
            print("  no GPS overlaps this recording; skipped")
            continue

        def run(sign):
            return georeference(pings, fix, hdg, sign, a.mount_yaw, a.mount_depth, not a.all_points)

        if a.flip_angle == "auto":
            cand = {s: run(s) for s in (1, -1)}
            above = {s: float(np.mean(P[:, 2] > 0)) if len(P) else 1.0 for s, P in cand.items()}
            sign = min(above, key=above.get)
            P = cand[sign]
        else:
            sign = -1 if a.flip_angle == "yes" else 1
            P = run(sign)
            above = {sign: float(np.mean(P[:, 2] > 0))}
        under = P[P[:, 2] <= 0]

        m = (fix[:, 0] >= t0) & (fix[:, 0] <= t1)
        te = (fix[m, 2] - HOME_LON) * M_PER_DEG * math.cos(math.radians(HOME_LAT))
        tn = (fix[m, 1] - HOME_LAT) * M_PER_DEG
        track = np.c_[te, tn]
        path_m = float(np.sum(np.hypot(np.diff(te), np.diff(tn))))
        with open(os.path.join(out, f"{name}_track.csv"), "w") as fh:
            fh.write("utc_s,lat,lon\n")
            for row in fix[m]:
                fh.write(f"{row[0]:.3f},{row[1]:.7f},{row[2]:.7f}\n")

        bed, bed_tab = seabed_filter(under, min_depth_m=a.min_depth)
        write_ply(os.path.join(out, f"{name}_cloud.ply"), under)
        write_ply(os.path.join(out, f"{name}_seabed.ply"), bed)
        write_csv(os.path.join(out, f"{name}_seabed.csv.gz"), bed)
        utc = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%H:%M:%S")
        write_preview(os.path.join(out, f"{name}_preview.png"), under, track,
                      f"{name}  {utc(t0)}-{utc(t1)} UTC  raw: {len(under):,} points")
        if len(bed):
            write_preview(os.path.join(out, f"{name}_seabed_preview.png"), bed, track,
                          f"{name}  {utc(t0)}-{utc(t1)} UTC  seabed: {len(bed):,} points")

        att = [x for _, x in pings if x is not None]
        dep = -bed[:, 2]
        rec = {
            "file": os.path.basename(f), "utc": [utc(t0), utc(t1)], "pings": len(pings),
            "points_total": int(sum(len(ps.points) for ps, _ in pings)),
            "points_classified_bottom": int(sum(p.pt_type == 1 for ps, _ in pings for p in ps.points)),
            "points_strong_underwater": int(len(under)), "points_seabed": int(len(bed)),
            "pings_with_seabed": int(bed_tab[:, 2].sum()) if len(bed_tab) else 0,
            "angle_sign": sign,
            "fraction_above_surface": above, "track_m": round(path_m, 1),
            "track_extent_m": [round(float(np.ptp(te)), 1), round(float(np.ptp(tn)), 1)],
            "gps_fixes": int(m.sum()),
            "depth_m_p5_p50_p95": [round(float(x), 2) for x in np.percentile(dep, [5, 50, 95])] if len(dep) else [],
            "range_window_m": [round(pings[0][0].points[0].tof_s * 750, 1) if pings[0][0].points else None],
            "roll_deg_p5_p50_p95": [round(float(x), 1) for x in np.percentile([x.roll_deg for x in att], [5, 50, 95])],
            "pitch_deg_p5_p50_p95": [round(float(x), 1) for x in np.percentile([x.pitch_deg for x in att], [5, 50, 95])],
        }
        report["recordings"].append(rec)
        print("  " + json.dumps(rec))
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
