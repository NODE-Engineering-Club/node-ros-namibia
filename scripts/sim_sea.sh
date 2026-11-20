#!/bin/bash
# Sea-state trial in Gazebo, on the Jetson alone (no Pixhawk, Pico or sensors).
# Run from the HOST, like boat.sh:
#
#   scripts/sim_sea.sh                     # rough sea, both Navigation mission routes
#   scripts/sim_sea.sh extreme towards     # calm | moderate | rough | extreme ; towards | away | both
#   scripts/sim_sea.sh stop                # kill a sim left running (and close the window)
#   NOGUI=1 scripts/sim_sea.sh rough       # no Gazebo window (faster, or over SSH)
#
# What runs (inside the namibia-gps-check container, ROS_DOMAIN_ID=77 so it
# never talks to a live boat stack on the default domain):
#   * Gazebo server, a world generated for the sea state (scripts/gen_sea_world.py):
#     datum = start of the routes, animated waves = the physics' waves, a buoy per waypoint
#   * the Gazebo 3D window on the Jetson's screen (scripts/sim_gui.sh, Docker + X11, software rendering: the GPU path crashed the Jetson)
#   * sim/sea_state_node: waves, wind, current, 2x T200 thrust limits
#   * the Jetson's own nav stack: EKF, navsat, Nav2 (survey profile), mission_manager,
#     nav_to_pid, pid_controller -> /control/effort -> simulated thrusters
#   * the cockpit on :8091 (same cockpit, sim data): http://<jetson>.local:8091
# then scripts/sim_sea_trial.py flies the GeoJSON routes and writes
# boat_data/sim/<route>_<sea>_<time>/{summary.txt,track.csv}.
#
# Don't run scripts/boat.sh while this runs: boat_up.sh's stop kills every
# ROS process in the container, the sim's included.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CTR="namibia-gps-check"
SEA="${1:-rough}"
WHICH="${2:-both}"
LOG=/workspace/boat_data/sim/launch.log

in_ctr() { podman exec -e ROS_DOMAIN_ID=77 "$CTR" bash -c "$1"; }

# Only the sim's own process group (setsid below), never the boat stack.
stop_sim() {
  in_ctr 'G=$(cat /tmp/sim_sea.pgid 2>/dev/null); [ -n "$G" ] || exit 0
          kill -INT -- -$G 2>/dev/null; for _ in $(seq 10); do kill -0 -- -$G 2>/dev/null || break; sleep 1; done
          kill -9 -- -$G 2>/dev/null; pkill -9 -f "gz sim -s -r .*openSeaWorld" 2>/dev/null; rm -f /tmp/sim_sea.pgid; true' || true
}

if [ "$SEA" = stop ]; then stop_sim; "$REPO/scripts/sim_gui.sh" stop >/dev/null; echo "sim stopped"; exit 0; fi
case "$SEA" in calm|moderate|rough|extreme) ;; *) echo "sea state: calm|moderate|rough|extreme"; exit 1;; esac
ROUTES=()
TW="/workspace/Navigation mission/Navigation_towards_shore_(0 to 30).geojson"
AW="/workspace/Navigation mission/Navigation_away_shore_(-20 to 0).geojson"
case "$WHICH" in
  towards) ROUTES=("$TW") ;;
  away)    ROUTES=("$AW") ;;
  both)    ROUTES=("$TW" "$AW") ;;
  *) echo "route: towards|away|both"; exit 1 ;;
esac

podman start "$CTR" >/dev/null
if in_ctr 'pgrep -f "bringup njord.launch.py" | xargs -r ps -o args= -p | grep -qv sea_state'; then
  echo "NOTE: the boat stack is running in the container too (domain 0). The sim uses domain 77,"
  echo "      so they don't mix, but both share the CPU."
fi
stop_sim

# Sim-only geofence round the routes (never /data/setup/geofence.yaml), and the world.
in_ctr "mkdir -p /workspace/boat_data/sim && python3 /workspace/scripts/sim_sea_trial.py --write-geofence /workspace/boat_data/sim/geofence_sea.yaml \"$TW\" \"$AW\" >/dev/null"
WORLD=/workspace/boat_data/sim/world_$SEA
in_ctr "source /opt/ros/jazzy/setup.bash; source /workspace/install/setup.bash; source /workspace/install_local/setup.bash
  python3 /workspace/scripts/gen_sea_world.py --sea $SEA --out $WORLD \"$TW\" \"$AW\" 2>&1 | grep -v INFO"

echo "starting Gazebo + stack, sea state '$SEA' (log: boat_data/sim/launch.log) ..."
in_ctr "source /opt/ros/jazzy/setup.bash; source /workspace/install/setup.bash; source /workspace/install_local/setup.bash
  [ -e /ws ] || ln -s /workspace /ws
  rm -f ~/.njord_mission_checkpoint.json
  setsid nohup ros2 launch bringup njord.launch.py use_sim:=true world:=$WORLD/sea.sdf sea_state:=$SEA \
    headless:=true enable_mavros:=false enable_sensors:=false enable_perception:=false enable_vision:=false \
    enable_geo_fusion:=false use_pico_bridge:=false enable_boat_bt:=false \
    geofence_file:=/workspace/boat_data/sim/geofence_sea.yaml \
    enable_gui:=true gui_port:=8091 gui_static_dir:=/workspace/NAMIBIA_REDESIGNED_GUI/dist \
    > $LOG 2>&1 < /dev/null &
  echo \$! > /tmp/sim_sea.pgid"
echo "cockpit (sim): http://$(hostname).local:8091"
if [ -z "${NOGUI:-}" ]; then
  for _ in $(seq 60); do grep -q "Entity creation successful" "$REPO/boat_data/sim/launch.log" 2>/dev/null && break; sleep 2; done
  "$REPO/scripts/sim_gui.sh" || echo "(no Gazebo window; the trial runs anyway)"
fi

set +e
ARGS=""; for r in "${ROUTES[@]}"; do ARGS="$ARGS \"$r\""; done
in_ctr "source /opt/ros/jazzy/setup.bash; source /workspace/install/setup.bash; source /workspace/install_local/setup.bash
  python3 /workspace/scripts/sim_sea_trial.py --sea $SEA $ARGS"
RC=$?
echo "stopping the sim ... (the Gazebo window closes too)"
[ -n "${KEEP:-}" ] && { echo "KEEP=1: sim left running; scripts/sim_sea.sh stop when done"; exit $RC; }
stop_sim
"$REPO/scripts/sim_gui.sh" stop >/dev/null
exit $RC
