# The OAK-D-LR in the GUI's Camera panel

Status: **prepared, not running.** The driver is not in the image and nothing
here has run against the real camera. With the defaults the Camera panel says
"Camera not connected", which is true.

## What is in place

| Piece | Where | State |
|---|---|---|
| Launch switch `enable_oak` (default `false`) | `bringup/launch/njord.launch.py` | Includes `depthai_ros_driver`'s `camera.launch.py` with `name:=oak` and `config/oak.yaml`. With `false` the package does not need to be installed. |
| Driver parameters | `bringup/config/oak.yaml` | `camera.i_ip: 10.10.20.21`, RGB pipeline only, no neural network, 10 fps. |
| GUI source | `gui_backend/config/topics.yaml` → `sources.camera_image` | `sensor_msgs/msg/CompressedImage` on `/oak/rgb/image_raw/compressed`. |
| Panel shown as fitted | `camera_fitted` = `enable_oak` or `enable_usb_camera` | Otherwise "Camera not connected". |
| Link adaptation | `gui_backend/core/ros_camera.py`, `core/streams.py` | Full link: the driver's JPEG untouched, 5 fps. Reduced link: 160×120 re-encoded (OpenCV), one frame every 5 s. Beacon: no picture, and the panel says so. The panel shows the rate actually **received**. |

## To install and verify on the Jetson

1. Install the driver and the compressed transport in the image:
   `ros-jazzy-depthai-ros-driver` and `ros-jazzy-compressed-image-transport`
   (`apt`, Luxonis packages on the ROS repository). Not added to the
   `Containerfile` in this change, on purpose: it is a large dependency and
   its version (DepthAI v2 driver, or the newer v3 package
   `depthai_ros_driver_v3`, whose parameter names differ) has to be chosen on
   the boat.
2. Check the camera answers on the network: `ping 10.10.20.21`.
3. `ros2 launch bringup njord.launch.py enable_oak:=true`, then
   `ros2 topic list | grep oak` and `ros2 topic hz /oak/rgb/image_raw/compressed`.
   If the names differ, change `sources.camera_image.topic` in `topics.yaml`.
4. The OAK-D-LR has three colour sensors and no "RGB" camera in the OAK-D
   sense: check which socket the driver's `RGB` pipeline uses and that the
   picture is the forward view.
5. If the PoE link or the Jetson's CPU struggles, the driver's
   `rgb.i_low_bandwidth: true` encodes MJPEG on the camera itself.

Luxonis documentation: <https://docs.luxonis.com/software/ros/depthai-ros/driver/>
(`camera.i_ip`, `camera.i_pipeline_type`, `rgb.i_low_bandwidth`).

## Overlaying recognised objects (planned, not built)

The camera is the boat's only object recognition. The plan for drawing what it
recognises on the picture:

1. The detector (on the OAK's own processor through the driver's NN pipeline,
   or on the Jetson) publishes `vision_msgs/msg/Detection2DArray`, with the
   **same header stamp** as the image it was computed on.
2. `gui_backend` subscribes to it, keeps the latest, and adds the boxes that
   match the frame being sent (by stamp, within one frame interval) to the
   camera frame header: `detections: [{label, score, x, y, w, h}]`, in
   fractions of the image so they survive the reduced-link re-scaling.
   A few hundred bytes; the frame format (`core/video_frame.py`) carries a JSON
   header already.
3. The Camera panel draws them on a `<canvas>` laid over the `<img>`, with the
   label and score. Boxes are drawn only on a live picture: when the picture
   goes stale or blank, its boxes go with it.
4. Details shows the detector's name and rate, so an empty picture without
   boxes can be told from a detector that has stopped.

Until then the panel shows the picture alone, and the operator is the
classifier.

## OAK-D-LR on USB with this repo's own node (current bench/boat setup)

`njord.launch.py enable_oak_rgbd:=true` starts `sensors/oak_rgbd` (DepthAI v2,
`pip install "depthai>=2.28,<3"`, now in the Containerfile) in the `/oak`
namespace. It publishes the JPEG the Camera panel shows. `enable_segmentation:=true`
adds `sam2_vision/sam2_depth_segmenter`, which fills the panel's
"Segmented + distance" view (weights in `models/sam2.1_hiera_tiny`, not in git).
`scripts/boat_up.sh` turns both on by itself when an OAK is on USB.

`sam2_vision` sits inside the `perception` package folder, so colcon does not
find it on its own: build it with `--base-paths src/perception/sam2_vision`.

A USB OAK needs the Luxonis udev rule on the **host** (once), or DepthAI
reports `X_LINK_INSUFFICIENT_PERMISSIONS`:

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/80-movidius.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then unplug and replug the camera.

### Over PoE (current setup, 2026-11-12)

The OAK is on a PoE injector wired straight to the Jetson's `eth0`. With no DHCP
server on that cable the camera falls back to **169.254.1.222**. The Jetson's
wired connection carries `169.254.1.10/16` (and `10.10.20.10/24`, the boat plan)
next to `192.168.88.10/24`:

```bash
nmcli con mod "Wired connection 1" +ipv4.addresses 10.10.20.10/24 +ipv4.addresses 169.254.1.10/16
nmcli device reapply eth0
```

`scripts/boat_up.sh` finds the camera with DepthAI's network discovery and passes
`oak_device_ip` to `oak_rgbd`. Measured: 10 fps colour + depth.

### GPU for SAM 2 / YOLO

The host is JetPack 5.1.2; the ROS container (Jazzy, Python 3.12) has no GPU
ONNX Runtime. `scripts/boat.sh` (run on the host) starts a second, rootful
container from `dustynv/onnxruntime:r35.4.1` that serves the models on
`127.0.0.1:7090` (`sam2_vision/remote_ort.py`). The segmenter uses it when it
answers and its own CPU sessions when it does not (`gpu_server` parameter).

### On-camera detection (default since 2026-11-12)

`oak_rgbd` runs `models/yolov8n_coco_640x352.blob` (Luxonis' YOLOv8n, 80 COCO
classes, compiled for the RVC2 chip) **on the camera**, with each object's 3D
position from the camera's stereo depth. Measured: ~10 fps, about 0% Jetson
CPU for the detector, load average ~2.8 for the whole stack. The Jetson only
draws: the mask is the depth region at the centre of the box near the object's
distance. Where depth is too sparse (dark, glossy, textureless surfaces) the
box is drawn instead, never a doubtful outline.

* `/oak/detections/image/compressed`: annotated frame, the GUI's
  "Segmented + distance" view (topics.yaml `camera_segmented`).
* `/oak/detections`: JSON list (label, confidence, distance, x/y/z, bearing, bbox).
* Raw `/oak/rgb/image_raw` and `/oak/stereo/depth` are published only while
  something subscribes (SAM 2). Unread, ~28 MB/s of best-effort UDP crowded out
  the JPEGs.
* SAM 2 on the Jetson is opt-in: `SAM2=1 scripts/boat_up.sh`.
* gui_backend now has one newest-wins video slot per stream. A single shared
  slot starved one of camera / camera_seg completely.

## Water / obstacle segmentation (layer 1 of collision avoidance, 2026-11-13)

For collision avoidance the boat does not need to know *what* floats ahead
(kelp, branches, debris), only that it is not water and how far away it is.
`oak_rgbd` runs **eWaSR** (ResNet-18, trained on MaSTr1325, Apache-2.0,
<https://github.com/tersekmatija/eWaSR>) on the Jetson CPU, in a worker thread
on the newest frame: about 350 ms per frame on 4 threads, so ~2.4 Hz while the
video stays at 10 fps. Weights: `scripts/fetch_models.sh` → `models/ewasr/`
(not in git). Parameter `water_seg_model` (empty = off), `water_seg_threads` (4
was fastest; 2 and 8 were slower).

- The "Segmented + distance" view tints water blue and outlines every region on
  the water as `obstacle` (or `shore` for a band across the frame), with its
  stereo distance (median over the region's lowest third, where it meets the
  water). `-- m` means stereo had no measurement there; nothing is guessed.
- `/oak/water_obstacles` (JSON): water fraction, per obstacle label, distance,
  bearing, bbox. `/oak/detections` stays the on-camera detector's only.
- Rules in `sensors/water_seg.py` (tested in `src/sensors/test/test_water_seg.py`):
  water below the region (or around it, for a region cut by the bottom of the
  frame); nothing reported when under 10 % of the frame is water (indoors).

Known limits, to fix with the mounting calibration: no self-mask for the bow yet
(if the bow is in view it shows as an obstacle); flat kelp gives stereo little
to measure, so its distance will need camera height + horizon geometry;
MaSTr1325 is Slovenian coastal water, so a fine-tune on frames from the kelp
farm will likely be needed.
