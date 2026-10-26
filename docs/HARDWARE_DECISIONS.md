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
| Camera | **OAK-D-LR** over PoE replaces the USB camera, which is no longer used. Object recognition by camera only. **No driver yet.** |
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
| Pixhawk 4 | USB (via hub) — today MAVLink via `fcu_url` `tcp://localhost:5777`, a BlueOS router (`src/bringup/launch/njord.launch.py:67`) | `/dev/pixhawk` (udev) — not yet used by the launch (Phase B) | `mavros` → `imu_gps_driver` → `/imu_driver/imu_raw`, `/gps_driver/gps_raw`; EKF → `/odometry/filtered` | yes (`enable_mavros`) | Vessel, Heading, map | ⚠️ needs a serial `fcu_url` without BlueOS (Phase B) |
| UM982 GNSS (dual antenna) | to the Pixhawk | — | via MAVROS as above | — | Heading (source shown as EKF) | ⚠️ ArduPilot/PX4 GPS-yaw config to verify |
| F9P RTK base | shore laptop → WiFi → Jetson | — | none (MAVROS `gps_rtk` plugin not fed) | — | — | ❌ (Phase B) |
| Pico 2 | USB CDC 115200 (via hub) | `pico_port` default `/dev/pico` (udev, `deploy/udev/`) | `pico_bridge` → `/pico/status`; `/pico/mode_request`, `/control/effort` in | **yes** (`use_pico_bridge:=true` is the default since Phase B a) | Mode, Vessel, pre-flight | ⚠️ bench test first |
| 2× T200 + Basic ESC 30A | PWM from Pico GPIO15/16, 1000–2000 µs, 1500 neutral | `firmware/pico-node_v4/pico-node_v4.ino` | firmware | always (firmware) | Vessel | ⚠️ motor path default (Phase B) |
| RP4TD (ELRS) + MT12 | SBUS into Pico GPIO5 (inverted, 100 kbaud 8E2) | firmware; Ch3 throttle, Ch4 yaw, Ch7 arm, Ch8 mode | firmware | always | Vessel (RC) | ⚠️ SBUS output and failsafe behaviour to verify |
| ESC relay (A3KM) | Pico GPIO21 | firmware | firmware | — | Vessel ("ESC power") | ❌ not wired |
| E-stop button feedback | Pico GPIO20 (divider) | `ESTOP_FEEDBACK_ENABLED 0` | firmware | — | "not sent" | ❌ not wired |
| 3 lights + buzzer | Pico GPIO10/11/12, GPIO13 | firmware | firmware | always | — | ✅ (firmware) |
| BMS pack 1, pack 2 (BQ76920 via MCP2221) | USB-UART 115200 (via hub) | `bms_port` `/dev/bms_pack1`, `bms2_port` `/dev/bms_pack2` (udev) | `bms_reader` ×2 → `/battery/pack1`, `/battery/pack2`; `battery_aggregator` → `/battery/state` | no (`enable_bms:=false`) | Power, status strip | ⚠️ state of charge estimated from cell voltage |
| 2× LiPo 4S 8000 mAh (parallel) | — | GUI capacity 237 Wh (`src/gui_backend/config/topics.yaml`) | — | — | Power | ✅ config |
| Omniscan 3D 450 SS Modular | Ethernet | 10.10.20.31 (`src/omniscan_bridge/config/omniscan.yaml`), UDP 62312 | `omniscan_bridge` → `/sonar/status` (optional health only) | no (`enable_sonar:=false`) | Sonar (health only) | ⚠️ SonarView owns the sonar |
| SonarView | Docker on the Jetson (OPEN) | SonarLink `ws://127.0.0.1:7077` (`src/comms/cloud_bridge/config/cloud_bridge_params.yaml`) | `sonar_bridge_node` → `/sonar/decimated` (cloud) | no | — | ⚠️ |
| OAK-D-LR | PoE Ethernet | 10.10.20.21 (no driver) | expected `/camera/front/image_raw` (SAM2) | — | Camera: "not fitted" | ❌ no driver |
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

## 5. Risks to keep in front of everybody

* **No obstacle detection at all.** No lidar, no working camera pipeline.
  Nav2's only enabled obstacle layer (`sam2_obstacle_layer`) reads the SAM2
  pipeline, which is not running. Autonomous operation needs a human watch and
  a clear area. The pre-flight reports this as a warning on every run.
* **Safety chain not complete:** relay and E-stop feedback are not wired; the
  RC failsafe behaviour with ExpressLRS-over-SBUS is not verified; the
  firmware does not trigger ESTOP if SBUS frames simply stop while in
  AUTONOMOUS (see Phase B in the PR description).
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
