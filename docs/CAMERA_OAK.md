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
