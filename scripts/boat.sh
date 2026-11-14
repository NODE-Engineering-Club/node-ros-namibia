#!/bin/bash
# Start / stop everything on the Jetson, from the HOST (not inside a container).
#
#   scripts/boat.sh          # up (or restart)
#   scripts/boat.sh stop
#   scripts/boat.sh status
#
# Containers:
#   * sonarview         Cerulean SonarView (docker, host network) on :7077: THE
#                       sonar 3D window. It holds the sonar's only TCP connection,
#                       so omniscan_bridge is not started (SONAR=bridge swaps them).
#                       Its recordings land in boat_data/sonarview/ and the black
#                       box uploads them to Azure.
#   * asket-gpu         rootful, --runtime nvidia, NVIDIA's JetPack 5 image with
#                       CUDA/TensorRT ONNX Runtime. Serves SAM 2 + YOLO on
#                       127.0.0.1:7090 (src/perception/sam2_vision/sam2_vision/remote_ort.py).
#   * namibia-gps-check rootless, the ROS 2 Jazzy stack (scripts/boat_up.sh): GUI on
#                       :8090, OAK, segmenter, MAVROS, cloud mirror, recording.
# The segmenter uses the GPU server when it answers and the CPU when it does not,
# so either container can restart without the other noticing more than a slowdown.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
GPU_IMAGE="docker.io/dustynv/onnxruntime:r35.4.1"   # JetPack 5.1.2 / L4T R35.4.1, matches the host
ROS_CTR="namibia-gps-check"
SONAR="${SONAR:-sonarview}"   # sonarview (default) | bridge
SV_IMAGE="docker.io/nicknothom/sonarview:latest"

# The two windows: our cockpit and SonarView, in Firefox on the Jetson's screen
# (Firefox, not Chromium: only Firefox has WebGL here, with webgl.force-enabled
# in the profile's user.js). From a laptop: http://<jetson>:8090 and :7077.
open_windows() {
  [ -S /tmp/.X11-unix/X0 ] || return 0
  DISPLAY=:0 nohup firefox --new-window "http://localhost:8090" "http://localhost:7077" >/dev/null 2>&1 &
}

sonarview_up() {
  docker rm -f sonarview >/dev/null 2>&1 || true
  mkdir -p "$REPO/boat_data/sonarview"
  # No restart policy: it would grab the sonar back from omniscan_bridge.
  docker run -d --name sonarview --network host \
    -v "$REPO/boat_data/sonarview:/userdata" -e ENABLE_OS3D=true "$SV_IMAGE" >/dev/null
  echo "SonarView: http://$(hostname).local:7077 (press Start in it to ping)"
}

gpu_up() {
  sudo -n podman rm -f asket-gpu >/dev/null 2>&1 || true
  # Full path: podman has no "nvidia" runtime registered on this host.
  sudo -n podman run -d --name asket-gpu --runtime /usr/bin/nvidia-container-runtime --network host \
    --restart unless-stopped \
    -v "$REPO:/ws" -e REMOTE_ORT_TRT="${REMOTE_ORT_TRT:-0}" \
    "$GPU_IMAGE" python3 /ws/src/perception/sam2_vision/sam2_vision/remote_ort.py serve --port 7090 >/dev/null \
    || { echo "GPU server: not started (needs sudo and the image); CPU fallback"; return 0; }
  echo "GPU server: starting (logs: sudo podman logs -f asket-gpu)"
}

case "${1:-up}" in
  stop)
    podman exec "$ROS_CTR" bash /workspace/scripts/boat_up.sh stop 2>/dev/null || true
    sudo -n podman rm -f asket-gpu >/dev/null 2>&1 || true
    docker rm -f sonarview >/dev/null 2>&1 || true
    echo "stopped" ;;
  status)
    sudo -n podman ps --filter name=asket-gpu --format "{{.Names}} {{.Status}}"
    podman ps --filter name="$ROS_CTR" --format "{{.Names}} {{.Status}}"
    docker ps --filter name=sonarview --format "{{.Names}} {{.Status}}"
    sudo -n podman logs --tail 3 asket-gpu 2>/dev/null || true ;;
  up|*)
    gpu_up
    podman start "$ROS_CTR" >/dev/null
    podman exec -e SONAR="$SONAR" "$ROS_CTR" bash /workspace/scripts/boat_up.sh
    if [ "$SONAR" = sonarview ]; then sonarview_up; else docker rm -f sonarview >/dev/null 2>&1 || true; fi
    open_windows ;;
esac
