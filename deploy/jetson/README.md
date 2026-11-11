# Running the stack on the Jetson so that its data survives

The container is started with `--rm`, so it is thrown away every time it
stops — a crash, a reboot, an image update. Anything written inside it is
gone with it. What must be kept is all written under `/data`, and `/data` is a
directory on the Jetson's own disk, mounted into the container.

| Kept under the host's `/srv/asket/data/` | Written by | Path in the container |
|---|---|---|
| Recorded missions | `mission_recorder` | `/data/missions/` |
| Pre-flight history | `system_test` | `/data/system_test.jsonl` |
| Map tiles (offline basemap and the download cache) | `gui_backend` | `/data/maps/` |
| The Setup page's file (vessel and deployment values) | `gui_backend`, read by `omniscan_bridge` | `/data/setup/asket_setup.yaml` (`ASKET_SETUP_FILE`, set in the `Containerfile`) |

The GUI's own small preferences (which panels are folded, and the like) are
kept by each browser, not on the Jetson, and survive on their own.

Not kept: ROS logs (`/root/.ros/log`). Add `-v /srv/asket/ros-log:/root/.ros/log`
to the unit if you want them.

## asket.service replaces njord.service — never both

`scripts/init.sh` is the **Raspberry Pi + BlueOS** installer. It installs
`njord.service` (plus `njord-update.service` and `blueos.service`), which runs
the same `njord.launch.py` in a container named `njord`. On the Jetson,
`asket.service` **replaces** it. If both ran, there would be two stacks: two
`pico_bridge` fighting over `/dev/pico`, every topic published twice, two GUIs
on port 8090.

Three things stop that:

1. Do not run `scripts/init.sh` on the Jetson. If it was run, disable its units
   first:

   ```bash
   systemctl list-unit-files | grep -E 'njord|blueos'     # what is installed
   sudo systemctl disable --now njord.service njord-update.service
   ```

2. `asket.service` declares `Conflicts=njord.service`: starting either one
   stops the other, so they cannot both be up even if both are enabled.
3. `asket.service` refuses to start while a container named `njord` is
   running (a container started by hand, for instance), and says why in
   `journalctl -u asket.service`.

Check that only one stack is running:

```bash
systemctl is-active asket.service njord.service   # active / inactive
sudo podman ps --format '{{.Names}}'               # only "asket"
```

`blueos.service` is not touched, but since Phase B (d) MAVROS opens the
Pixhawk itself: `fcu_url` defaults to `/dev/pixhawk:115200` (the udev link),
and `autopilot:=ardupilot|px4` says which firmware it runs. If BlueOS is
running on the same machine it holds that serial port too — stop it
(`sudo systemctl disable --now blueos.service`), or go back through its
router with `use_blueos:=true`.

## Install

```bash
sudo mkdir -p /srv/asket/data
sudo cp deploy/jetson/asket.service /etc/systemd/system/asket.service
sudoedit /etc/systemd/system/asket.service     # check IMAGE and the launch line
sudo systemctl daemon-reload
sudo systemctl enable --now asket.service
journalctl -u asket.service -f                  # watch it start
```

Install the udev rules first (`deploy/udev/README.md`): the unit mounts the
host's `/dev`, so the stable names appear inside the container as they are on
the host.

## Check that persistence works

1. Open the GUI (`http://10.10.20.10:8090`), save something on the Setup
   page, and record a short mission (Start / Stop recording).
2. `ls /srv/asket/data/missions /srv/asket/data/setup` on the Jetson: the
   mission directory and `asket_setup.yaml` are there.
3. `sudo systemctl restart asket.service`, reload the page: the mission is
   still listed and the Setup values are still filled in.

## Things this unit does not decide

- **The image.** `ghcr.io/node-engineering-club/node-ros-2026:latest` is the
  generic `prod` image CI publishes from `main`. The Jetson-only
  `prod-jetson` image (SAM2/TensorRT) is built by hand and its default command
  runs only the SAM2 launch, not `njord.launch.py` — the explicit `ros2 launch`
  line in the unit overrides that.
- **The motor path.** The defaults apply: the Pico on `/dev/pico`
  (`use_pico_bridge:=true` since Phase B a), and MAVROS on `/dev/pixhawk`
  (Phase B d). Add `autopilot:=px4` to the launch line if the Pixhawk runs
  PX4; the pre-flight's `autopilot.match` check says which one it found.
- **`enable_bms:=true`** assumes the pack 1 BMS reader is fitted and named by
  udev (`/dev/bms_pack1`); the second one is off by default (`enable_bms2`,
  only one BMS is wired). If none is fitted, drop it — the battery then reads
  "not sent" and the pre-flight says it cannot confirm the battery (NO-GO),
  which is correct.
- **The geofence.** The Nav2 survey profile needs
  `/srv/asket/data/setup/geofence.yaml` (example:
  `src/bringup/config/geofence.example.yaml`); without it the pre-flight is
  NO-GO.
- **`scripts/init.sh`** is the older Raspberry Pi + BlueOS installer. It is
  left as it was; this unit is for the Jetson without BlueOS.
