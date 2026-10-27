# Hardware decisions — ASKET, Namibia (Kelp Blue)

The reference for what is actually on the boat, agreed by the team on
2026-10-26. When code and this file disagree, this file states the intent and
the code is the thing to change (or this file is, after a team decision).
Items marked **TO VERIFY** are believed but not confirmed on the hardware;
**OPEN** items have no decision yet.

Branch history: written on `hw-alignment`, based on `GUI_AUXI` (mission GUI
ported from node-ros-2026 `1606eb7`, see `docs/gui/PORTING_NOTES.md`).

## 1. Decisions

| Area | Decision |
|---|---|
| Autopilot | **Pixhawk 4.** Provides position, heading and IMU through MAVROS. **It does not drive the motors.** Firmware believed to be PX4 by the team, but the code assumes ArduPilot Rover (`README.md:40` records `FRAME_CLASS=2`, an ArduPilot parameter, read off the real Pixhawk) — **TO VERIFY**. |
| Propulsion | 2× Blue Robotics T200 + 2× Blue Robotics Basic ESC 30A (bidirectional). ESC signal wires go to the **Pico** (GPIO15 left, GPIO16 right). |
| Low-level controller | Raspberry Pi Pico 2. Firmware flashed: **pico-node_v4** — **TO VERIFY** by a `STATE ver=4 …` line on `/pico/status`. |
| Radio | Radiomaster MT12 + RP4TD ExpressLRS 2.4 GHz receiver, **set to SBUS output** (the firmware decodes inverted SBUS only). Behaviour on radio loss (failsafe flag vs. silence) — **TO VERIFY on the bench**. |
| ESC relay / E-stop feedback | Relay (A3KM, 47 Ω precharge) and E-stop button feedback **not wired yet**. Pins kept as in v4: GPIO21 relay, GPIO20 E-stop feedback, GPIO10/11/12 lights, GPIO13 buzzer. |
| Signalling | 3 lights (green/yellow/red) as in the firmware. |
| Energy | 2× LiPo 4S 8000 mAh **in parallel** ≈ 2 × 14.8 V × 8 Ah ≈ **237 Wh**. Probably **2 BQ76920 BMS** boards, one per pack — **TO VERIFY**. |
| GNSS | Holybro H-RTK Unicore UM982 (dual antenna, heading) **on the Pixhawk**. RTK base Holybro F9P ashore; corrections wanted along base → shore laptop → WiFi → Jetson → MAVROS → Pixhawk (**not implemented**, see Phase B). |
| Sonar | **One** Cerulean Omniscan 3D 450 SS Modular, run by **SonarView** (Cerulean, ideally in Docker on the Jetson) on a **separate screen**. The mission GUI shows at most a simple health state. |
| Camera | **OAK-D-LR** over PoE replaces the USB camera, which is no longer used. Object recognition by camera only. **No driver in the image yet**; prepared behind `enable_oak` (off), see `docs/CAMERA_OAK.md`. |
| Lidars | **No Livox and no RPLidar on board for now.** ⇒ **The boat has no obstacle detection.** |
| BlueOS / Raspberry Pi 5 | **Not used.** |
| Network | MikroTik CSS610 switch + NetMetal 5 ax (boat) + mANTBox ax 15s (shore). IP plan below. |
| USB | Pico, BMS and Pixhawk all go through one USB hub ⇒ **udev rules** with stable names (`deploy/udev/`). |
| Cloud | Cloudflare relay probably used. **Read-only**: no command reaches the boat from the internet. |
| LTE beacon | LilyGO T-SIM7000G — **OPEN** (not decided). |

## 2. IP plan — `10.10.20.0/24`, one flat network bridged over the WiFi link

| Address | Device |
|---|---|
| 10.10.20.2 | MikroTik CSS610 switch |
| 10.10.20.3 | MikroTik NetMetal 5 ax (boat) |
| 10.10.20.4 | MikroTik mANTBox ax 15s (shore) |
| 10.10.20.10 | Jetson AGX Orin |
| 10.10.20.21 | OAK-D-LR |
| 10.10.20.22 | reserved (Livox) |
| 10.10.20.31 | Omniscan 3D 450 SS (**ships as 192.168.2.92 — must be re-addressed**) |
| 10.10.20.32 | reserved (second sonar) |
| 10.10.20.100–199 | DHCP, shore laptops |

Mission GUI: `http://10.10.20.10:8090`. Foxglove: `ws://10.10.20.10:8765`
(firewalled to loopback by `scripts/harden-network.sh`). SonarView/SonarLink:
port 7077 on the host running SonarView.

Where the plan is written into the configuration (hw-alignment step 7):

| Value | File |
|---|---|
| Sonar `10.10.20.31` | `src/omniscan_bridge/config/omniscan.yaml` (`host`), `src/omniscan_bridge/omniscan_bridge/omniscan_bridge_node.py` (parameter default), `src/gui_backend/launch/gui.launch.py` (`sonar_host`) |
| Whole plan, for reference | `src/gui_backend/config/topics.yaml` (`network:`; nothing connects to it) |
| Shore sector mANTBox ax 15s (confirmed), boat NetMetal 5 ax | `src/gui_backend/config/topics.yaml` (`shore_station.antenna`, `vessel_radio`) — the link budget |

Re-addressing the sonar (once, on the bench): connect a laptop **by cable** to
the CSS610, run SonarView, let its discovery find the Omniscan at
192.168.2.92 and set it to 10.10.20.31 / 255.255.255.0. Discovery is a
broadcast, and WiFi bridges usually drop it — do not try it over the link.
Then check from the Jetson: `ping 10.10.20.31`.

Note: `scripts/harden-network.sh` comments describe a *routed* shore subnet
(10.10.10.0/24 via 10.10.99.0/30) from an earlier design note. The plan above
is a single bridged subnet. The script's actual rule (Foxglove reachable from
loopback only) does not depend on either.

Deploying on the Jetson: stable USB names in `deploy/udev/README.md`, and the
systemd unit that keeps missions, pre-flight history, map tiles and the Setup
file across container restarts (host `/srv/asket/data` on `/data`) in
`deploy/jetson/README.md`.

## 3. Component table (state of the `hw-alignment` branch)

Status: ✅ integrated and testable · ⚠️ code present but incomplete · ❌ expected, no code · — not applicable.

| Component | Link | Port / IP in code | Node → topics | Launched by default? | GUI | Status |
|---|---|---|---|---|---|---|
| Pixhawk 4 | USB (via hub), MAVROS straight to it since Phase B (d): `fcu_url` `/dev/pixhawk:115200`, `autopilot:=ardupilot\|px4`; `use_blueos:=true` goes back through BlueOS (`tcp://localhost:5777`) | `/dev/pixhawk` (udev) | `mavros` → `imu_gps_driver` → `/imu_driver/imu_raw`, `/gps_driver/gps_raw`; EKF → `/odometry/filtered` | yes (`enable_mavros`) | Vessel, Heading, map | ⚠️ firmware (PX4 / ArduPilot) still to confirm: §4c |
| UM982 GNSS (dual antenna) | to the Pixhawk | — | via MAVROS as above | — | Heading (source shown as EKF) | ⚠️ ArduPilot/PX4 GPS-yaw config to verify |
| F9P RTK base | shore laptop → WiFi → Jetson | — | none (MAVROS `gps_rtk` plugin not fed) | — | — | ❌ (Phase B) |
| Pico 2 | USB CDC 115200 (via hub) | `pico_port` default `/dev/pico` (udev, `deploy/udev/`) | `pico_bridge` → `/pico/status`; `/pico/mode_request`, `/control/effort` in | **yes** (`use_pico_bridge:=true` is the default since Phase B a) | Mode, Vessel, pre-flight | ⚠️ bench test first |
| 2× T200 + Basic ESC 30A | PWM from Pico GPIO15/16, 1000–2000 µs, 1500 neutral | `firmware/pico-node_v4/pico-node_v4.ino` | firmware | always (firmware) | Vessel | ⚠️ motor path default (Phase B) |
| RP4TD (ELRS) + MT12 | SBUS into Pico GPIO5 (inverted, 100 kbaud 8E2) | firmware; Ch3 throttle, Ch4 yaw, Ch7 arm, Ch8 mode | firmware | always | Vessel (RC) | ⚠️ SBUS output and failsafe behaviour to verify |
| ESC relay (A3KM) | Pico GPIO21 | firmware | firmware | — | Vessel ("ESC power") | ❌ not wired |
| E-stop button feedback | Pico GPIO20 (divider) | `ESTOP_FEEDBACK_ENABLED 0` | firmware | — | "not sent" | ❌ not wired |
| 3 lights + buzzer | Pico GPIO10/11/12, GPIO13 | firmware | firmware | always | — | ✅ (firmware) |
| BMS pack 1, pack 2 (BQ76920 via MCP2221) | USB-UART 115200 (via hub) | `bms_port` `/dev/bms_pack1`, `bms2_port` `/dev/bms_pack2` (udev) | `bms_reader` → `/battery/pack1` (+ `/battery/pack2` with `enable_bms2:=true`); `battery_aggregator` → `/battery/state` | no (`enable_bms:=false`); only one BMS wired, so `enable_bms2` defaults to false | Power, status strip | ⚠️ state of charge estimated from cell voltage; with one BMS the second pack is not measured |
| 2× LiPo 4S 8000 mAh (parallel) | — | GUI capacity 237 Wh (`src/gui_backend/config/topics.yaml`) | — | — | Power | ✅ config |
| Omniscan 3D 450 SS Modular | Ethernet | 10.10.20.31 (`src/omniscan_bridge/config/omniscan.yaml`), UDP 62312 | `omniscan_bridge` → `/sonar/status` (optional health only) | no (`enable_sonar:=false`) | Sonar (health only) | ⚠️ SonarView owns the sonar |
| SonarView | Docker on the Jetson (OPEN) | SonarLink `ws://127.0.0.1:7077` (`src/comms/cloud_bridge/config/cloud_bridge_params.yaml`) | `sonar_bridge_node` → `/sonar/decimated` (cloud) | no | — | ⚠️ |
| OAK-D-LR | PoE Ethernet | 10.10.20.21 (`enable_oak`, off) | `/oak/rgb/image_raw/compressed` for the GUI (to verify); SAM2 expects `/camera/front/image_raw` | — | Camera: "Camera not connected" | ❌ driver not installed |
| USB camera | — (not used) | `/dev/video0` | `camera_driver` | **no** (`enable_usb_camera:=false`) | — | — |
| RPLidar S2 | — (not on board) | `/dev/ttyUSB0` | `sllidar_node` | **no** (`enable_lidar:=false`) | Obstacles: "not fitted" | — |
| Livox Mid-360S | — (not on board) | 10.10.20.22 reserved | none | — | — | ❌ no driver |
| CSS610, NetMetal 5 ax, mANTBox ax 15s | Ethernet / WiFi | see §2 | none (radio not read) | — | Link (predicted signal) | ⚠️ |
| USB hub | — | — | — | — | — | — (udev rules) |
| Cloudflare relay | outbound WebSocket | `wss://telemetry.asket-namibia.internal/...` | `cloud_telemetry_node` (read-only) | no | — | ⚠️ |
| LTE beacon (LilyGO) | OPEN | — | none | — | link profile "minimal" exists | ❌ OPEN |

## 4. Wiring (from the code and the decisions)

```
                     shore                                  │                    boat
                                                            │
 F9P base ──USB── laptop(s) 10.10.20.1xx ── mANTBox ax 15s ~~~~ NetMetal 5 ax ── CSS610 switch
                    │  browser :8090 (GUI)    10.10.20.4    │  10.10.20.3        10.10.20.2
                    │  SonarView screen                     │                      │
                    │                                       │      ┌───────────────┼──────────────┐
                    │                                       │  Omniscan 3D     OAK-D-LR      Jetson AGX Orin
                    │                                       │  10.10.20.31     10.10.20.21   10.10.20.10
                    │                                       │  (SonarView)     (PoE, no      │ ROS 2 (Podman)
                    │                                       │                   driver yet)  │
                    │                                       │                     USB hub ───┤
                    │                                       │        ┌──────────────┼──────────────┐
                    │                                       │    Pixhawk 4      Pico 2        2× BMS BQ76920
                    │                                       │   /dev/pixhawk   /dev/pico     /dev/bms_pack1,2
                    │                                       │   UM982 (GPS)      │
                    │                                       │                    ├─ SBUS ◄── RP4TD (ELRS) ◄~~ MT12
                    │                                       │                    ├─ PWM GPIO15/16 ─► ESC ─► T200 ×2
                    │                                       │                    ├─ GPIO21 ─► relay A3KM (not wired)
                    │                                       │                    ├─ GPIO20 ◄─ E-stop feedback (not wired)
                    │                                       │                    └─ GPIO10/11/12/13 ─► lights, buzzer
                    │                                       │  E-stop button ── cuts ESC power, independent of software
 Cloudflare relay ◄─(outbound, read-only)── Jetson (when enable_cloud_bridge:=true)
```

## 4b. Nav2 survey profile (Phase B f) — no obstacle sensors

`njord.launch.py nav2_profile:=survey` (default) loads
`src/bringup/config/nav2_params_survey.yaml`. `nav2_profile:=full` keeps
`nav2_params.yaml`, built for the SAM2/fusion obstacle pipeline (Livox +
OAK-D-LR, not fitted). The survey profile knows two things: where the boat may
go, and how fast. **It does not know about anything in the water.**

| What | Value | Where | Why |
|---|---|---|---|
| Obstacle layers | none, in both costmaps | `global_costmap`, `local_costmap` `plugins` | nothing aboard detects obstacles |
| Geofence | `geofence_layer`: `nav2_costmap_2d::KeepoutFilter`, `filter_info_topic: /geofence/filter_info` | both costmaps | outside the polygon is lethal: nothing is planned or followed out of it |
| Geofence polygon | `/data/setup/geofence.yaml` (`geofence_file`), lat/lon corners | `sensors/geofence_mask_node`, example `src/bringup/config/geofence.example.yaml` | site-specific; the mask is placed in the `map` frame once the Pixhawk home position is known (the same origin `datum_sync` uses) |
| Lethal ring around the fence | 30 m (`margin_m`), mask 1 m cells (`resolution_m`) | `geofence_mask_node` parameters | beyond the mask Nav2 applies no keep-out; the ring is what blocks a path that would leave and come back |
| Inflation | 5 m global, 2 m local, `cost_scaling_factor` 3 | `inflation_layer` | keeps the path off the fence line |
| Cruise speed | `desired_linear_vel: 0.8` m/s | `controller_server.FollowPath` (Regulated Pure Pursuit) | survey pace with nobody aboard and no obstacle detection |
| Speed cap (hard) | `max_velocity: [0.8, 0.0, 0.6]`, `min_velocity: [0.0, 0.0, -0.6]` | `velocity_smoother` | after the controller: ≤ 0.8 m/s, no reverse, ≤ 0.6 rad/s |
| Acceleration | `max_accel: [0.3, 0.0, 0.5]`, `max_decel: [-0.5, 0.0, -0.5]` | `velocity_smoother` | gentle on the props and the ESCs |
| Slow in turns / near the fence | `use_regulated_linear_velocity_scaling` (min radius 3 m, min 0.25 m/s), `use_cost_regulated_linear_velocity_scaling` | `FollowPath` | |
| Reverse | `allow_reversing: false` | `FollowPath` | |
| collision_monitor | in the chain, every polygon and source **disabled** | `collision_monitor` | passes commands through; it is **not** a safety layer in this profile |

Measured in a Nav2 Jazzy (1.3.13) image with a fence edge 60 m east of the
home: all nine Nav2 nodes active; a cell 70 m east is lethal; a plan to 30 m
east succeeds, a plan to 100 m east is refused (no valid path); 2 m/s and
2 rad/s in come out as 0.80 m/s and 0.60 rad/s.

Pre-flight: `nav.geofence` is **critical** when the survey profile runs — no
fence (or no home position yet) is NO-GO, because Nav2 would then have no area
limit at all. It reads "Not checked" with `enable_nav2:=false` or
`nav2_profile:=full`.

Operating rules this profile assumes: draw the fence around open water only
(not over moorings, jetties or the shore); start every mission inside it; keep
the boat in sight with the transmitter in hand — Ch8 low, or a lost RC link,
stops it in firmware (`pico-node_v5`).

Two faults in `nav2_params.yaml`, found while testing this and fixed in both
files, had kept Nav2 from starting at all: collision_monitor polygon `points`
written as a nested YAML list (Jazzy wants a string — the generated params file
was unparseable and every Nav2 node exited), and a `KeepOutFilter` class that
does not exist (`KeepoutFilter`).

## 4c. Pixhawk 4 without BlueOS (Phase B d) — PX4 or ArduPilot

MAVROS opens the Pixhawk itself on `/dev/pixhawk` (USB). Which firmware it
runs is not yet confirmed, so both are prepared and `njord.launch.py
autopilot:=ardupilot|px4` (default `ardupilot`) selects
`config/mavros_<autopilot>.yaml` and whether MAVROS requests stream rates.
The pre-flight check **Flight controller firmware** (`autopilot.match`,
critical) reads the Pixhawk's own HEARTBEAT and fails if it is the other one;
the Boat panel's Details show the detected firmware.

### What the stack reads from MAVROS, and where each comes from

Checked in the ROS image (launch × firmware, a fake flight controller sending
heartbeats): every topic below has a MAVROS publisher with both
`autopilot:=ardupilot` and `autopilot:=px4`. Whether **data** flows depends on
the Pixhawk sending the MAVLink message — the last two columns.

| Topic | Read by | MAVLink message | ArduPilot | PX4 |
|---|---|---|---|---|
| `/mavros/global_position/raw/fix` | imu_gps_driver → `/gps_driver/gps_raw` → GUI position, navsat_transform | GPS_RAW_INT | after `set_stream_rate` (done by the launch) | streamed on USB |
| `/mavros/global_position/global` | pre-flight GNSS, recorder | GLOBAL_POSITION_INT | after `set_stream_rate` | streamed |
| `/mavros/global_position/raw/satellites`, `raw/gps_vel` | pre-flight, recorder | GPS_RAW_INT | after `set_stream_rate` | streamed |
| `/mavros/gpsstatus/gps1/raw` | GUI fix quality (RTK fixed/float), satellites, HDOP; pre-flight | GPS_RAW_INT | after `set_stream_rate` | streamed |
| `/mavros/imu/data` | imu_gps_driver → EKF (heading, attitude), pre-flight roll, recorder | ATTITUDE (+ quaternion), SCALED/HIGHRES_IMU | after `set_stream_rate` | streamed |
| `/mavros/time_reference` | pre-flight GPS clock | SYSTEM_TIME | after `set_stream_rate` | streamed |
| `/mavros/home_position/home` | datum_sync → map frame → geofence mask | HOME_POSITION | MAVROS polls for it until answered | streamed |
| `/mavros/state` | (diagnostics) | HEARTBEAT | yes | yes |
| `/uas1/mavlink_source` | autopilot_probe → `autopilot.match` | HEARTBEAT (raw) | yes | yes |

What is missing, for one or the other:

- **ArduPilot:** nothing beyond the stream request the launch already makes
  (`/mavros/set_stream_rate`, all streams at 10 Hz, 10 s after start). If a
  topic stays silent, raise `SR0_*` (the USB port's stream rates) instead.
- **PX4:** `set_stream_rate` is not sent (PX4 ignores REQUEST_DATA_STREAM).
  The USB link starts PX4's MAVLink in *config* mode, which is expected to
  stream all of the above (to verify on the real Pixhawk: `ros2 topic hz`
  on each). If the Pixhawk is ever wired on TELEM instead of USB, set that
  instance's `MAV_x_MODE` to *Onboard* and its rate, or nothing arrives.
- **Both:** *RTK fixed / RTK float* in the GUI needs RTCM corrections reaching
  the UM982 — Phase B (e), not done (note below). Until then the best is
  *GPS only*. The dual-antenna heading needs the settings in §4d; the
  stack does not read the GNSS yaw separately: it arrives through the
  flight controller's attitude (`/mavros/imu/data`) once the FC fuses it.
- **Both:** the EKF (`robot_localization`) and navsat_transform need no change
  between the two: MAVROS gives an ENU `/mavros/imu/data` and a NavSatFix in
  both cases.

**RTK corrections (Phase B e), not done.** What it will need: a correction
source (a base station or an NTRIP caster on the shore laptop), MAVROS's
`/mavros/gps_rtk/send_rtcm` (plugin `gps_rtk`, not on the denylist) or
QGroundControl's RTK injection through the relay (`enable_gcs_relay`), and
on the FC side ArduPilot `GPS_INJECT_TO` / PX4's injection on the GPS port.

## 4d. Settings to make in the Pixhawk, per autopilot

Done once, from QGroundControl on the shore laptop: either with the Pixhawk's
USB on the laptop, or through the Jetson with `enable_gcs_relay:=true`
(QGroundControl connects by itself on UDP 14550). None of this has been
tried on this Pixhawk yet.

### Which firmware is it?

- **QGroundControl:** *Vehicle Setup → Summary* names the firmware
  (ArduPilot Rover / PX4) and its version, and the setup pages differ (PX4
  has *Airframe*; ArduPilot has *Frame* and *Servo Output*). *Analyze Tools →
  MAVLink Inspector → HEARTBEAT → autopilot*: 3 = ArduPilot, 12 = PX4.
- **On the Jetson, before launching:** USB IDs (`deploy/udev/README.md`):
  `26ac:0032` is PX4, `1209:5740`/`1209:5741` is ArduPilot.
- **Running:** the pre-flight row *Flight controller firmware*, and *Autopilot*
  in the Boat panel's Details.

Then launch with the matching `autopilot:=`.

### ArduPilot (Rover 4.5 / 4.6)

USB and MAVLink:

| Parameter | Value | |
|---|---|---|
| `SERIAL0_PROTOCOL` | 2 (MAVLink 2) | the USB port; default |
| `SR0_POSITION` | 5 | GLOBAL_POSITION_INT |
| `SR0_EXT_STAT` | 2 | GPS_RAW_INT, SYS_STATUS |
| `SR0_EXTRA1` | 10 | ATTITUDE (heading, roll) |
| `SR0_RAW_SENS` | 10 | IMU |
| `SR0_EXTRA3` | 1 | SYSTEM_TIME (the pre-flight's GPS clock) |

The launch also asks for every stream at 10 Hz at start-up
(`set_stream_rate`); these make the rates survive without it.

UM982, one module, two antennas, **no magnetometer** (ArduPilot "GPS for
yaw"). Parameter names are `GPS1_*` from 4.6; up to 4.5 they are `GPS_TYPE`,
`GPS_MB1_TYPE`, `GPS_MB1_OFS_X/Y/Z`.

| Parameter | Value | |
|---|---|---|
| `SERIAL3_PROTOCOL` | 5 (GPS) | Pixhawk 4 "GPS module" port = SERIAL3 |
| `SERIAL3_BAUD` | 230 (230 400) | the UM982's configured rate (Holybro ships 230 400) |
| `GPS1_TYPE` | 25 (UnicoreMovingBaseline) | 24 would be position only, no yaw |
| `GPS1_MB_TYPE` | 1 | shows the offset parameters |
| `GPS1_MB_OFS_X/Y/Z` | measured, m | master relative to slave: +X master ahead, +Y master to the right, +Z master below. Antennas **≥ 30 cm** apart |
| `GPS1_POS_X/Y/Z` | measured, m (optional) | master antenna from the boat's centre |
| `AHRS_EKF_TYPE`, `EK3_ENABLE`, `EK2_ENABLE` | 3, 1, 0 | EKF3 |
| `EK3_SRC1_YAW` | 2 (GPS) | 3 = GPS with compass fallback, only if a compass is kept |
| `COMPASS_USE`, `COMPASS_USE2`, `COMPASS_USE3` | 0 | no magnetometer in the heading |
| `GPS_AUTO_SWITCH` | not 2 | Blend is not allowed with moving baseline |

Check: point the boat at a distant landmark; QGroundControl's heading
matches, and follows when the boat is turned.

### PX4 (1.14 and later)

USB: nothing to set — PX4 starts MAVLink on USB by itself (*config* mode,
high rates). Only if the link is moved to a TELEM port: `MAV_1_CONFIG` = that
port, `MAV_1_MODE` = Onboard, `MAV_1_RATE` = 0 (maximum).

UM982 on the *GPS 1* port, dual-antenna heading:

| Parameter | Value | |
|---|---|---|
| `GPS_1_CONFIG` | GPS 1 | the port the UM982 is on |
| `SER_GPS1_BAUD` | 230400 | |
| `GPS_1_PROTOCOL` | 6 (NMEA) | Unicore = NMEA + Unicore sentences |
| `EKF2_GPS_CTRL` | bit 3 set (+8), e.g. 15 | dual-antenna heading into the yaw estimate |
| `GPS_YAW_OFFSET` (1.14) | 0 if the primary antenna is ahead; 90 if it is to the right (clockwise) | |
| `SENS_GNSS0_HDG`, `SENS_GNSS0_OFFX/Y/Z`, `SENS_GNSS0_AUXX/Y/Z` (newer releases) | dual antenna; primary and secondary antenna positions | replace `GPS_YAW_OFFSET` |
| `SYS_HAS_MAG` | 0 | no magnetometer. **To verify**: on some versions the EKF then refuses to arm or keeps a compass; an older alternative is `EKF2_MAG_TYPE` = None |

PX4 forum reports `EKF2_GPS_CTRL` refusing 8 alone and reverting to 15 on
some 1.14 builds — 15 (all bits) includes the heading bit and is fine.

Sources: ArduPilot "GPS for Yaw (aka Moving Baseline)"; PX4 "Holybro H-RTK
Unicore UM982"; Holybro's UM982 setup guide.

## 5. Risks to keep in front of everybody

* **No obstacle detection at all.** No lidar, no working camera pipeline.
  Nav2's only enabled obstacle layer (`sam2_obstacle_layer`) reads the SAM2
  pipeline, which is not running. Autonomous operation needs a human watch and
  a clear area. The pre-flight reports this as a warning on every run.
* **Safety chain not complete:** relay and E-stop feedback (GPIO20) are not
  wired; the RC failsafe behaviour with ExpressLRS-over-SBUS is not verified.
  `pico-node_v5` (Phase B c) now stops the boat when SBUS frames stop in any
  mode, including AUTONOMOUS, and does not re-arm on its own after a
  failsafe — **once flashed**: until then the Pico runs whatever is on it, and
  the pre-flight fails on anything but `ver=5`. See `firmware/README.md`.
* **No obstacle detection.** The Nav2 survey profile (§4b) is contained by a
  geofence and a speed limit only; anything in the water inside the fence is
  invisible to the stack.
* **Default motor path is now the Pico** (`use_pico_bridge:=true`, Phase B a),
  on `/dev/pico`. `actuator_driver` (Pixhawk RC override) runs only with
  `use_pico_bridge:=false`. A simulation (`use_sim:=true`) no longer starts the
  bridge unless `pico_bridge_in_sim:=true` is also given.

## 6. TO VERIFY / OPEN

| # | Item | How |
|---|---|---|
| V1 | Pixhawk firmware: PX4 or ArduPilot | USB IDs (`udevadm info`, see `deploy/udev/README.md`), or `ros2 topic echo /mavros/state --once` (`mode:` `MANUAL/HOLD/GUIDED` = ArduPilot Rover; `MANUAL/POSCTL/OFFBOARD` = PX4) |
| V2 | Pico firmware is pico-node_v4 | `ros2 topic echo /pico/status --field data` shows `STATE ver=4 …` |
| V3 | RP4TD outputs SBUS, and what it does on radio loss | bench, ESC unpowered: switch the MT12 off, watch `sbusfs=` / `sbuslost=` / `sbusok=` in the STATE line |
| V4 | Two BMS boards, one per pack | count MCP2221 devices (`ls -l /dev/serial/by-id/`) |
| V5 | MCP2221 serial numbers are unique | `udevadm info` (they are often blank or identical — then use the USB port path) |
| V6 | UM982 yaw reaches the EKF | `ros2 topic echo /odometry/filtered` heading vs. a known bearing |
| V7 | Sonar re-addressed to 10.10.20.31 | SonarView device discovery |
| V8 | SonarView and `omniscan_bridge` can both read the sonar | only if `enable_sonar:=true` is ever wanted |
| O1 | LTE beacon (LilyGO T-SIM7000G) | team decision |
| O2 | Where SonarView runs (Docker on the Jetson?) | team decision |
| O3 | Cloudflare relay hostname and deployment | team decision |
