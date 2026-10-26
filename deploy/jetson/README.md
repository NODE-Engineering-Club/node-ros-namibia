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
- **The motor path.** `use_pico_bridge` keeps its default (false), as do
  `fcu_url` and `pico_port`. Changing them is Phase B.
- **`enable_bms:=true`** assumes the two BMS readers are fitted and named by
  udev. If they are not, drop it — the battery then reads "not sent" and the
  pre-flight says it cannot confirm the battery (NO-GO), which is correct.
- **`scripts/init.sh`** is the older Raspberry Pi + BlueOS installer. It is
  left as it was; this unit is for the Jetson without BlueOS.
