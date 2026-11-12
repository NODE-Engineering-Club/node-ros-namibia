#!/bin/bash
# Bring the boat stack up for operation with or without internet.
#
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
pkill -9 -f "ros2 launch bringup njord.launch.py" 2>/dev/null
pkill -9 -f "/workspace/install" 2>/dev/null; pkill -9 -f "/workspace/install_local" 2>/dev/null
pkill -9 -f gui_backend_node 2>/dev/null; pkill -9 -f mavros_node 2>/dev/null
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

OAK=false; lsusb 2>/dev/null | grep -qi "03e7:" && OAK=true

nohup ros2 launch bringup njord.launch.py \
  fcu_url:="${FCU_URL:-/dev/ttyACM1:57600}" \
  enable_gui:=true gui_static_dir:=/workspace/NAMIBIA_REDESIGNED_GUI/dist \
  enable_gui_cloud_mirror:=true enable_sonar:=true enable_oak:=$OAK \
  enable_nav2:=false enable_perception:=false enable_vision:=false \
  enable_boat_bt:=false enable_mission:=false \
  ${EXTRA_ARGS:-} > /workspace/boat_data/boat_up.log 2>&1 &
echo "launched (oak=$OAK). Log: boat_data/boat_up.log  Cockpit: http://$(hostname).local:8090"
