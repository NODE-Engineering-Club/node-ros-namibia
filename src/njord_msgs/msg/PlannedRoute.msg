# A human-planned route loaded from a YAML/JSON waypoint file (see
# mission/mission/waypoint_route_loader.py), for display and manual
# piloting reference — NOT an autonomous mission. The boat is driven by a
# human watching this rendered as a line on the map, same way a nautical
# chart's planned track works; nothing in this message causes any motion
# by itself. (Nothing stops a future node from also feeding `waypoints`
# into njord_msgs/srv/StartMission for autonomous execution of the same
# route — that's a one-line addition elsewhere, deliberately not wired
# here so this stays a pure visualization/reference message.)

std_msgs/Header header        # frame_id is unused (these are absolute WGS84 points,
                               # not frame-relative); stamp = load/reload time

string name                   # from the route file's own "name" field, or the filename
string source_path            # absolute path of the file this was loaded from

geographic_msgs/GeoPoint[] waypoints   # in file order; altitude is unused (0.0)
