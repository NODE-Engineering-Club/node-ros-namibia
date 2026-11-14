#!/bin/bash
# Bring the boat stack up for operation with or without internet.
#
#   (normally via scripts/boat.sh on the host, which also starts the GPU server)
#   podman start namibia-gps-check
#   podman exec namibia-gps-check bash /workspace/scripts/boat_up.sh      # (re)start
#   podman exec namibia-gps-check bash /workspace/scripts/boat_up.sh stop
#
# Two views of the SAME cockpit (NAMIBIA_REDESIGNED_GUI):
#   * boat WiFi/LAN, no internet needed:  http://node2026.local:8090
#   * cloud (when the Jetson has internet): the Azure storage website, fed by gui_cloud_mirror.
# Everything is recorded on the Jetson (mission_recorder + cloud spool) under
# /workspace/boat_data, and the spool uploads to Azure whenever internet returns.
#
# Env overrides: FCU_URL (default /dev/ttyACM1:57600), EXTRA_ARGS (more launch args).
# (no set -u: ROS setup scripts read unset variables)
# Stop EVERYTHING ROS in this container, not just the launch process: killing
# `ros2 launch` alone leaves its nodes running (seen 2026-11-12: seven copies of
# every node after a few restarts, load average 265). Nodes run from
# /ws/install*, /workspace/install* or /opt/ros. This script itself does not match.
stop_all() {
  # The black-box recorder first, with SIGINT so it closes its .mcap and
  # writes metadata.yaml (it runs in its own process group).
  pkill -INT -f "ros2 bag record" 2>/dev/null
  for _ in $(seq 15); do pgrep -f "ros2 bag record" >/dev/null || break; sleep 1; done
  for pat in "ros2 bag record" in "ros2 launch" "/ws/install" "/workspace/install" "/opt/ros/jazzy/lib" "gui_backend_node" "mavros_node"; do
    pkill -9 -f "$pat" 2>/dev/null
  done
  for _ in $(seq 10); do
    pgrep -f "/ws/install|/opt/ros/jazzy/lib|ros2 launch" >/dev/null || break
    sleep 1
  done
}
stop_all
[ "${1:-}" = stop ] && { echo stopped; exit 0; }
sleep 1

# install/ was colcon symlink-built under /ws in another container.
[ -e /ws ] || ln -s /workspace /ws
# Persistent data on the host (the container's own /data is thrown away with it).
mkdir -p /workspace/boat_data/{secrets,missions,maps,cloud_spool,cloud_spool_gui}
if [ ! -L /data ]; then rm -rf /data 2>/dev/null; ln -s /workspace/boat_data /data; fi
[ -f /data/secrets/azure_storage_connection_string ] || \
  cp /root/.azure_storage_connection_string /data/secrets/azure_storage_connection_string 2>/dev/null
chmod 600 /data/secrets/* 2>/dev/null

source /opt/ros/jazzy/setup.bash
source /ws/install/setup.bash
source /ws/install_local/setup.bash   # sensors, gui_backend, cloud_bridge, ... rebuilt here

# OAK-D-LR on USB -> camera (sensors/oak_rgbd) + SAM 2 segmented view.
# Up to 15 s: right after a stop the camera resets and is briefly off the bus.
OAK=false
for _ in $(seq 15); do
  grep -qx 03e7 /sys/bus/usb/devices/*/idVendor 2>/dev/null && { OAK=true; break; }
  sleep 1
done
# Or over PoE: OAK_IP if given, else ask DepthAI to find one on the network
# (with no DHCP on the camera's cable it sits at its fallback 169.254.1.222;
# the Jetson's eth0 has 169.254.1.10/16 and 10.10.20.10/24 for that).
OAK_IP="${OAK_IP:-}"
if [ "$OAK" = false ]; then
  [ -n "$OAK_IP" ] || OAK_IP=$(timeout 20 python3 -c "
import depthai as dai
for d in dai.Device.getAllAvailableDevices():
    if d.protocol == dai.XLinkProtocol.X_LINK_TCP_IP: print(d.name); break" 2>/dev/null | tail -1)
  [ -n "$OAK_IP" ] && OAK=true
fi
SEG=false
if [ "$OAK" = true ]; then
  python3 -c "import depthai" 2>/dev/null || pip install --break-system-packages -q "depthai>=2.28,<3"
  # Objects, masks and distances come from the OAK's own chip (oak_rgbd's
  # detection_blob). SAM 2 on the Jetson is opt-in: SAM2=1 (worth it with the
  # GPU worker; seconds per frame on the CPU).
  [ "${SAM2:-0}" = 1 ] && [ -f /ws/models/sam2.1_hiera_tiny/vision_encoder_int8.onnx ] && SEG=true
fi

# Sonar: SonarView (scripts/boat.sh, docker) is the 3D window and holds the
# sonar's single TCP connection, so omniscan_bridge stays off unless SONAR=bridge.
SONAR_BRIDGE=false
[ "${SONAR:-sonarview}" = bridge ] && SONAR_BRIDGE=true

nohup ros2 launch bringup njord.launch.py \
  fcu_url:="${FCU_URL:-/dev/ttyACM1:57600}" \
  enable_gui:=true gui_static_dir:=/workspace/NAMIBIA_REDESIGNED_GUI/dist \
  enable_gui_cloud_mirror:=true enable_blackbox:=true enable_sonar:=$SONAR_BRIDGE enable_oak_rgbd:=$OAK ${OAK_IP:+oak_device_ip:=$OAK_IP} enable_segmentation:=$SEG \
  enable_nav2:=false enable_perception:=false enable_vision:=false \
  enable_boat_bt:=false enable_mission:=false \
  ${EXTRA_ARGS:-} > /workspace/boat_data/boat_up.log 2>&1 &
echo "launched (sonar=${SONAR:-sonarview}, oak=$OAK${OAK_IP:+ at $OAK_IP}, on-camera detection, sam2=$SEG). Log: boat_data/boat_up.log  Cockpit: http://$(hostname).local:8090"
