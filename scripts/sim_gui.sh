#!/bin/bash
# Gazebo 3D window on the Jetson's screen for a running sim (scripts/sim_sea.sh).
# Docker image node-ros-2026:local (Gazebo Harmonic GUI), the X11 socket and
# GDM's Xauthority, host network. Only the GUI runs here; the Gazebo server
# and the ROS stack stay in namibia-gps-check.
#
# SOFTWARE RENDERING BY DEFAULT (Mesa llvmpipe, CPU only). On the GPU
# (--runtime nvidia: the Ubuntu 24.04 container's Ogre2 on the JetPack 5 /
# R35 driver) the window triggers a GPU shader fault ("sm machine check err",
# MMU FAULT on the gz sim -g channel) that takes Xorg down and has rebooted
# the Jetson (2026-11-20, three times). GPU=1 brings the GPU path back:
# only for testing, never during a trial or with the boat stack up.
#
#   scripts/sim_gui.sh          # open (or reopen) the window, camera follows the boat
#   scripts/sim_gui.sh stop
set -e
NAME=sea-gzgui
docker rm -f "$NAME" >/dev/null 2>&1 || true
[ "${1:-}" = stop ] && { echo "Gazebo window closed"; exit 0; }
XAUTH=/run/user/1000/gdm/Xauthority
# The logged-in desktop's display: usually :0, but :1 after a GNOME session
# restart (e.g. a GPU fault took the old one down). Override with DISP=:N.
DISP="${DISP:-$(who | awk '$2 ~ /^:[0-9]+$/ {print $2; exit}')}"
DISP="${DISP:-:0}"
[ -S /tmp/.X11-unix/X${DISP#:} ] || { echo "no X display $DISP on this Jetson (log in on its screen, or use NoMachine)"; exit 1; }
if [ "${GPU:-0}" = 1 ]; then
  echo "WARNING: GPU=1 - this has crashed the Jetson's GPU/desktop before (see the top of this script)"
  RENDER=(--runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all)
else
  # CPU only: no NVIDIA runtime, no GPU device nodes, Mesa's llvmpipe.
  # Capped at 4 cores so the window can't starve the nav stack.
  RENDER=(--cpus 4 -e LIBGL_ALWAYS_SOFTWARE=1 -e __GLX_VENDOR_LIBRARY_NAME=mesa
          -e __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json -e GALLIUM_DRIVER=llvmpipe)
fi
docker run -d --name "$NAME" "${RENDER[@]}" --network host \
  -e DISPLAY=$DISP -e XAUTHORITY=$XAUTH \
  -v /tmp/.X11-unix:/tmp/.X11-unix -v $XAUTH:$XAUTH \
  -v "$(cd "$(dirname "$0")/.." && pwd)":/workspace \
  node-ros-2026:local bash -c 'source /opt/ros/jazzy/setup.bash; gz sim -g -v 2' >/dev/null
# Follow the boat once the scene is up (CameraTracking's /gui/follow service).
docker exec "$NAME" bash -c 'source /opt/ros/jazzy/setup.bash
  for _ in $(seq 60); do gz service -l 2>/dev/null | grep -q "^/gui/follow$" && break; sleep 1; done
  sleep 3
  gz service -s /gui/follow/offset --reqtype gz.msgs.Vector3d --reptype gz.msgs.Boolean --timeout 3000 --req "x: -9, y: -7, z: 4" >/dev/null
  gz service -s /gui/follow --reqtype gz.msgs.StringMsg --reptype gz.msgs.Boolean --timeout 3000 --req "data: \"asket\"" >/dev/null' \
  && echo "Gazebo window open, following the boat (right-click > Move to / Follow to change)" \
  || echo "Gazebo window open (could not set camera follow)"
