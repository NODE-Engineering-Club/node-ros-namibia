"""Turn what the real vessel publishes into the pre-flight's state keys.

ROS-free, so it is tested without a ROS install, like ``checks``.

These exist because several critical checks could never pass on the real
boat, whatever its condition (docs/HARDWARE_DECISIONS.md):

* ``/pico/status`` is a ``std_msgs/String`` of raw firmware ``STATE`` lines
  (pico_bridge republishes them verbatim). The node subscribed to it as
  ``asket_interfaces/PicoStatus``, a type only the simulator publishes, so on
  the boat ``pico.link`` and ``pico.firmware_version`` never had data.
* ``/asket/heading`` is published only by the simulator. On the boat the
  heading is the EKF's, on ``/odometry/filtered`` — the same source the GUI
  draws.
* ``gnss.fix`` needs a satellite count and nothing supplied one.
* ``clock.gps_offset`` needs a GPS time reference and nothing supplied one.

The line parser is ``gui_backend.core.pico_state``: one parser for the wire
format, shared with the GUI, so the pre-flight and the vessel panel cannot read
the same line two ways.
"""

from __future__ import annotations

from gui_backend.core.pico_state import parse_state_line


def pico_facts(line: str | None, age_s: float | None) -> dict:
    """The ``pico_*`` / ``rc_*`` keys from one raw STATE line.

    Empty when nothing has arrived, so every Pico check reports SKIPPED
    rather than passing a vessel nobody has heard from.
    """
    if line is None:
        return {}
    state = parse_state_line(line)
    return {
        "pico_age_s": age_s,
        "rc_link_ok": state.rc_link_ok,
        "rc_channel8_raw": state.rc_channel8_raw,
        "rc_channel7_raw": state.rc_channel7_raw,
        "pico_firmware_version": state.firmware_version,
        "pico_state_format_verified": state.format_verified,
        "pico_state_parsed": state.parsed,
        "pico_state_line": state.raw,
        "pico_state_unknown_keys": list(state.unknown_keys),
    }


def heading_facts(estimate) -> dict:
    """The ``heading_*`` keys from an :class:`asket_common.heading.HeadingEstimate`.

    The estimate comes from ``heading_from_odometry`` on the boat, so the
    pre-flight, the recorder and the GUI judge the same EKF heading the same
    way.
    """
    return {
        "heading_valid": estimate.valid,
        "heading_source": estimate.source,
        "heading_accuracy_deg": estimate.accuracy_deg,
        "heading_divergence_deg": estimate.divergence_deg if estimate.valid else None,
        "heading_divergence_meaningful": estimate.divergence_meaningful,
    }


def gps_time_facts(
    measured_at_s: float | None, gps_time_s: float | None, age_s: float | None,
) -> dict:
    """``gps_time_offset_ms`` / ``gps_time_age_s`` from a TimeReference.

    ``measured_at_s`` is the message's ``header.stamp`` (the Jetson's clock
    when the reference was taken), ``gps_time_s`` its ``time_ref``. Positive
    means the Jetson is ahead of GPS.
    """
    if measured_at_s is None or gps_time_s is None or gps_time_s <= 0:
        return {}
    return {
        "gps_time_offset_ms": int(round((measured_at_s - gps_time_s) * 1000.0)),
        "gps_time_age_s": age_s,
    }
