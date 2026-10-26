# Mission GUI — porting notes (node-ros-2026 → node-ros-namibia)

Branch `feature/gui-port`. Steps 1–4 of the porting plan: copy, check in
simulation, container/CI, launch. **Nothing new runs unless asked for:**
`ros2 launch bringup njord.launch.py` with no options starts exactly the same
nodes as before this branch.

> **Hardware reference:** `docs/HARDWARE_DECISIONS.md` (team decisions of
> 2026-10-26: one sonar run by SonarView, no lidar on board, OAK-D-LR instead
> of the USB camera, IP plan, udev names, wiring diagram). Where the notes
> below assume other hardware, that file wins. The `hw-alignment` branch
> (after this port) aligns the code with it; see the "Status after
> hw-alignment" column added to §4.

## 1. What was copied, and from where

Source: `NODE-Engineering-Club/node-ros-2026` at **`1606eb7`** (merge of PR #20,
"Gui namibia"). Copied with `git archive`, so only tracked files came over.

| Path in this repo | Notes |
|---|---|
| `src/asket_interfaces/` | GUI messages and services. No name clash with `njord_msgs` (only `StartMission.srv` exists in both, as two distinct packages: `njord_msgs/srv/StartMission` and `asket_interfaces/srv/StartMission`). |
| `src/asket_common/` | ROS-free shared maths, including the Python mirror of the Pico's mode arbitration |
| `src/asket_sim/` | Simulated sources (used by `--sim` and the tests) |
| `src/omniscan_bridge/` | Omniscan 3D Ping Protocol bridge (`OS3D_POINT_SET`, id 3104). **Not started by default.** |
| `src/mission_recorder/`, `src/system_test/` | Recording and pre-flight. Started only with `enable_gui:=true`. |
| `src/gui_backend/` | FastAPI + WebSocket backend (ROS node `gui_backend`) |
| `src/asket_gui/` | React + MapLibre frontend |
| `pytest.ini`, `conftest.py`, `.flake8` | No equivalent existed here. `pytest.ini` is scoped to the GUI packages only. |
| `docs/safety.md`, `docs/ws_protocol.md`, `docs/SETUP.md`, `docs/setup_page.md`, `docs/map_tiles.md` | No file of the same name existed, so they keep the paths the code comments refer to. |
| `firmware/pico-node_v4/`, `firmware/test/` | **Next to** `firmware/pico/asket_ec_pico.ino`, not replacing it. Needed by `src/asket_common/test/test_firmware_arbitration_matches.py`. A new `firmware/README.md` states that which firmware is flashed is to be confirmed by the team. |

Deliberately **not** copied: `src/asket_bringup/` (stand-alone launchers on a
different port), `scripts/init.sh` from node-ros-2026 (it contains a GitHub
token in clear — that token should be revoked by its owner). The copied files
were scanned for tokens, keys and passwords; none were found.

## 2. What changed in existing namibia files

| File | Change | Default behaviour |
|---|---|---|
| `src/bringup/launch/njord.launch.py` | New args `enable_gui` (default `false`) and `gui_port` (default `8090`); includes `gui_backend/launch/gui.launch.py` only when `enable_gui:=true`. Model: node-ros-2026 `njord.launch.py:90-95,543-554`. | unchanged |
| `src/gui_backend/config/topics.yaml` | Comments only. Every topic the GUI reads already has the same name and type in namibia; nothing renamed. Battery deliberately not wired (see §4). | — |
| `src/system_test/test/test_motor_path_exclusive.py` | Adapted from node-ros-2026's `motor_path` argument to namibia's `use_pico_bridge`. Same guarantee: no combination of arguments starts both `actuator_driver` and `pico_bridge`. Also pins namibia's current default (`false`, Pixhawk path). | test only |
| `Containerfile` | New stage `gui-frontend` (Node 20) runs `npm ci && npm run build`; `prod` copies the result **before** `colcon build`; `fastapi` and `uvicorn[standard]` added to pip. `prod-jetson` inherits it from `prod`. | image gains the GUI; nothing starts it |
| `.dockerignore` | Ignores `node_modules/` and the host's built `static/`, so the image always uses the frontend built inside it. | — |
| `.github/workflows/build.yaml` | `actions/checkout` now uses `submodules: recursive`, so `src/sllidar_ros2` (RPLIDAR S2 driver) is in the image. Before this, CI built with an empty submodule directory. | — |
| `.gitignore` | GUI block: `node_modules/`, built frontend, `/data/`, `*.mbtiles`… | — |

Not touched: `firmware/pico/asket_ec_pico.ino`, namibia's `pico_bridge`, Nav2
and its config, the SAM2/fusion pipeline, `cloud_bridge`/`sonar_bridge`, sensor
drivers, existing topic names, and anything in the safety chain (ESTOP, RC,
relay).

### CI and GHCR — read before merging

`build.yaml` publishes only on a **push to `main`** (`on.push.branches: [main]`,
`push: ${{ github.event_name == 'push' }}`). Pushing this branch publishes
nothing; the PR triggers a build check that does not push.

**Merging into `main` will publish** `ghcr.io/node-engineering-club/node-ros-2026:latest`
and `:<sha>`. Note the image name: it is the **same image name node-ros-2026
publishes to**. Whichever repo merged last owns `:latest`. If the Jetson pulls
`:latest` (as `scripts/init.sh`'s `njord-update.service` does), a merge here
changes what the boat runs on its next boot. Decide this before merging (§6).

## 3. How to run the GUI

### On a laptop, no ROS, no boat

```bash
pip install fastapi "uvicorn[standard]" pyyaml pytest httpx websockets
pytest                                   # GUI packages only, no ROS needed

cd src/asket_gui && npm ci && npm run dev:mock   # the interface alone, fake data in the browser
npm run build && cd ../..                # writes src/gui_backend/gui_backend/static/

PYTHONPATH=src/gui_backend:src/asket_common:src/asket_sim:src/omniscan_bridge:src/mission_recorder:src/system_test \
  python3 -m gui_backend.core.app --sim --healthy --port 8080
# then open http://localhost:8080
```

### On the Jetson (ROS)

```bash
git fetch origin feature/gui-port && git checkout feature/gui-port
git submodule update --init --recursive
cd src/asket_gui && npm ci && npm run build && cd ../..   # BEFORE colcon
colcon build --symlink-install
source install/setup.bash
ros2 launch bringup njord.launch.py enable_gui:=true
# laptop: http://<jetson-ip>:8090   (health: /api/health)
```

Inside the production image the frontend is already built; only
`enable_gui:=true` is needed. Optional: `enable_sonar:=true sonar_host:=<ip>`
starts `omniscan_bridge`; `use_pico_bridge:=true` is needed for the mode panel
to show anything (it selects the Pico motor path — a team decision, §6).

## 4. What is not wired yet (steps 5–11)

These were found in the review of node-ros-2026 and are **not fixed** on this
branch.

| Step | Item | What is wrong today | Where | Status after hw-alignment |
|---|---|---|---|---|
| 5 | Lidar orientation | URDF mounts `lidar` at +90° yaw from `base_link`; the GUI treats lidar angle 0 as the bow. An object dead ahead is drawn on the starboard beam. | `src/description/urdf/asket.urdf.xacro:231`, `src/gui_backend/gui_backend/core/adapters.py` (`obstacles_from_pointcloud`, `lidar_from_ros`) | **Moot for now**: no lidar is fitted (`enable_lidar` default false, panel "not fitted", step 3). Still wrong if a lidar is fitted. |
| 5 | Camera | No `camera` branch in `RosSource.snapshot()`: the panel stays "waiting for the first frame" on the real boat. (In this sandbox the sim camera also did not display a frame in headless Chromium although binary frames arrived — unverified.) | `src/gui_backend/gui_backend/core/ros_source.py` | **Not fixed.** The USB camera is gone (`enable_usb_camera` default false); the panel says "not fitted" (step 3). An OAK-D-LR driver and a `camera` branch are still needed. |
| 6 | Battery | No `battery` source in `topics.yaml`. Wiring `/battery/state` as-is gives 0 % and a permanent critical alarm because `bms_reader` never fills `percentage`. Compute a state of charge (e.g. from cell voltages, 4S pack) first. `bms_reader` is also off by default (`enable_bms:=false`). | `src/sensors/sensors/bms_reader.py:148-162`, `adapters.py:333`, `alarms.py` | **Fixed** (step 2): SoC from cell voltages, two packs + `battery_aggregator`, 237 Wh, `battery` source wired. Off unless `enable_bms:=true`. |
| 7 | Pico firmware & bridge | The GUI's parser expects `STATE ver=4 …` (pico-node_v4). `asket_ec_pico` emits `STATE mode=…` without `ver=`: fields parse, but the critical pre-flight check `pico.firmware_version` cannot run → **NO-GO**. Mode panel only works with `use_pico_bridge:=true`. | `src/gui_backend/gui_backend/core/pico_state.py`, `src/system_test/system_test/core/checks.py:183` | **Unchanged — Phase B** (motor path). The pre-flight now reads the v4 `STATE` line from `/pico/status` (step 5). |
| 8 | "Drop to MANUAL" confirmation | Sends `MANUAL`, but waits for `ESTOP` + latch → always reports "Not applied" although the Pico did switch. Label text "software latch" is also wrong on ROS. | `gui_backend/core/hub.py` (`CMD_CUT_PROPULSION`), `commands.py` (`propulsion_cut_confirmed`), `asket_gui/src/panels/ModeCommands.jsx` | **Fixed** (step 6): confirmed by MANUAL or ESTOP; label text corrected. |
| 9 | Recording / mission | `RosSource` never reads `/mission/state` and hard-codes `IDLE` → "Start recording" always reports failed; mission panel empty; export/delete not wired. namibia's `/mission/status` (Nav2 waypoint progress) is not shown. | `ros_source.py` (`state()`), `test_ros_command_wiring.py` `KNOWN_NOT_WIRED` | **Fixed** (step 6): `/mission/state`, stored missions from disk, export/delete wired. `/mission/status` (Nav2) still not shown. |
| 9 | Pre-flight and recorder topics | `system_test` and `mission_recorder` still subscribe to `/scan`, `/asket/heading` and `/pico/status` as `PicoStatus` (namibia: `/lidar_driver/scan_raw`, no `/asket/heading`, `/pico/status` is `std_msgs/String`) → type clash on `/pico/status`, blind pre-flight checks. | `src/system_test/system_test/system_test_node.py:106-117`, `src/mission_recorder/mission_recorder/mission_recorder_node.py:95-107` | **Fixed** (step 5): String `/pico/status`, EKF heading, `/battery/state`, `/lidar_driver/scan_raw`, satellites, GPS time; absent lidar/sonar not a NO-GO. |
| 10 | Sonar (Omniscan 3D 450 SS Modular, 2 units) | `omniscan_bridge` decodes `OS3D_POINT_SET` (3104), which is what Cerulean documents for Omniscan 3D. To settle: where the 3104 packets come from in the Modular variant (the Linux host running Cerulean's container), whether `omniscan_bridge` (UDP 62312) and SonarView/SonarLink (7077) can listen at the same time, two units (port/starboard) vs one, byte layout validated on Cerulean sample data, NTP (`SET_NTP_URL`, id 18, now listed by Cerulean). Coverage ribbon never fed in ROS mode. namibia's `cloud_bridge/sonar_protocol.py` parses `os_mono_profile` (2198) with a layout that does not match Cerulean's published one — not touched here. | `src/omniscan_bridge/`, `src/comms/cloud_bridge/cloud_bridge/sonar_protocol.py:31` | **Reduced** (step 4): one sonar, run by SonarView; the panel is health-only, address 10.10.20.31 (step 7). Whether the bridge and SonarView can share the sonar is still TO VERIFY. |
| 11 | Data persistence | Missions (`/data/missions`), tile cache (`/data/maps`) and setup page answers (`~/.ros/asket_setup.yaml`) live inside the container: lost on restart unless `/data` and `/root/.ros` are mounted as volumes. | deployment (`podman run`) | **Fixed** (step 9): `ASKET_SETUP_FILE=/data/setup/…` in the image, `deploy/jetson/asket.service` mounts `/srv/asket/data` on `/data`. |
| 11 | Ports | `python3 -m gui_backend.core.app` defaults to 8080, the ROS node to 8090; `docs/SETUP.md` §5 still says 8080 and "no internet". | `core/app.py:59`, `docs/SETUP.md` | Unchanged. |
| 11 | Access | No authentication on `/ws`; any device on the boat network can request AUTO/MANUAL (still gated by RC Ch8/Ch7 on the Pico). Foxglove can also publish `/pico/mode_request`. | `gui_backend/core/server.py` | Unchanged. |

Not part of the GUI, noticed while porting, **not changed**:

* Nav2's only enabled obstacle layer reads `/obstacles/fused_tracked_points`,
  produced by the SAM2/fusion pipeline, which `njord.launch.py` does not start
  and whose sensors (Livox, OAK-D-LR) are not installed yet. The old
  `/obstacles/fused` layer is disabled (`src/bringup/config/nav2_params.yaml:100-136`).
* **`asket_sensor_fusion` and `sam2_vision` are never built**, on `main`
  as on this branch: they sit inside other packages' directories
  (`src/fusion/asket_sensor_fusion/`, `src/perception/sam2_vision/`), and
  colcon does not look for packages inside a package. `colcon list` in the
  image lists neither, so `sam2_perception_fusion.launch.py` cannot start
  them. Moving them is a SAM2-pipeline change and was left alone.
* `prod-jetson`'s default command runs `sam2_perception_fusion.launch.py`
  **instead of** `njord.launch.py` (`Containerfile`, last line): that image
  does not start navigation, the Pico bridge or the GUI unless overridden.

## 5. What was verified on this branch

| Check | Result |
|---|---|
| `pytest` (GUI packages, Python 3.13, no ROS) | 1012 passed, 5 skipped before the frontend was installed; **1015 passed, 2 skipped** with `node_modules` present (the 3 panel-render tests need Node). Skips: root ignores permission bits; one command category covered elsewhere. |
| `make -C firmware/test` (v4 sketch compiled on the host) | all pass |
| `test_motor_path_exclusive.py` adapted test | passes; confirmed it fails on a launch file where both motor paths can start |
| `npm ci && npm run build` | builds into `src/gui_backend/gui_backend/static/` (bundle size warning only) |
| `python3 -m gui_backend.core.app --sim --healthy` | `/api/health` ok, page HTTP 200; WebSocket probe received all 10 subscribed streams at their negotiated rates; screenshot shows map, survey lines, coverage, pre-flight GO, sonar, obstacles |
| Container image, target `prod` | **built** in the sandbox from this branch's `Containerfile` (native **amd64**; CI builds **arm64** under QEMU — not run here). The `gui-frontend` stage built the GUI (`npm ci`: 91 packages; `vite build` ok); colcon: 21 packages finished, 0 failed (incl. `sllidar_ros2` from the submodule and all 8 GUI packages); `/opt/njord/gui_backend/share/gui_backend/static/index.html` present; `fastapi` 0.161.1, `uvicorn` 0.54.0. The sandbox's network proxy required a locally re-tagged base image trusting its CA; the repository's `Containerfile` was not modified for that. `prod-jetson` was not built (needs NVIDIA's apt repo). |
| `ros2 launch bringup njord.launch.py` in that image (sensors, Nav2, control, MAVROS, vision, Foxglove disabled; no hardware) | without options: `robot_state_publisher`, `twist_mux`, `static_transform_publisher` only — no GUI node. With `enable_gui:=true`: additionally `gui_backend`, `system_test`, `mission_recorder`; log `mission GUI on http://0.0.0.0:8090`; `/api/health` → `ok`, `"mode": "ros"`; page HTTP 200; boot pre-flight NO-GO (expected, nothing connected). `--show-args` lists `enable_gui` (default `false`) and `gui_port` (default `8090`). |
| `colcon build`, `ros2 launch` on the Jetson, anything with hardware | **not verified** |

## 6. Team decisions needed

1. **GHCR image name and `:latest`** — this repo and node-ros-2026 both publish
   `ghcr.io/node-engineering-club/node-ros-2026`. Which repo owns it, and does
   the Jetson pull `:latest`?
2. **Which Pico firmware is flashed**, and whether to move to `pico-node_v4`
   (same mode/arming/failsafe logic; adds the version, a non-blocking serial
   reader, and removes `BENCH_NO_RC_OVERRIDE`). Check with
   `ros2 topic echo /pico/status --field data`.
3. **Motor path default** — `use_pico_bridge` stays `false` here. The GUI's mode
   panel needs `true`.
4. **"Cut propulsion"** — keep it as "Drop to MANUAL", or add a real serial
   ESTOP path in firmware (bench decision, boat out of the water).
5. **`prod-jetson` default command** — SAM2 pipeline only, or the full stack.
6. **Sonar data path** for the Omniscan 3D 450 SS Modular pair (§4, step 10).
