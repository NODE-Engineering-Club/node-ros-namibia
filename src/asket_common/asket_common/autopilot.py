"""Which autopilot the Pixhawk runs, from its MAVLink HEARTBEAT. ROS-free.

MAVROS republishes every message from the flight controller, raw, on
``/uas1/mavlink_source`` (``mavros_msgs/Mavlink``). A HEARTBEAT (msgid 0) has
the autopilot type in its sixth payload byte; MAVROS packs the payload into
little-endian uint64 words (``payload64``). Payload layout:
``custom_mode`` uint32, ``type`` uint8, ``autopilot`` uint8, ``base_mode``,
``system_status``, ``mavlink_version``.

The only two this boat can run (MAV_AUTOPILOT): 3 ArduPilot, 12 PX4.
"""

from __future__ import annotations

import struct

HEARTBEAT_MSGID = 0
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_AUTOPILOT_PX4 = 12

#: The words njord.launch.py's `autopilot` argument takes.
NAMES = {MAV_AUTOPILOT_ARDUPILOTMEGA: "ardupilot", MAV_AUTOPILOT_PX4: "px4"}
LABELS = {"ardupilot": "ArduPilot", "px4": "PX4"}

#: The flight controller's own component (MAV_COMP_ID_AUTOPILOT1). A
#: companion, a camera or a GCS also send heartbeats, with autopilot 8
#: (INVALID): only the autopilot component counts.
AUTOPILOT_COMPID = 1


def heartbeat_fields(payload64) -> dict | None:
    """The HEARTBEAT fields from a Mavlink message's payload64, or None."""
    raw = b"".join(struct.pack("<Q", int(word)) for word in payload64)
    if len(raw) < 9:
        return None
    custom_mode, mav_type, autopilot, base_mode, system_status, version = struct.unpack_from(
        "<IBBBBB", raw
    )
    return {
        "custom_mode": custom_mode,
        "type": mav_type,
        "autopilot": autopilot,
        "base_mode": base_mode,
        "system_status": system_status,
        "mavlink_version": version,
    }


def autopilot_name(code: int) -> str:
    """'ardupilot', 'px4', or 'other:<code>'."""
    return NAMES.get(int(code), f"other:{int(code)}")


def from_mavlink(msgid: int, compid: int, payload64) -> str | None:
    """The autopilot name if this is the flight controller's HEARTBEAT."""
    if int(msgid) != HEARTBEAT_MSGID or int(compid) != AUTOPILOT_COMPID:
        return None
    fields = heartbeat_fields(payload64)
    return autopilot_name(fields["autopilot"]) if fields else None


def label(name: str | None) -> str:
    if not name:
        return "not detected"
    return LABELS.get(name, name)
