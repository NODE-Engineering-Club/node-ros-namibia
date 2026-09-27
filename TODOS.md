# ASKET 2.0 (Kelp Blue, Namibia) — Outstanding Work

IDs in brackets refer to the Kelp design documents in
`Mission controls and documents/`: **M0–M8** are missions in
`KELP_MISSIONS.md`; **C/P/D/R/S/E** items and bench tests **T01–T18** are in
`KELP_CAPABILITIES.md` §4.3 and §5.7.

## CRITICAL — do first

- [ ] **Revoke the GitHub token that was hard-coded in `scripts/init.sh`**
  (C9). It has been removed from the file on the `namibia-kelp-cleanup`
  branch, but it is still in the git history of every clone and of the
  public `node-ros-2026` repo. Removing it from the file does not un-leak it:
  revoke it in GitHub settings, create a new `read:packages` token, and pass
  it only through the environment (`sudo GHCR_TOKEN=<pat> bash
  scripts/init.sh`). Also note that `init.sh` writes the token into
  `/etc/systemd/system/njord-update.service` in plain text; move it to a
  root-only `EnvironmentFile=` instead. `scripts/deploy-pi.sh` passes the SSH
  password on the `sshpass` command line with host-key checking off; switch
  to SSH keys.

## Before any autonomy trial (safety chain)

- [ ] **`pid_controller` subscribes to `/imu/data`, which nothing publishes**
  (C1, bench test T16). `imu_gps_driver` publishes `/imu_driver/imu_raw`, so
  yaw-rate feedback is stuck at 0, and the stale-IMU warning never fires
  because it only checks after a first message. Change the topic and warn
  when no IMU message has ever arrived (patch in `KELP_CAPABILITIES.md`
  §5.8).

- [ ] **Finish the command watchdog** (C2, bench test T15). `pid_controller`
  already zeroes a setpoint older than 0.5 s, but it keeps publishing
  `/control/effort` at 20 Hz, so if `pid_controller` itself dies
  `actuator_driver` keeps the last RC override latched, and `pico_bridge`'s
  0.3 s timeout never fires while `pid_controller` is alive. Add
  neutral-on-silence + override release to `actuator_driver` (§5.8 patch),
  then run the kill-upstream test on each wired actuation path.

- [ ] **Enable the Nav2 `collision_monitor` stop polygon** (C3, T17).
  `FootprintApproach` is `enabled: false` in `bringup/config/nav2_params.yaml`.
  Measure the stopping distance `d_stop` on the water first (M2), then size
  the stop/slow polygons.

- [ ] **Decide and document the wired actuation path** (C4): MAVROS RC
  override (`actuator_driver`, default) or Pico 2 (`use_pico_bridge:=true`,
  firmware in `firmware/pico/`). Redraw the safety chain (Capabilities §5.3)
  from the real firmware: Ch8 LOW = ESTOP, MID = manual forced, HIGH =
  autonomy permitted; neutral on serial-heartbeat loss.

- [ ] **Thruster count vs mixer** (C5). The URDF and `pico_bridge._mix` have
  two thrusters; the Kelp BOM has three T200 + three BESC30. Spare or third
  thruster? A third changes the mixer, ESC mapping and frame type.

- [ ] **Stand-test dry-run before water** (M0)
  1. Boat on a stand, FCU + onboard computer + thrusters connected.
  2. Launch the stack. Confirm: `/mavros/state.connected=true`,
     `/imu_driver/imu_raw` + `/gps_driver/gps_raw` publishing,
     `/odometry/filtered` position updates when boat is physically carried
     a few meters outside.
  3. Call `/mission/start` with a waypoint ~10 m away. Confirm thrusters
     spin in a direction that would drive toward the goal.
  4. Call `/mission/abort`. Confirm thrusters stop within 2 s.

- [x] **Disable RPP rotate-to-heading** — done. `FRAME_TYPE` confirmed `0`
  (`FRAME_CLASS=2`, Boat) by pulling FCU params directly via
  `/mavros/param` against the real Pixhawk (2026-08-08/09 stand test) — this
  boat is normal rudder+throttle steering, not skid-steer. Set
  `use_rotate_to_heading: false` and `allow_reversing: false` in
  `bringup/config/nav2_params.yaml`'s `FollowPath` block. Re-check if the
  thruster layout changes (C5).

## Kelp mission backlog

### M0 — Harbour acceptance
- [ ] Turn the M0 go/no-go table and bench tests T01–T18 into a written
  checklist the crew runs before every sortie.
- [ ] Leak sensors and enclosure humidity/temperature publishers (§4.1 "Hull
  and enclosure").

### M1 — Manual transit + link baseline
- [ ] Log MikroTik NetMetal RSSI / rate / chain balance (RouterOS API) and
  RTCM age at 1 Hz for the range walk.
- [ ] MQTT telemetry bridge (`mqtt_bridge`, topic taxonomy in Missions §4.2,
  `(mission_id, seq)` on every message, Last Will = offline).

### M2 — Autonomous transit + collision avoidance
- [ ] Keep-out layer (L0): Nav2 keep-out costmap filter built from the Kelp
  Blue farm GeoJSON, plus a geofence check in `mission_manager` before it
  accepts waypoints. Treat farm geometry as versioned data (the farm grows
  ~4 ha/month).
- [ ] Livox Mid-360S → `livox_ros_driver2` (risk S2: not released for
  Jazzy, build from source) → point-cloud-to-laser-scan slice at waterline → publish on
  `/lidar_driver/scan_raw` so every existing consumer works unchanged
  (Capabilities §5.5). Inverted mount = roll 180°.
- [ ] OAK-D-LR RGB → `/front_camera_driver/image_raw` + `camera_info`
  (depthai-ros, Jazzy). Redo intrinsics and the LiDAR–camera extrinsic for
  the new hardware.
- [ ] Retrain the YOLO model for kelp canopy and farm demarcation buoys.
  `models/yolo26n-seg-navier.onnx` is the Njord buoy/cardinal model; update
  `buoy_*_class_id` in `njord.launch.py` and `CLASS_COLORS`/`CLASS_LABELS`
  in `src/foxglove/gui_markers.py` to the new class order.
- [ ] Speed governor and entanglement detection (commanded thrust high,
  speed-over-ground low for N s → stop, alert, hold). The BESC30 has no
  current telemetry (P14).
- [ ] Gazebo world for the farm: surface canopy patches, demarcation buoys,
  lanes, a service vessel. `collisionAvoidanceWorld.sdf` (buoy gates +
  moving vessel) is the closest existing stand-in.
- [ ] Set the sim GPS datum to the real launch / F9P base point. It is an
  approximate Lüderitz point in `basicWorld.sdf` and
  `collisionAvoidanceWorld.sdf` (was Trondheim). Re-run the "Verified
  working" sim checks in the README with the southern-hemisphere datum.

### M3 — Sonar survey, lane following
- [ ] Lawnmower lane generator: farm polygon + line spacing + range →
  waypoint list for `/mission/start`. Raise `desired_linear_vel` in
  `nav2_params.yaml` if surveying at 1.5 m/s.
- [ ] SonarView on the onboard computer (BlueOS extension on the Pi, Docker
  on a Jetson); Omniscan in a tank first (T09).
- [ ] `nmea_udp_bridge`: GGA + heading sentences from MAVROS to SonarView
  over UDP.
- [ ] `sonar_bridge`: SonarLink listen-only WebSocket → QC metrics
  (Missions §5.6) → MQTT + MCAP.
- [ ] Auto-pause the survey line (hold, not abort) on heading invalid, RTK
  lost for more than N s, or ping loss above 5 %.

### M4 — Structure verification
- [ ] Offset-pass planner (10 / 15 / 25 m from a known line, two tilts, two
  speeds) and a detection-scoring table against Kelp Blue's surveyed float
  and anchor positions.

### M6 — Drift and deviation monitor
- [ ] Node that computes cross-track error against the active leg, heading
  error, speed-made-good vs commanded and a set/drift estimate, with the
  warn/alarm thresholds in Missions §2 M6.

### M7 — Failsafe and recovery
- [ ] Escalation ladder: link lost > 10 s → hold; > 60 s → retrace;
  battery below return-energy + reserve → return; leak → stop, alert,
  return. `boat_bt` is the natural home (subtree between `GlobalSafety` and
  `MissionMonitor`).

### M8 — Post-mission offload + QA
- [ ] `rosbag2` MCAP recorder (rotated, zstd) on a local SSD, not microSD.
- [ ] Integrity-checked offload and a generated mission report.

### Explainable autonomy and telemetry (Missions §3–§4)
- [ ] `/node/decision` decision log (JSON schema in Missions §3.1), emitted
  from `boat_bt_node` on every stop/slow/divert/hold/pause.
- [ ] Health monitor (compute, power, comms, enclosure) and the alert rules
  in Missions §4.6.
- [ ] Foxglove: bind or firewall `foxglove_bridge` (port 8765, no auth) to
  the boat LAN only (C8). The missing navigation-monitor panels are listed
  in `src/foxglove/README.md`.

### Platform and integration
- [ ] Decide the onboard computer (Pi + BlueOS as in this repo, or Jetson AGX
  Orin) and its power path (Capabilities §0.5, P9). On a Jetson, `fcu_url`
  must become a serial/UDP URL (C6).
- [ ] udev symlinks by ID for the Pixhawk, Pico and RPLidar instead of
  `/dev/ttyACM0` / `/dev/ttyUSB0` (C7).
- [ ] UM982 GPS-yaw in ArduPilot (Capabilities §5.5 checklist) and RTCM
  injection through `/mavros/gps_rtk/send_rtcm`.
- [ ] Add the Kelp sensors to the URDF: UM982 antenna phase centres, Livox
  (inverted), OAK-D-LR, sonar transducers with tilt (lever arms, Capabilities
  §5.4).

## Repository housekeeping

- [ ] **Rename the Njord identifiers** once the Pi deployment can be updated
  in the same step: `njord_msgs` (used by `mission`, `fusion`, `boat_bt`,
  `foxglove`), `njord.launch.py`, `/opt/njord` (`Containerfile`,
  `entrypoint.sh`), the `njord` container and `njord.service` /
  `njord-update.service` units (`scripts/init.sh`), `99-njord.rules`, the
  devcontainer name, and the maintainer entries (`njord@stud.ntnu.no`) in
  every `package.xml` / `setup.py`.
- [ ] **Resolve the orphaned `navigation_no_collision.launch.py`**. It
  instantiates Nav2's `opennav_docking` server but is not included by
  `njord.launch.py` or referenced anywhere. Delete it or repurpose it.
- [ ] **Move or rename `Mission controls and documents/`** to a path without
  spaces (e.g. `docs/kelp/`) if tooling ever needs to reference it.

- [ ] **`datum_sync` hard-depends on real mavros `/mavros/home_position/home`,
  breaking any Pico-only (no Pixhawk in the loop) run's GPS-waypoint
  navigation** — found 2026-08-12 during a hybrid bench test.
  `sensors/datum_sync.py` only calls `/datum` on `navsat_transform_node`
  when it receives a `HomePosition` from mavros; with `enable_mavros:=false`
  (real Pico for actuation, Gazebo for GPS/perception, no Pixhawk) it never
  fires, so `navsat_transform_node` falls back to a (0,0,0) datum and every
  `/fromLL` conversion comes out wildly wrong — confirmed live:
  `bt_navigator` computed a goal ~700 km away and immediately aborted.
  Worked around with a one-off manual `/datum` service call from the live
  GPS fix. Needs a real fix (e.g. `datum_sync` falling back to the first live
  GPS fix when mavros home position never arrives within some timeout).

- [ ] **No automated tests for `boat_bt`**. The GlobalSafety risk scoring,
  bypass-side choice and MissionMonitor re-arm logic have only boilerplate
  lint tests. The perception package lost its only real test suite with the
  docking detectors; `lidar_obstacle_node` and `fusion_node` have none.

## Simulation Performance (no-GPU / headless sandboxes)

Found while getting a real-Gazebo run working in a GPU-less sandbox
(Njord PR #16 review):

- [x] **Gazebo sensor rendering hangs in server-only (`-s`) mode** — root
  cause identified: `-s` mode deadlocks `gz-sim`'s `Sensors` render thread
  regardless of software-rendering setup (Xvfb, `LIBGL_ALWAYS_SOFTWARE`,
  `--headless-rendering`, explicit `--render-engine-server` flags all
  tried, all hung identically at `Sensors.cc: Waiting for init`). **GUI-
  attached mode (`headless:=false`) works** — same software (llvmpipe)
  rendering underneath, just not server-only. Real GPU rendering was never
  tested here (no GPU in this sandbox); untried but promising: enabling
  actual GPU passthrough (`--gpus=all`) in `.devcontainer/devcontainer.json`
  for machines that have one — WSL2 + Docker Desktop should support this
  natively for an NVIDIA GPU. `runArgs` currently requests none at all.

- [ ] **Real-time factor is very low under software rendering** — measured
  directly (sim `/clock` vs wall clock): RTF ≈ 0.08 (~12x slower than
  real-time) with `headless:=false` + camera/gpu_lidar sensors active.
  Since BT/mission timeouts are sim-time durations,
  this makes a "3 second" timeout take ~35 real seconds — painful for
  interactive testing, though the underlying control logic still behaves
  correctly in sim-time terms (bearing convergence traced cleanly:
  88°→18° over ~2.3 sim-seconds). Real hardware is entirely unaffected
  (no simulated rendering involved at all). Worth revisiting if GPU
  passthrough becomes available.

- [ ] **`gpu_lidar` → CPU-raycast `lidar` sensor type: tried, reverted**
  Attempted switching Asket's LiDAR sensor (`asket.urdf.xacro`) from
  `type="gpu_lidar"` to `type="lidar"` to sidestep Ogre2 rendering
  entirely for the LiDAR (cameras aren't
  needed for LiDAR-only tests). Same `<ray>` schema, should be a drop-in swap per the
  gz-sensors docs. In practice it produced zero scan data in this
  gz-sensors8 build — confirmed at both the ROS topic and native `gz
  topic` level, even 45+ seconds after spawn. Didn't dig further into
  whether this is a genuine version gap or a missing config; reverted to
  the confirmed-working `gpu_lidar`. Worth another look if someone wants
  faster headless testing and has time to debug the CPU lidar plugin
  directly (check for silent errors in `~/.gz/sim/log/*/server_console.log`
  around sensor creation, or try a minimal single-sensor test world first).

## Sensor Data Processing Tests

- [ ] **Verify `lidar_obstacle_node` output in sim**
  Launch with `use_sim:=true`, check `/obstacles/lidar` is published at ~15 Hz with `width > 0`.
  Also confirm `header.frame_id = "lidar"` and that range filtering (0.1–10 m) works correctly
  (objects at >10 m should not appear).

- [ ] **Verify `fusion_node` lidar passthrough (no YOLO)**
  With `enable_vision:=false`, `fusion_node` should echo all `/obstacles/lidar` points into
  `/obstacles/fused` with `frame_id = "base_link"`. Confirm: same point count, correct frame.
  TF lookup (`lidar → front_camera`) should succeed (check no `LookupException` in logs).

- [ ] **Verify `fusion_node` with YOLO active**
  With `enable_vision:=true`, place a visible object in Gazebo. Confirm `/yolo/detections`
  arrives, `/yolo/seg_mask` arrives, and `/obstacles/fused` combines both sources.
  YOLO-only detections (no lidar match) should appear at `DEFAULT_OBSTACLE_DISTANCE = 5.0 m`.

- [ ] **Verify EKF input rates**
  After `use_sim:=true` launch, check:
  - `ros2 topic hz /odom` → ~30 Hz (Gazebo OdometryPublisher)
  - `ros2 topic hz /imu_driver/imu_raw` → ~200 Hz (Gazebo IMU)
  - `ros2 topic hz /odometry/filtered` → ~30 Hz (EKF output)
  Low or missing rates indicate a broken bridge or plugin.

- [ ] **Verify costmap receives `/obstacles/fused`**
  After nav2 activates, echo `/local_costmap/costmap` and move a sim obstacle near the robot.
  Confirm the costmap inflates around the obstacle position reported by `/obstacles/fused`.

- [ ] **Verify sensor drivers start cleanly on hardware (no hardware attached)**
  `sllidar_node` (RPLIDAR S2, launched as `lidar_driver`) should report a clean connection failure without crashing the launch — verify actual behaviour on the S2 (untested since the A-series → S2 driver swap; the old custom driver's reconnect-loop behaviour does not carry over).
  `camera_driver` should log a degraded-mode warning without crashing.
  `imu_gps_driver` should wait for MAVROS without crashing.

- [ ] **Retune LiDAR-dependent perception params for the RPLIDAR S2**
  Swapped from the A-series (360 fixed rays, 0.2-12 m, custom driver) to the S2 (`sllidar_ros2`, DenseBoost mode, far denser point cloud, ~0.05-30 m). `lidar_obstacle_node`'s `maximum_obstacle_range_m` default was bumped 10→20 m and an `angular_decimation_deg` param was added to bound output point count (protects `geo_fusion_node`'s O(n²) Euclidean clustering from the S2's much higher native density) — needs validation on real water/buoy returns. `geo_fusion_node`'s clustering/tracking constants (`cluster_tolerance`, `min_cluster_points`, Kalman noise params) were validated against A-series density and are unchanged; they may need retuning once real S2 data is available.

## Perception / Fusion

- [ ] **Implement real late-fusion projection in `fusion_node`**
  `perception/perception/fusion_node.py` does not do actual camera projection.
  It estimates bearing from bbox centre pixel and places obstacles at a hardcoded
  5 m range. Replace with proper pipeline:
  1. Subscribe to raw `/points` (PointCloud2 from lidar) **in addition to** `/obstacles/lidar`
  2. ~~Subscribe to `/camera/camera_info` for intrinsics matrix K~~ — **Done**: `fusion_node` now subscribes to `/front_camera_driver/image_raw/camera_info` and updates fx/fy/cx/cy live.
  3. Look up `camera_optical_link → lidar_link` TF at message time
  4. Project each 3D LIDAR point onto the image plane, check if it falls inside a
     YOLO segmentation bbox (or mask when available); label matching points semantically
  5. Fall back to clustered `/obstacles/lidar` for points outside any detection

- [x] **Publish `CameraInfo` from `camera_driver`**
  Done. `camera_driver` now loads a calibration YAML via `camera_info_manager`
  and publishes `/front_camera_driver/image_raw/camera_info` on every frame.
  Run `ros2 launch bringup calibrate_camera.launch.py` to generate
  `bringup/config/front_camera.yaml`.

- [ ] **Add in-memory object persistence to `fusion_node`**
  The node is stateless — the same buoy is re-fused every frame. Add a
  lightweight object map (dict of id → position + last_seen timestamp) with
  nearest-neighbour association (threshold ~2 m) and a configurable TTL
  (e.g. 8 s). Publish map state as a separate `/obstacles/tracked` topic.

## Control

- [ ] **Verify RC channel mapping in `actuator_driver`**
  Channel indices (`CHAN_STEERING=0`, `CHAN_THROTTLE=2`) and the `RC_RANGE`
  scaling are placeholders. Confirm against the ArduPilot frame/channel
  assignment for the specific boat configuration (Rover skid-steer vs rudder+throttle).

## Navigation (Nav2)

- [ ] **Tune Nav2 controller for boat dynamics**
  `config/nav2_params.yaml` uses `RegulatedPurePursuitController`. For a USV
  with inertia and no skid-steering, evaluate switching to MPPI
  (`nav2_mppi_controller`) or tuning DWB with a diff-drive model that matches
  the boat's turning radius and maximum surge speed. At minimum, set
  `desired_linear_vel`, `lookahead_dist`, and `min_lookahead_dist` based on
  real on-water measurements.

## Calibration

- [x] **Camera intrinsic calibration**
  Done via `calibrate_camera.launch.py` — `bringup/config/front_camera.yaml`
  committed. Note: the solve's own reprojection error was never recorded (only
  shown live in the calibrator GUI, not logged, and the raw calibration images
  weren't saved anywhere persistent). Everything downstream — the LiDAR-camera
  extrinsic below, `fusion_node`'s projection — inherits whatever error is
  baked into these intrinsics. Worth redoing with more checkerboard samples
  and actually noting the on-screen error next time.

- [x] **LiDAR-camera extrinsic calibration**
  Done via `calibrate_lidar_camera.launch.py` + `ros2 run calibration
  calibrate` — `bringup/config/lidar_camera_extrinsic.yaml` committed,
  2.51 px mean reprojection error (7/12 RANSAC inliers). Not yet visually
  verified end-to-end — see below.

- [ ] **Visually verify LiDAR-camera alignment in RViz2**
  Per the README's documented last step: launch RViz2, add Image
  (`/front_camera_driver/image_raw`) + PointCloud2 (`/lidar_driver/cloud`,
  fixed frame `front_camera_cal`), confirm LiDAR points actually land on
  visible surfaces in the image. The 2.51 px figure is a curve-fit quality
  metric, not proof the whole pipeline (TF wiring, frame conventions) is
  correct end-to-end — this is the real sanity check and hasn't been run yet.

- [ ] **Live-test `fusion_node`/`geo_fusion_node` with the calibrated extrinsic**
  Both were only verified against synthetic/unit data so far
  (`src/fusion/test/test_geo_fusion.py`, and a hand-built synthetic TF for
  `geo_fusion_node.lidar_to_camera`). Run `njord.launch.py` with
  `lidar_camera_extrinsic:=$(pwd)/src/bringup/config/lidar_camera_extrinsic.yaml`
  against a real object and confirm `/obstacles/fused` and `/obstacles/global`
  report sane positions.

- [x] **Fix LiDAR mount yaw sign in URDF**
  `lidar_mount_joint` was `-90°`; verified empirically (an object measured
  dead ahead of the boat read as lidar-local `y≈-range` on
  `/lidar_driver/scan_raw` — only `+90°` predicts that sign) to be wrong, and
  fixed. This joint feeds the `base_link<->lidar` TF used by anything
  consuming `/obstacles/lidar` via tf2 (e.g. Nav2 costmap layers) — with the
  wrong sign, an obstacle dead ahead of the boat would resolve to roughly
  180° from its true position in `base_link`/`map` frame.

- [ ] **Fix URDF sensor heights to match physical hardware**
  Measured heights above hull (`base_link`):
  - LiDAR scan plane: ~52.5 mm (URDF has 174.8 mm — delta −122 mm)
  - Camera lens: ~24.5 mm (URDF has 137.3 mm — delta −113 mm)

  Update `src/description/urdf/asket.urdf.xacro`:
  - `front_camera_joint` origin z: `0.137275` → `0.0245`
  - `lidar_mount_joint`  origin z: `0.137275` → `0.015`
    (so `lidar_mount` z + `lidar_joint` z = 0.015 + 0.0375 = 0.0525)

  Verify in Foxglove/RViz2 that the sensor frames appear at the correct heights on the hull mesh.
  Note: do not change sim Gazebo sensor positions (those are set by `<pose>` in the URDF Gazebo extensions, which may differ).
  Note: does not affect the LiDAR-camera extrinsic above (solved directly from
  point correspondences, independent of URDF geometry) — but does affect the
  scan-plane guide overlay's accuracy and the nominal/uncalibrated fallback
  path (`front_camera` without `lidar_camera_extrinsic` set).

## Infrastructure

- [ ] **Bind-mount config at runtime instead of baking it in the image**
  `config/` is `COPY`-ed into the image at build time. Field params (EKF
  covariances, Nav2 speeds, waypoints) change between tests. Mount
  `./config:/config:ro` in `compose.yaml` so tuning doesn't require a rebuild.
