"""Heading quality assessment.

Heading is the dominant error source in the whole survey: **1 degree is about
90 cm at 50 m range**. This module is small, and it is shared by
``gui_backend``, ``mission_recorder`` and ``system_test`` deliberately — all
three must agree on whether the heading was trustworthy at a given instant, or
the recorded validity flag will not mean what the GUI showed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geo import angular_difference

SOURCE_GNSS_COMPASS = "gnss_compass"
#: The Njord stack's EKF yaw, from /odometry/filtered. A fusion of IMU, GNSS and
#: whatever else robot_localization is configured with — better than a bare
#: magnetometer, and not as good as a dual-antenna compass.
SOURCE_EKF = "ekf"
SOURCE_MAGNETOMETER = "magnetometer"
SOURCE_COG = "cog"
SOURCE_NONE = "none"

#: Typical accuracy by source, degrees, used **only** when the source reports
#: none of its own. The device's figure always wins: a UM982 that has lost one
#: antenna reports a degraded accuracy, and substituting the nominal 0.2 would
#: hide exactly the failure worth seeing (docs/open_questions.md Q4).
#:
#: The magnetometer figure is the upper end of the 2-10 degree range the hull
#: actually shows; the GNSS compass figure assumes the 1 m baseline
#: (PROVISIONAL, docs/open_questions.md Q4).
NOMINAL_ACCURACY_DEG = {
    SOURCE_GNSS_COMPASS: 0.2,
    # PROVISIONAL: the EKF publishes a pose covariance and that is the figure to
    # use once somebody confirms robot_localization is filling it in rather than
    # leaving the default. Until then this is a guess at a fused heading, and
    # the panel marks it "assumed" because it is not a measurement.
    SOURCE_EKF: 3.0,
    SOURCE_MAGNETOMETER: 10.0,
    SOURCE_COG: 5.0,
    SOURCE_NONE: float("nan"),
}

#: Below this speed, course over ground is noise and comparing it with heading
#: says nothing. Sitting still with a 40 degree "divergence" is not a fault.
MIN_SPEED_FOR_DIVERGENCE_MS = 0.6

#: Sustained divergence above this, at speed, means the heading is wrong or the
#: current is strong. Either way the operator wants to know.
DIVERGENCE_WARN_DEG = 15.0


@dataclass
class HeadingEstimate:
    heading_deg: float
    source: str
    valid: bool
    accuracy_deg: float
    cog_deg: float
    sog_ms: float
    divergence_deg: float
    divergence_meaningful: bool
    #: True when ``accuracy_deg`` came from the device. False means it is the
    #: nominal figure for the source — a guess about this class of hardware,
    #: not a measurement of this one, and the GUI labels it as such.
    accuracy_reported: bool = False

    @property
    def divergence_suspicious(self) -> bool:
        return (
            self.valid
            and self.divergence_meaningful
            and abs(self.divergence_deg) > DIVERGENCE_WARN_DEG
        )

    def position_error_at_m(self, range_m: float) -> float:
        """Across-track seabed error this heading accuracy implies at a range.

        The number that makes heading matter. At 50 m, one degree is 87 cm.
        """
        if math.isnan(self.accuracy_deg):
            return float("nan")
        return range_m * math.tan(math.radians(self.accuracy_deg))


def evaluate_heading(
    heading_deg: float | None,
    source: str,
    cog_deg: float,
    sog_ms: float,
    reported_accuracy_deg: float | None = None,
    source_valid: bool = True,
) -> HeadingEstimate:
    """Combine a heading and a velocity into an estimate with provenance."""
    valid = source_valid and heading_deg is not None and source != SOURCE_NONE
    hdg = heading_deg if heading_deg is not None else float("nan")

    accuracy = reported_accuracy_deg
    reported = accuracy is not None and not (
        isinstance(accuracy, float) and math.isnan(accuracy)
    )
    if not reported:
        accuracy = NOMINAL_ACCURACY_DEG.get(source, float("nan"))

    meaningful = valid and sog_ms >= MIN_SPEED_FOR_DIVERGENCE_MS
    divergence = angular_difference(hdg, cog_deg) if valid else float("nan")

    return HeadingEstimate(
        heading_deg=hdg,
        source=source if valid else SOURCE_NONE,
        valid=valid,
        accuracy_deg=accuracy if valid else float("nan"),
        cog_deg=cog_deg,
        sog_ms=sog_ms,
        divergence_deg=divergence,
        divergence_meaningful=meaningful,
        accuracy_reported=bool(reported and valid),
    )


#: A heading older than this is not a heading. The GUI drops the EKF heading at
#: the same age (gui_backend ros_source), so the recorder and the pre-flight
#: cannot call valid a heading the screen shows as gone.
MAX_HEADING_AGE_S = 2.0


def compass_deg_from_enu_quaternion(x: float, y: float, z: float, w: float) -> float:
    """Compass bearing (0 = north, clockwise) from a ROS ENU orientation.

    ROS yaw is counter-clockwise from east, hence ``90 - yaw``.
    """
    yaw_enu = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (90.0 - math.degrees(yaw_enu)) % 360.0


def heading_from_odometry(
    heading_deg: float | None,
    age_s: float | None,
    body_vx: float = 0.0,
    body_vy: float = 0.0,
) -> HeadingEstimate:
    """The EKF heading on ``/odometry/filtered``, as the real boat has it.

    ``/asket/heading`` is published only by the simulator; on the boat the
    heading is robot_localization's. ``body_vx`` / ``body_vy`` are the
    odometry twist (forward, left — body frame), so course over ground is the
    heading turned by the drift angle. Stale or absent odometry is an invalid
    heading, never the last value.
    """
    fresh = heading_deg is not None and age_s is not None and age_s < MAX_HEADING_AGE_S
    sog = math.hypot(body_vx, body_vy)
    cog = heading_deg if heading_deg is not None else 0.0
    if sog > 0.05 and heading_deg is not None:
        cog = (heading_deg - math.degrees(math.atan2(body_vy, body_vx))) % 360.0
    return evaluate_heading(
        heading_deg=heading_deg if fresh else None,
        source=SOURCE_EKF if fresh else SOURCE_NONE,
        cog_deg=cog,
        sog_ms=sog,
        source_valid=fresh,
    )
