#!/bin/bash
# Lock the autopilot's home at the boat's current GPS position: the ground
# station point the cockpit's "From home" distance/bearing is measured from.
# Run it from the HOST, with the stack up (scripts/boat.sh), the boat next to
# the ground station and the GPS outdoors:
#
#   scripts/set_home.sh
#
# Why: ArduPilot Rover keeps moving home to its own position while disarmed,
# so without this "From home" can sit near 0 m. A home set by command is
# locked until the Pixhawk reboots. Refuses without a 3D fix (no home from a
# bad indoor position). After a Pixhawk reboot, run it again.
set -e
CTR="namibia-gps-check"
MIN_SATS="${MIN_SATS:-8}"
podman exec -e MIN_SATS="$MIN_SATS" "$CTR" bash -c '
source /opt/ros/jazzy/setup.bash
source /workspace/install_local/setup.bash 2>/dev/null || true
raw=$(timeout 10 ros2 topic echo --once /mavros/gpsstatus/gps1/raw 2>/dev/null)
fix=$(echo "$raw" | awk "/^fix_type:/{print \$2}")
sats=$(echo "$raw" | awk "/^satellites_visible:/{print \$2}")
echo "GPS: fix_type=${fix:-?} satellites=${sats:-?}"
if [ "${fix:-0}" -lt 3 ] || [ "${sats:-0}" -lt "$MIN_SATS" ]; then
  echo "NOT set: need fix_type>=3 and >=$MIN_SATS satellites. Wait outdoors and retry."; exit 1
fi
ros2 service call /mavros/cmd/set_home mavros_msgs/srv/CommandHome "{current_gps: true}" | tail -1
sleep 3
timeout 10 ros2 topic echo --once /mavros/home_position/home 2>/dev/null | grep -A3 "^geo:"
echo "Home locked. The cockpit Navigate screen shows it under \"From home\"."
'
