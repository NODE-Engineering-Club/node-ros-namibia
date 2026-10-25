# 2026-10-25 — Cloud telemetry dummy end-to-end test, and what's left for ROS2

What was actually tested this session, the exact API contract the frontend
and the cloud relay speak, and the concrete steps to validate `cloud_bridge`
against real ROS2 next — in case this needs to be re-derived later.

## 1. What this test proved, and what it didn't

**Proved:** the frontend ↔ cloud relay half of the pipeline — the
WebSocket protocol, the role-based routing, and the dashboard's rendering
— all work, including over a real public internet hop (Cloudflare Tunnel).

**Did not touch:** ROS2, `cloud_telemetry_node.py` itself, any real sensor,
or the actual `cloudflare/worker-telemetry-relay.js` + Durable Object code.
The "ASV" in this test was a plain Python script constructing the same JSON
by hand — it never imported anything from this repo's ROS packages.

No ROS2 install exists on the machine this was run on (confirmed
repeatedly across this project's sessions), so this was the right-sized
test for what's actually checkable here. Closing the remaining gap (does
`cloud_telemetry_node.py`'s own subscription/decimation/packaging code
work) needs an actual ROS2 Jazzy environment — §4 below is exactly that
next step, with a ready-to-run tool already written for it.

## 2. Why Cloudflare Workers + Durable Objects were avoided

`cloudflare/worker-telemetry-relay.js` uses a Durable Object
(`TelemetryRelay`) to hold the live ASV↔browser routing state. Durable
Objects require Cloudflare's paid Workers plan, which needs a card on
file even if usage stays within free limits. Since the goal was "just a
test, no card," the actual Worker was **not** deployed. Instead:

- `relay_server.py` is a plain local WebSocket server implementing
  **the exact same routing logic** as the `TelemetryRelay` class — same
  `?role=asv`/`?role=browser` handling, same broadcast-on-ASV-message, same
  latest-packet hydration for new browsers.
- **Cloudflare Tunnel** (`cloudflared`) exposed that local server to the
  public internet as `https://<random>.trycloudflare.com` — a genuinely
  free, no-login, no-card Cloudflare product (a "quick tunnel").

This means the *routing logic* is verified (it's identical code, logically,
to the Worker), but the Worker/Durable-Object *deployment path itself* is
still unverified — see §6.

## 3. The API contract (frontend ↔ relay)

This is what any sender — the dummy script today, `cloud_telemetry_node.py`
tomorrow, or a real deployed Worker eventually — and any listener (the
dashboard, or anything else) must agree on.

### Endpoint

```
<ws:// or wss://><host>/api/v1/telemetry/stream?role=<asv|browser>
```

- **Transport:** plain WebSocket. `ws://` for local/plaintext testing,
  `wss://` once behind any real TLS termination (Cloudflare Tunnel and the
  real Worker both terminate TLS, so production is always `wss://`).
- **`role=asv`** — exactly one sender per relay instance. Sends packets,
  never reads. Required, not optional: the Worker (and this test's local
  stand-in) routes purely on this query param — a connection without it,
  or with a different value, is treated as a `role=browser` listener and
  the whole pipeline silently relays nothing. (This was an actual bug in
  the original design document — see `cloud_telemetry_node.py`'s own
  module docstring.)
- **`role=browser`** — any number of listeners. Receives every packet the
  ASV sends, broadcast as-is, plus one immediate "hydration" packet (the
  most recent one seen) on connect, so a late-joining dashboard isn't
  stuck showing nothing until the next tick.

### Message format

One JSON text frame per packet, sent ASV → relay → fan-out to all
browsers, unmodified. Nominal rate 5 Hz (`stream_rate_hz` param on
`cloud_telemetry_node`), but nothing on either end assumes a fixed rate —
the frontend just renders whatever arrives, whenever it arrives (QoS 0,
drop-tail: a dropped or delayed packet is not retried or buffered).

```jsonc
{
  "asv_id": "ASKET_02",
  "seq": 14208,                    // monotonically increasing
  "timestamp": 1792612345.204,     // unix epoch seconds, UTC
  "nav": {
    "lat": -26.638521, "lon": 15.152843, "alt": 0.42,
    "heading": 1.87, "sog": 1.22, "cog": 2.01,
    "rtk_mode": 3                  // 0 none, 1 single/DGPS, 2 RTK float, 3 RTK fixed
  },
  "lidar_clusters": [
    {
      "id": 101, "class": "KELP_CANOPY",        // or BUOY / VESSEL / FLOTSAM
      "centroid_local": [12.4, -2.1, 0.05],      // metres, boat-relative
      "centroid_global": [-26.638410, 15.152822],// [lat, lon]
      "size_m": [3.2, 2.8, 0.25]
    }
  ],
  "sam2_polygons": [
    { "id": 101, "class": "KELP_CANOPY", "conf": 0.94,
      "vertices_norm": [[0.21, 0.48], [0.28, 0.45], "..."] } // [0..1] normalized to image W/H
  ],
  "sonar": {                         // OMITTED entirely if sonar_bridge_node isn't running
    "ping_id": 8421, "range_m": 30.0, "gain_db": 22.5,
    "port_intensity_u8": [/* 100 values, 0-255 */],
    "stbd_intensity_u8": [/* 100 values, 0-255 */]
  },
  "health": {                        // any field may be null if that sensor isn't wired yet
    "battery_voltage": 16.24, "battery_soc": 84.5,
    "jetson_temp_c": null, "leak_detected": null, "wifi_rssi_dbm": null
  },
  "image_jpeg_b64": "..."            // OMITTED on most ticks; present ~1 Hz only
}
```

Full schema: `Mission controls and documents/asket_2_0_cloud_telemetry_remote_control_architecture.md`
§5.1. The frontend (`src/frontend/index.html`) is null-safe on every
numeric field via its `fmt()` helper — a missing/`null` health value
renders as `—`, it does not throw and does not skip rendering the rest of
the dashboard (that was a real bug caught and fixed last session; see that
file's own header comment).

## 4. How to validate `cloud_telemetry_node.py` itself (next step, needs ROS2)

This repo's dev machine has no ROS2 install, so none of this was run —
written as the concrete next step, not as something already done.

### 4.1 What you need

- A ROS2 Jazzy environment with this workspace built
  (`colcon build --symlink-install --packages-select cloud_bridge`, or the
  whole workspace). The devcontainer (`.devcontainer/`) or
  `Containerfile`'s `dev` target both give you this; a bare WSL2 + ROS2
  Jazzy install works too.
- `pip install websockets` inside that environment if using the `dev`
  Containerfile target directly — the `prod` target already has it, `dev`
  does not run that pip-install layer.
- **No Jetson, no GPU, no real sensors, no Cloudflare deployment needed
  for this step.**

### 4.2 The tool already written for this: `fake_sensors_node`

`src/comms/cloud_bridge/cloud_bridge/fake_sensors_node.py` (new this
session) publishes synthetic messages on every single topic
`cloud_telemetry_node` subscribes to — a moving GPS track, fake tracked
LiDAR hazards, a fake SAM2 mask, a fake camera frame, fake battery and
sonar data. Run it standalone, **not** via `njord.launch.py` (keep fake-
data tooling out of the production bringup file on purpose):

```bash
ros2 run cloud_bridge fake_sensors_node
```

Then, in another terminal, run the real node against it, pointed at
either today's local relay or the actual Worker once deployed:

```bash
ros2 run cloud_bridge cloud_telemetry_node --ros-args \
  -p cloud_ws_url:="ws://<relay-host>:8787/api/v1/telemetry/stream?role=asv"
```

(Reuse today's exact setup for this: start `relay_server.py` and open
`index.html` pointed at `ws://127.0.0.1:8787/...?role=browser` again — if
real ROS-sourced data renders identically to today's dummy run, the whole
chain from ROS topic to pixel is proven.)

### 4.3 The exact topic contract (from `cloud_telemetry_node.py`, verified by reading the code, not guessed)

| Topic | Type | QoS | Gates output? |
|---|---|---|---|
| `/gps_driver/gps_raw` | `sensor_msgs/NavSatFix` | BEST_EFFORT, depth 1 | **Yes** — `dispatch_packet()` sends nothing at all until this has arrived once |
| `/odometry/filtered` | `nav_msgs/Odometry` | BEST_EFFORT, depth 1 | **Yes** — `lidar_clusters` stays empty until this has arrived once (needed to turn map-frame detections into a boat-relative + absolute position) |
| `/mavros/global_position/raw/gps_vel` | `geometry_msgs/TwistStamped` | BEST_EFFORT, depth 1 | No — `sog`/`cog` just stay at 0.0 |
| `/mavros/gpsstatus/gps1/raw` | `mavros_msgs/GPSRAW` | BEST_EFFORT, depth 1 | No — `rtk_mode` stays 0 (no fix) |
| `/obstacles/fused_tracked` | `vision_msgs/Detection3DArray` | default (RELIABLE), depth 10 | No — `lidar_clusters` stays `[]` |
| `/perception/sam2_masks` | `sensor_msgs/Image` (mono8) | BEST_EFFORT, depth 1 | No — `sam2_polygons` stays `[]` |
| `/camera/front/image_raw` | `sensor_msgs/Image` (bgr8) | BEST_EFFORT, depth 1 | No — `image_jpeg_b64` is simply omitted |
| `/battery/state` | `sensor_msgs/BatteryState` | default (RELIABLE), depth 10 | No — health fields stay `null` |
| `/sonar/decimated` | `std_msgs/String` (JSON) | BEST_EFFORT, depth 1 | No — `sonar` key is omitted entirely |

**Only the first two actually gate output.** Everything else degrades
gracefully to an empty/null/omitted field — this was a deliberate design
choice (see that node's own docstring), so a partial bring-up (e.g. GPS
and odometry up, camera not yet wired) still produces a valid, useful
packet instead of nothing.

`class_id` on `Detection3DArray.results[0].hypothesis` must be in
`"<CLASS>:<id>"` form (e.g. `"KELP_CANOPY:101"`) — that's
`geo_fusion_tracker_node.cpp`'s actual convention for carrying a stable
numeric track id without relying on an unverified `vision_msgs` field (see
that file's comment). `fake_sensors_node.py` already follows this.

### 4.4 Checklist

1. `colcon build --packages-select cloud_bridge` succeeds.
2. `ros2 run cloud_bridge cloud_telemetry_node` starts with no exceptions
   and logs `Cloud telemetry bridge operational at 5.0 Hz -> ...` even
   with nothing else running yet (it should just idle — no GPS fix means
   no packets sent, which is correct, not a hang).
3. Start `fake_sensors_node`. Within ~1s, `cloud_telemetry_node` should
   start dispatching — confirm with a `role=browser` test client (same one
   piece of Python used throughout today's test) that `nav.lat`/`nav.lon`
   are non-null and changing.
4. Confirm `lidar_clusters` has 2 entries (`KELP_CANOPY`, `BUOY` — note:
   `BUOY_OBSTACLE` internally, mapped to `BUOY` for the wire format via
   `geo_utils.CLASS_MAP`) with sane `centroid_global` values near
   Shearwater Bay.
5. Confirm `sonar` appears once `fake_sensors_node`'s `/sonar/decimated`
   publisher is running, and is absent if you kill that one publisher
   specifically (proves the graceful-omission path, not just the happy
   path).
6. Confirm `image_jpeg_b64` appears on roughly 1 in 5 packets.
7. Point the dashboard at this ROS-sourced stream and visually compare
   against today's dummy run — map, hazards, sonar waterfall, and camera
   overlay should all look the same *kind* of output, with real node code
   underneath this time.

### 4.5 What this step still won't prove

Real GPS/IMU/LiDAR/sonar hardware, a real Jetson, the actual radio link
over water, or a deployed Cloudflare Worker — those are each their own
later step (bench tests in `KELP_CAPABILITIES.md` §5.7 cover the hardware
side; §6 below covers the Worker).

## 5. Artifacts from today's test (not committed — scratch only)

Everything below lived in `scratchpad/cloud_test/` for this session and
was torn down afterward — nothing of this was added to the actual repo
except the reference doc you're reading and `fake_sensors_node.py`:

- `relay_server.py` — local stand-in for the Worker's routing logic.
- `dummy_asv_publisher.py` — synthetic `role=asv` sender (JSON-layer only).
- `index.html` — a copy of `src/frontend/index.html` with `wsUrl`
  overridden to the local/tunnel address (the real repo file still points
  at the real production placeholder domain).
- `test_relay.py` — the functional test that actually proved broadcast
  routing and late-joiner hydration both work (not just "no exception
  thrown" — asserted the exact received payloads).

If this needs re-running: recreate these four files from this doc's
description (they're short) or ask for them to be regenerated — the logic
in `relay_server.py` is simple enough that it's reasonable to treat this
doc as the source of truth rather than keeping dead scratch files around.

## 6. Remaining gap: the real Cloudflare Worker deployment

Not attempted this session (needs the paid Workers plan for Durable
Objects, which was explicitly ruled out). If/when that constraint changes:

```bash
cd cloudflare
wrangler login       # interactive, needs a real browser + your Cloudflare account
wrangler deploy
```

`wrangler.toml` already has the Durable Object binding configured (see
that file's own comments — the original design doc never provided this
binding config at all, which was a separate bug fixed last session). After
deploying, `wrangler` prints the real `*.workers.dev` URL — that replaces
the placeholder `telemetry.asket-namibia.internal` domain in both
`src/comms/cloud_bridge/config/cloud_bridge_params.yaml` and
`src/frontend/index.html`.

**Alternative if the card requirement is a hard no going forward:** keep
using `relay_server.py` + Cloudflare Tunnel permanently instead of
Workers+Durable Objects — run `relay_server.py` as a small always-on
process on the shore ground-station laptop (not the boat; it just needs to
be reachable), and `cloudflared tunnel` (a **named** tunnel this time, not
a quick one, for a stable URL — still free, ties to your Cloudflare
account but not to a paid plan) to expose it. This is a legitimate
permanent architecture, not just a test harness — Durable Objects buy
globally-distributed low-latency fan-out, which matters at scale, not for
one boat and a handful of dashboard viewers.
