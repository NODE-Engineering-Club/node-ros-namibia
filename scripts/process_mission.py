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
autopilot compass. Two mounting facts are not recorded anywhere, so by default
the script measures them from the data itself:
  --flip-angle  which way the beam angle runs (which fan edge is "up").
                auto: in most pings the seabed is one straight line in the
                fan; pick the sign that makes that line level with gravity.
  --mount-yaw   device x-axis relative to the bow, degrees clockwise.
                auto: pick the yaw where repeated passes over the same ground
                agree best on depth (needs the boat to cross its own track).
Getting either wrong turns each ping's flat seabed into a tilted or misplaced
streak, and the overlaid pings blur into an unreadable cloud (2026-11-24:
the old "fewest points above the surface" rule picked the wrong sign).

Outputs, per recording, in --out:
  <name>_cloud.ply     binary PLY, x=east y=north z=up (m, from home), intensity,
                       depth-coloured: CloudCompare / MeshLab / Blender
  <name>_seabed.ply    the same, filtered to a running seabed estimate
  <name>_seabed.csv.gz lat, lon, depth_m, power, ping, utc_ms (for GIS)
  <name>_track.csv     GPS track during the recording
  <name>_preview.png   plan view + depth profile, for a quick look
  <name>_filtered_out.ply / .csv.gz  points the seabed filter dropped
                       (same columns as the seabed CSV), for review
  <name>_water.csv.gz  the water-column echoes below, same columns
  <name>_water.ply     everything that is NOT seabed: echoes that stand out from
                       the background at their range (objects, fish, ropes ...)
  <name>_objects.csv   candidate objects: compact echo clusters that stay at one
                       place on the ground over many pings (lat, lon, depth ...)
  <name>_objects.ply   the points of those clusters only
  <name>_objects.png   numbered objects on the map + water-column echogram
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
    # Read incrementally so a recording cut off mid-write (power off before
    # SonarView closed the file) still yields every complete frame.
    chunks = []
    with gzip.open(path, "rb") as fh:
        try:
            while True:
                c = fh.read(1 << 20)
                if not c:
                    break
                chunks.append(c)
        except EOFError:
            print("  recording truncated (not closed cleanly); "
                  "using the frames before the cut", file=sys.stderr)
    d = b"".join(chunks)
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


def seabed_filter(P, window=25, band_m=1.0, max_iqr_m=2.0, min_depth_m=1.0, with_rejected=False):
    """Keep returns near a running seabed estimate.

    Per ping: median depth and inter-quartile spread of its returns. A ping
    whose returns fill the water column (spread > max_iqr_m: turns, aeration,
    the head out of the water) is dropped. The seabed is the running median of
    the remaining per-ping medians over +-window pings; points more than
    band_m from it (fish, kelp, surface, multipath) are dropped. Returns
    (seabed_points, per-ping table [utc_ms, seabed_depth_m, kept]), plus the
    dropped points as a third value when with_rejected is set."""
    if not len(P):
        return (P, np.zeros((0, 3)), P) if with_rejected else (P, np.zeros((0, 3)))
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
    if with_rejected:
        return P[keep], np.c_[t, -bed, ok], P[~keep]
    return P[keep], np.c_[t, -bed, ok]


# ------------------------------------------------------- mount from data ---

def pick_angle_sign(pings, n_sample=800, seed=0):
    """Fit the dominant straight line (the seabed) in each sampled ping and
    measure how far its normal is from gravity for both angle signs. Returns
    (sign, {sign: median tilt deg}, pings used)."""
    rng = np.random.default_rng(seed)
    idx = np.linspace(0, len(pings) - 1, min(n_sample, len(pings))).astype(int)
    tilt = {1: [], -1: []}
    for i in idx:
        ps, at = pings[i]
        if at is None:
            continue
        A = np.array([(p.angle_rad, p.tof_s * ps.speed_of_sound / 2) for p in ps.points
                      if p.power >= ps.pwr_threshold_high and p.tof_s > 0])
        if len(A) < 40:
            continue
        X = np.c_[A[:, 1] * np.sin(A[:, 0]), -A[:, 1] * np.cos(A[:, 0])]
        best_n, best = 0, None
        for _ in range(60):
            j = rng.choice(len(X), 2, replace=False)
            d = X[j[1]] - X[j[0]]
            L = math.hypot(*d)
            if L < 0.5:
                continue
            nrm = np.array([-d[1], d[0]]) / L
            c = nrm @ X[j[0]]
            k = int(np.sum(np.abs(X @ nrm - c) < 0.15))
            if k > best_n:
                best_n, best = k, (nrm if c >= 0 else -nrm)
        if best is None or best_n < 0.5 * len(X):
            continue
        down = -np.array([at.up_y, at.up_z])
        down /= np.linalg.norm(down) or 1.0
        for sgn in (1, -1):
            nv = np.array([sgn * best[0], best[1]])
            tilt[sgn].append(math.degrees(math.acos(np.clip(nv @ down, -1, 1))))
    if not tilt[1]:
        return 1, {}, 0
    med = {s: round(float(np.median(v)), 1) for s, v in tilt.items()}
    return min(med, key=med.get), med, len(tilt[1])


def crossover_disagreement(P, cell_m=1.0, window_s=30.0):
    """Median spread of seabed depth between different passes (30 s windows)
    over the same 1 m cell, and how many cells were seen on 2+ passes."""
    bed, _ = seabed_filter(P[P[:, 2] <= 0])
    if len(bed) < 100:
        return np.inf, 0
    c = np.floor(bed[:, :2] / cell_m).astype(np.int64)
    c -= c.min(0)
    cell = c[:, 0] * (c[:, 1].max() + 1) + c[:, 1]
    win = (bed[:, 5] / 1000 / window_s).astype(np.int64)
    key = np.c_[cell, win - win.min()]
    u, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    med = np.array([np.median(bed[inv == k, 2]) for k in range(len(u))]) if len(u) < 20000 else None
    if med is None:
        return np.inf, 0
    ok = cnt >= 5
    u, med = u[ok], med[ok]
    cells, ci = np.unique(u[:, 0], return_inverse=True)
    spread = np.array([np.ptp(med[ci == k]) for k in range(len(cells))])
    n_pass = np.bincount(ci)
    multi = spread[n_pass >= 2]
    return (float(np.median(multi)) if len(multi) else np.inf), int(len(multi))


def pick_mount_yaw(pings, fix, hdg, sign, mount_depth, step=4):
    """Search the mount yaw that makes overlapping passes agree. Uses every
    step-th ping to keep it quick. Returns (yaw, disagreement m, cells, table)."""
    sub = pings[::step]
    table = {}

    def score(yaw):
        if yaw not in table:
            table[yaw] = crossover_disagreement(georeference(sub, fix, hdg, sign, yaw, mount_depth, True))
        return table[yaw][0]

    best = min(range(0, 360, 30), key=score)
    best = min([(best + d) % 360 for d in (-20, -10, 0, 10, 20)], key=score)
    best = min([(best + d) % 360 for d in (-5, 0, 5)], key=score)
    return best, table[best][0], table[best][1], {k: round(v[0], 3) for k, v in sorted(table.items())}


# ---------------------------------------------------------- water column ---

def anomaly_score(P, t_bin_s=20.0, r_bin_m=0.25):
    """Robust z-score of each return's log power against the other returns at
    the same range in the same ~20 s window. The Omniscan returns something at
    almost every range, so raw power alone lights up the whole water column;
    an object is what is LOUDER than its range neighbours."""
    lp = np.log10(np.maximum(P[:, 3], 1))
    tb = ((P[:, 5] - P[:, 5].min()) / 1000 / t_bin_s).astype(np.int64)
    rb = np.clip((P[:, 6] / r_bin_m).astype(np.int64), 0, 999)
    key = tb * 1000 + rb
    o = np.argsort(key, kind="stable")
    ks = key[o]
    s = np.r_[0, np.flatnonzero(np.diff(ks)) + 1]
    e = np.r_[s[1:], len(ks)]
    z = np.zeros(len(P))
    for a, b in zip(s, e):
        v = lp[o[a:b]]
        m = np.median(v)
        z[o[a:b]] = (v - m) / max(np.median(np.abs(v - m)) * 1.48, 0.05)
    return z


def water_column(P, bed_tab, z_min=3.0, min_range_m=1.2, bed_band_m=1.0):
    """Anomalous returns that are not seabed. Pings where the whole column is
    anomalous (interference, the boat turning hard) are dropped."""
    if not len(P):
        return P, np.zeros(0)
    z = anomaly_score(P)
    u, inv = np.unique(P[:, 4], return_inverse=True)
    frac = np.bincount(inv, weights=(z > 2)) / np.bincount(inv)
    keep = (z > z_min) & (frac[inv] <= 0.25) & (P[:, 2] < 0) & (P[:, 6] > min_range_m)
    if len(bed_tab):
        bed = dict(zip(bed_tab[:, 0], bed_tab[:, 1]))
        b = np.array([bed.get(t, np.nan) for t in P[:, 5]])
        with np.errstate(invalid="ignore"):
            keep &= ~(np.abs(-P[:, 2] - b) <= bed_band_m)
    return P[keep], z[keep]


def find_objects(W, voxel_m=0.4, min_pings_per_voxel=3, min_pings=8, max_size_m=5.0):
    """Cluster water-column returns on the ground. A real object stays in the
    same place while the boat moves past it (an arch on the echogram); noise
    and fish do not. Returns (object table rows, point mask, label per point)."""
    from scipy import ndimage
    if not len(W):
        return [], np.zeros(0, bool), np.zeros(0, int)
    k = np.floor(W[:, :3] / voxel_m).astype(np.int64)
    k -= k.min(0)
    shape = tuple(k.max(0) + 1)
    lin = np.ravel_multi_index(k.T, shape)
    vp = np.unique(np.c_[lin, W[:, 4]], axis=0)
    cnt = np.bincount(vp[:, 0].astype(np.int64), minlength=int(np.prod(shape)))
    lab, n = ndimage.label((cnt >= min_pings_per_voxel).reshape(shape), structure=np.ones((3, 3, 3)))
    pl = lab.ravel()[lin]
    rows, mask, ids = [], np.zeros(len(W), bool), np.zeros(len(W), int)
    for c in range(1, n + 1):
        sel = pl == c
        X = W[sel]
        pings = np.unique(X[:, 4])
        size = np.ptp(X[:, :3], 0)
        if len(pings) < min_pings or max(size[0], size[1]) > max_size_m:
            continue
        ts = np.unique(X[:, 5]) / 1000
        passes = 1 + int(np.sum(np.diff(ts) > 15))
        top, bottom = -np.percentile(X[:, 2], 90), -np.percentile(X[:, 2], 10)
        rows.append({"east": float(np.median(X[:, 0])), "north": float(np.median(X[:, 1])),
                     "depth_m": float(-np.median(X[:, 2])), "top_m": float(top), "bottom_m": float(bottom),
                     "size_m": float(max(size[0], size[1])), "pings": int(len(pings)), "passes": passes,
                     "t0": float(ts[0]), "t1": float(ts[-1]), "range_m": float(np.median(X[:, 6])),
                     "points": int(sel.sum())})
        mask |= sel
    order = sorted(range(len(rows)), key=lambda i: (-rows[i]["passes"], -rows[i]["pings"]))
    rows = [rows[i] for i in order]
    # relabel the points 1..N in table order
    for new, r in enumerate(rows, 1):
        r["id"] = new
    return rows, mask, pl


def tag_objects(rows, bed, radius_m=5.0):
    """Compare each object with the seabed measured within radius_m. Echoes
    from BELOW the seabed cannot be real objects: they are the seabed echo
    bouncing a second time (boat hull / surface -> bottom)."""
    for r in rows:
        r["seabed_m"] = None
        if len(bed):
            d = np.hypot(bed[:, 0] - r["east"], bed[:, 1] - r["north"])
            near = bed[d <= radius_m]
            if len(near) >= 20:
                r["seabed_m"] = float(-np.median(near[:, 2]))
        sb = r["seabed_m"]
        if sb is not None and r["top_m"] > sb + 0.5:
            r["note"] = "below seabed: echo, not an object"
        elif r["top_m"] <= 1.5:
            r["note"] = "at the surface"
        elif sb is not None and r["bottom_m"] >= sb - 1.0:
            r["note"] = "on the seabed"
        elif sb is not None:
            r["note"] = "in the water column"
        else:
            r["note"] = "no seabed measured nearby"
    good = [r for r in rows if not r["note"].startswith("below")]
    bad = [r for r in rows if r["note"].startswith("below")]
    rows[:] = good + bad
    for i, r in enumerate(rows, 1):
        r["id"] = i
    return rows


def write_objects_csv(path, rows):
    utc = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%H:%M:%S")
    with open(path, "w") as fh:
        fh.write("id,lat,lon,east_m,north_m,depth_m,top_m,bottom_m,size_m,pings,passes,"
                 "first_seen_utc,last_seen_utc,slant_range_m,seabed_nearby_m,note\n")
        for r in rows:
            lat = HOME_LAT + r["north"] / M_PER_DEG
            lon = HOME_LON + r["east"] / (M_PER_DEG * math.cos(math.radians(HOME_LAT)))
            fh.write(f"{r['id']},{lat:.7f},{lon:.7f},{r['east']:.1f},{r['north']:.1f},{r['depth_m']:.1f},"
                     f"{r['top_m']:.1f},{r['bottom_m']:.1f},{r['size_m']:.1f},{r['pings']},{r['passes']},"
                     f"{utc(r['t0'])},{utc(r['t1'])},{r['range_m']:.1f},"
                     f"{'' if r['seabed_m'] is None else round(r['seabed_m'], 1)},{r['note']}\n")


def write_objects_preview(path, Pall, zall, W, rows, bed, track, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(18, 12))
    g = fig.add_gridspec(2, 2, height_ratios=[1.4, 1], width_ratios=[1.2, 1])
    ax = fig.add_subplot(g[0, 0])
    if len(bed):
        sb = bed[:: max(1, len(bed) // 50000)]
        ax.scatter(sb[:, 0], sb[:, 1], s=0.3, c="0.75", label="seabed")
    sw = W[:: max(1, len(W) // 100000)]
    sc = ax.scatter(sw[:, 0], sw[:, 1], c=-sw[:, 2], s=0.4, cmap="viridis_r", vmin=0,
                    vmax=max(np.percentile(-sw[:, 2], 98), 1) if len(sw) else 10, alpha=0.5)
    ax.plot(track[:, 0], track[:, 1], "r-", lw=0.8, label="GPS track")
    ax.plot(0, 0, "k^", label="home")
    for r in [r for r in rows if not r["note"].startswith("below")]:
        ax.add_patch(plt.Circle((r["east"], r["north"]), max(r["size_m"] / 2, 0.8), fill=False,
                                ec="k", lw=1.5 if r["passes"] > 1 else 0.8))
        ax.annotate(str(r["id"]), (r["east"], r["north"]), xytext=(4, 4), textcoords="offset points",
                    fontsize=9, weight="bold" if r["passes"] > 1 else "normal")
    ax.set_aspect("equal")
    ax.set_xlabel("east of home (m)")
    ax.set_ylabel("north of home (m)")
    ax.legend(loc="best", fontsize=8)
    ax.set_title("circles: candidate objects (bold: seen on 2+ passes)", fontsize=10)
    fig.colorbar(sc, ax=ax, label="depth below surface (m)")

    ax = fig.add_subplot(g[0, 1])
    ax.axis("off")
    lines = ["id  depth   size  pings passes  seen (UTC)     seabed  note"]
    for r in rows[:40]:
        utc = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%H:%M:%S")
        lines.append(f"{r['id']:>2}  {r['depth_m']:4.1f} m {r['size_m']:4.1f} m {r['pings']:5d} {r['passes']:5d}   "
                     f"{utc(r['t0'])}-{utc(r['t1'])[3:]}  "
                     f"{'  -  ' if r['seabed_m'] is None else format(r['seabed_m'], '4.1f') + ' m'}  {r['note']}")
    if len(rows) > 40:
        lines.append(f"... {len(rows) - 40} more in the CSV")
    ax.text(-0.05, 1, "\n".join(lines), family="monospace", fontsize=7.5, va="top")

    ax = fig.add_subplot(g[1, :])
    t0 = Pall[:, 5].min() / 1000
    t = Pall[:, 5] / 1000 - t0
    H, _, _ = np.histogram2d(t, -Pall[:, 2], bins=(1200, 180), range=((0, t.max()), (0, 18)),
                             weights=np.maximum(zall, 0) ** 2)
    N, _, _ = np.histogram2d(t, -Pall[:, 2], bins=(1200, 180), range=((0, t.max()), (0, 18)))
    ax.imshow((H / np.maximum(N, 1)).T, aspect="auto", origin="upper", extent=(0, t.max(), 18, 0),
              cmap="magma", vmin=0, vmax=6)
    for r in [r for r in rows if not r["note"].startswith("below")]:
        ax.annotate(str(r["id"]), ((r["t0"] + r["t1"]) / 2 - t0, r["depth_m"]), color="cyan", fontsize=8,
                    ha="center", va="center")
    ax.set_xlabel("time into recording (s)")
    ax.set_ylabel("depth below surface (m)")
    ax.set_title("echogram: how much each echo stands out from its range neighbours (arches = something the boat drove past)")
    fig.suptitle(title)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(path, dpi=100)
    plt.close(fig)


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
    ap.add_argument("--mount-yaw", default="auto",
                    help="degrees clockwise from the bow, or 'auto' (from pass-to-pass agreement)")
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

        if a.flip_angle == "auto":
            sign, seabed_tilt, n_fit = pick_angle_sign(pings)
            print(f"  angle sign {sign}: seabed line tilt vs level {seabed_tilt} deg ({n_fit} pings)")
        else:
            sign, seabed_tilt = (-1 if a.flip_angle == "yes" else 1), {}
        if a.mount_yaw == "auto":
            print("  mount yaw: searching for best pass-to-pass agreement")
            yaw, yaw_dis, yaw_cells, yaw_table = pick_mount_yaw(pings, fix, hdg, sign, a.mount_depth)
            print(f"  mount yaw {yaw} deg: passes agree to {yaw_dis:.2f} m over {yaw_cells} cells")
            if yaw_cells < 300:
                print("  (too little overlap between passes to trust it; using 0)")
                yaw = 0
        else:
            yaw, yaw_dis, yaw_cells, yaw_table = float(a.mount_yaw), None, None, {}
        P = georeference(pings, fix, hdg, sign, yaw, a.mount_depth, not a.all_points)
        above = {sign: float(np.mean(P[:, 2] > 0)) if len(P) else 0.0}
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

        bed, bed_tab, rejected = seabed_filter(under, min_depth_m=a.min_depth, with_rejected=True)
        write_ply(os.path.join(out, f"{name}_cloud.ply"), under)
        write_ply(os.path.join(out, f"{name}_seabed.ply"), bed)
        write_csv(os.path.join(out, f"{name}_seabed.csv.gz"), bed)
        # what the seabed filter dropped: surface clutter, multipath, but also
        # anything standing off the bottom (pillars, kelp) -- kept for review
        write_ply(os.path.join(out, f"{name}_filtered_out.ply"), rejected)
        write_csv(os.path.join(out, f"{name}_filtered_out.csv.gz"), rejected)
        utc = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%H:%M:%S")
        write_preview(os.path.join(out, f"{name}_preview.png"), under, track,
                      f"{name}  {utc(t0)}-{utc(t1)} UTC  raw: {len(under):,} points")
        if len(bed):
            write_preview(os.path.join(out, f"{name}_seabed_preview.png"), bed, track,
                          f"{name}  {utc(t0)}-{utc(t1)} UTC  seabed: {len(bed):,} points")

        print("  water column: scoring every return")
        Pall = georeference(pings, fix, hdg, sign, yaw, a.mount_depth, False)
        Pall = Pall[np.argsort(Pall[:, 5], kind="stable")]
        zall = anomaly_score(Pall)
        W, _ = water_column(Pall, bed_tab)
        objs, omask, _ = find_objects(W)
        tag_objects(objs, bed)
        if len(W):
            write_ply(os.path.join(out, f"{name}_water.ply"), W)
            write_csv(os.path.join(out, f"{name}_water.csv.gz"), W)
        if omask.any():
            write_ply(os.path.join(out, f"{name}_objects.ply"), W[omask])
        write_objects_csv(os.path.join(out, f"{name}_objects.csv"), objs)
        write_objects_preview(os.path.join(out, f"{name}_objects.png"), Pall, zall, W, objs, bed, track,
                              f"{name}  {utc(t0)}-{utc(t1)} UTC  water column: {len(W):,} points, "
                              f"{len(objs)} candidate objects")

        att = [x for _, x in pings if x is not None]
        dep = -bed[:, 2]
        rec = {
            "file": os.path.basename(f), "utc": [utc(t0), utc(t1)], "pings": len(pings),
            "points_total": int(sum(len(ps.points) for ps, _ in pings)),
            "points_classified_bottom": int(sum(p.pt_type == 1 for ps, _ in pings for p in ps.points)),
            "points_strong_underwater": int(len(under)), "points_seabed": int(len(bed)),
            "points_water_column": int(len(W)), "objects": len(objs),
            "objects_seen_on_several_passes": sum(o["passes"] > 1 for o in objs),
            "objects_below_seabed": sum(o["note"].startswith("below") for o in objs),
            "pings_with_seabed": int(bed_tab[:, 2].sum()) if len(bed_tab) else 0,
            "angle_sign": sign, "seabed_line_tilt_deg_by_sign": seabed_tilt,
            "mount_yaw_deg": yaw, "pass_disagreement_m": yaw_dis, "pass_overlap_cells": yaw_cells,
            "pass_disagreement_by_yaw": yaw_table,
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
