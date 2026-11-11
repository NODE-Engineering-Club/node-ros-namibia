"""Signal and board health read straight from the kernel, no ROS.

Nothing else in the stack publishes Wi-Fi strength or Jetson temperature,
so the cloud node reads them here. Every function returns ``None`` rather
than raising when the source is missing (no Wi-Fi interface in a
container, a different board), so a bench run degrades to empty fields.

Note what the Wi-Fi reading is: the Jetson's OWN wireless interface. On the
boat the shore link is the MikroTik NetMetal over Ethernet, which the Jetson
cannot see through /proc; that radio's RSSI would need the MikroTik API.
"""
from __future__ import annotations

import glob
from typing import Optional


def parse_iw_link(text: str, interface: str) -> Optional[dict]:
    """Parse ``iw dev <if> link`` output; None when not associated."""
    if "Not connected" in text or "signal:" not in text:
        return None
    out = {"interface": interface}
    for raw in text.splitlines():
        line = raw.strip()
        key, _, val = line.partition(":")
        val = val.strip()
        try:
            if key == "signal":
                out["rssi_dbm"] = float(val.split()[0])
            elif key == "SSID":
                out["ssid"] = val
            elif key == "freq":
                out["freq_mhz"] = int(float(val.split()[0]))
            elif key == "tx bitrate":
                out["tx_mbps"] = float(val.split()[0])
        except (ValueError, IndexError):
            continue
    if "rssi_dbm" in out:
        # Same 0..100 scale NetworkManager shows: -100 dBm -> 0, -50 -> 100.
        out["quality_pct"] = round(min(max(2.0 * (out["rssi_dbm"] + 100.0), 0.0), 100.0), 1)
    return out


def read_wifi(interface: str, path: str = "/proc/net/wireless") -> Optional[dict]:
    """Signal for ``interface``: ``iw`` first, /proc/net/wireless as fallback.

    ``iw`` reads nl80211 and reports true dBm. /proc/net/wireless is driver-
    defined: on the Jetson's card it printed 61 while ``iw`` said -38 dBm,
    so from /proc a level is only trusted when it is a plausible dBm value.
    """
    import shutil
    import subprocess
    iw = shutil.which("iw")
    if iw:
        try:
            r = subprocess.run([iw, "dev", interface, "link"], capture_output=True,
                               text=True, timeout=1.5)
            if r.returncode == 0:
                return parse_iw_link(r.stdout, interface)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return read_proc_wireless(interface, path)


def read_proc_wireless(interface: str, path: str = "/proc/net/wireless") -> Optional[dict]:
    """Link quality and signal level for ``interface`` from /proc/net/wireless.

    Format (two header lines, then one per interface)::

        wlan0: 0000   58.  -52.  -256        0      0      0      0      3        0
    """
    try:
        with open(path, encoding="ascii") as fh:
            lines = fh.read().splitlines()[2:]
    except OSError:
        return None
    for line in lines:
        name, _, rest = line.partition(":")
        if name.strip() != interface:
            continue
        cols = rest.split()
        if len(cols) < 3:
            return None
        try:
            quality = float(cols[1].rstrip("."))
            level = float(cols[2].rstrip("."))
        except ValueError:
            return None
        # Most drivers report dBm directly (negative). Some report an
        # unsigned 0..255 value, i.e. dBm + 256. Anything else is a driver-
        # specific scale and is not reported as dBm.
        if level > 0:
            level -= 256.0
        rssi = round(level, 1) if -110.0 <= level <= -10.0 else None
        return {
            "interface": interface,
            "rssi_dbm": rssi,
            "quality_pct": round(min(quality / 70.0, 1.0) * 100.0, 1),
        }
    return None


def read_max_temp_c(pattern: str = "/sys/class/thermal/thermal_zone*/temp") -> Optional[float]:
    """Hottest thermal zone in °C, or None if there are none readable."""
    best = None
    for p in glob.glob(pattern):
        try:
            with open(p, encoding="ascii") as fh:
                milli = int(fh.read().strip())
        except (OSError, ValueError):
            continue
        # Jetson reports an idle/absent zone as -40 °C or similar; ignore
        # anything below a plausible ambient.
        c = milli / 1000.0
        if c < -20.0 or c > 150.0:
            continue
        best = c if best is None else max(best, c)
    return None if best is None else round(best, 1)


def summarize_sonar_points(points, max_points: int = 120) -> dict:
    """Decimate one ping's (x, y, z, intensity) tuples for the cloud.

    Keeps the ``max_points`` strongest returns rather than every n-th one,
    since the dashboard wants what the sonar actually saw, and reports the
    range spread of the whole ping so nothing is hidden by the cut.
    Output points are ``[y, z, intensity_norm]``: lateral and boresight
    metres in the bridge's frame, intensity scaled 0..1 within the ping.
    """
    # float() also turns numpy scalars into plain floats for json.
    pts = [tuple(float(v) for v in p[:4]) for p in points]
    pts = [p for p in pts if all(v == v for v in p)]  # drop NaNs
    n = len(pts)
    if n == 0:
        return {"n_points": 0, "points": [], "range_min_m": None, "range_median_m": None,
                "range_max_m": None}
    ranges = sorted((p[0] ** 2 + p[1] ** 2 + p[2] ** 2) ** 0.5 for p in pts)
    strongest = sorted(pts, key=lambda p: p[3], reverse=True)[:max_points]
    imax = max(p[3] for p in strongest) or 1.0
    return {
        "n_points": n,
        "range_min_m": round(ranges[0], 2),
        "range_median_m": round(ranges[n // 2], 2),
        "range_max_m": round(ranges[-1], 2),
        "points": [[round(p[1], 2), round(p[2], 2), round(p[3] / imax, 2)] for p in strongest],
    }
