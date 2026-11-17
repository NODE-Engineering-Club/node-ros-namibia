"""LiPo state of charge from cell voltages, and combining parallel packs.

ROS-free, like everything else in ``asket_common``. Used by
``sensors/bms_reader`` (one instance per BMS) and ``sensors/battery_aggregator``
(the two packs in parallel), and testable on a laptop with ``pytest``.

Why this exists
===============

The BQ76920 BMS reports cell voltages, pack voltage, current and temperature,
but **no state of charge**. ``bms_reader`` used to leave
``BatteryState.percentage`` at its default of 0, and the mission GUI reads that
field as the state of charge: wiring it would have shown 0 % and a permanent
"battery critical" alarm. A voltage-based estimate is the honest minimum.

What it is and is not
=====================

* It is an **estimate from resting voltage**. Under load the cell voltage sags
  (internal resistance), so the figure reads *low* while the thrusters pull and
  recovers when they stop. Low is the safe direction for a "can we finish the
  survey" question. There is no coulomb counting: the BQ76920 does have a
  coulomb counter, but its output is not in the text report this repository
  reads.
* It is computed from the **weakest cell**, not the average. A 4S pack is empty
  when its lowest cell is empty; averaging would hide a failing cell behind
  three good ones.

The curve
=========

A common open-circuit voltage curve for LiPo cells (per cell, at rest, room
temperature). It is the typical RC-hobby LiPo table, not a measurement of the
Gens ace 8000 mAh packs on this boat — **PROVISIONAL**: replace it with a curve
measured on the real packs (discharge at a constant low current, log voltage
against Ah) when there is time.

    4.20 V 100 %   4.15 V 95 %   4.11 V 90 %   4.08 V 85 %   4.02 V 80 %
    3.98 V  75 %   3.95 V 70 %   3.91 V 65 %   3.87 V 60 %   3.85 V 55 %
    3.84 V  50 %   3.82 V 45 %   3.80 V 40 %   3.79 V 35 %   3.77 V 30 %
    3.75 V  25 %   3.73 V 20 %   3.71 V 15 %   3.69 V 10 %   3.61 V  5 %
    3.27 V   0 %

Values in between are interpolated linearly; outside, they are clamped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: (cell volts, state of charge 0..1), ascending in voltage. PROVISIONAL, see above.
LIPO_CELL_CURVE: tuple[tuple[float, float], ...] = (
    (3.27, 0.00),
    (3.61, 0.05),
    (3.69, 0.10),
    (3.71, 0.15),
    (3.73, 0.20),
    (3.75, 0.25),
    (3.77, 0.30),
    (3.79, 0.35),
    (3.80, 0.40),
    (3.82, 0.45),
    (3.84, 0.50),
    (3.85, 0.55),
    (3.87, 0.60),
    (3.91, 0.65),
    (3.95, 0.70),
    (3.98, 0.75),
    (4.02, 0.80),
    (4.08, 0.85),
    (4.11, 0.90),
    (4.15, 0.95),
    (4.20, 1.00),
)

#: A cell reading outside this range is not a LiPo cell voltage (a dropped
#: byte, an unconnected balance lead, the BMS's own "no data") and is ignored
#: rather than read as 0 % or 100 %.
PLAUSIBLE_CELL_V = (2.5, 4.35)

#: Packs this far apart in voltage while in parallel are worth a warning:
#: either one is not connected, or a cell is failing.
PACK_IMBALANCE_WARN_V = 0.3


def _finite(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def cell_state_of_charge(volts: float) -> float | None:
    """State of charge 0..1 of one LiPo cell at rest, or None if implausible."""
    if not _finite(volts) or not (PLAUSIBLE_CELL_V[0] <= volts <= PLAUSIBLE_CELL_V[1]):
        return None
    curve = LIPO_CELL_CURVE
    if volts <= curve[0][0]:
        return curve[0][1]
    if volts >= curve[-1][0]:
        return curve[-1][1]
    for (v_lo, s_lo), (v_hi, s_hi) in zip(curve, curve[1:]):
        if v_lo <= volts <= v_hi:
            return s_lo + (s_hi - s_lo) * (volts - v_lo) / (v_hi - v_lo)
    return None  # unreachable: the curve is ascending and covers the clamp range


def pack_state_of_charge(cell_volts, expected_cells: int | None = None) -> float | None:
    """State of charge 0..1 of a series pack, from its **weakest** cell.

    ``None`` when no cell reading is plausible, or when fewer plausible cells
    than ``expected_cells`` were reported — a missing cell is not evidence the
    others are representative.
    """
    socs = [s for s in (cell_state_of_charge(v) for v in (cell_volts or [])) if s is not None]
    if not socs:
        return None
    if expected_cells is not None and len(socs) < expected_cells:
        return None
    return min(socs)


@dataclass
class PackReading:
    """One BMS's latest report, as the aggregator sees it."""

    name: str
    voltage: float = float("nan")
    current: float = float("nan")
    percentage: float = float("nan")
    temperature: float = float("nan")
    cells: list[float] = field(default_factory=list)
    age_s: float = float("inf")


@dataclass
class CombinedBattery:
    voltage: float
    current: float
    percentage: float
    temperature: float
    cells: list[float]
    packs_used: list[str]
    packs_missing: list[str]
    imbalance_v: float | None
    warnings: list[str]


def combine_parallel_packs(
    packs: list[PackReading], stale_s: float = 5.0
) -> CombinedBattery | None:
    """Combine packs wired **in parallel** into one battery.

    * voltage: mean of the fresh packs (in parallel they share one bus; the
      spread is reported separately as ``imbalance_v``);
    * current: sum (each BMS measures its own pack's share), NaN if none known;
    * percentage: the **lowest** fresh pack — conservative;
    * temperature: the **highest**;
    * cells: those of the pack with the lowest cell, so the per-cell display
      shows the cells that limit the boat.

    ``None`` when no pack has reported within ``stale_s``.
    """
    fresh = [p for p in packs if p.age_s <= stale_s and _finite(p.voltage)]
    missing = [p.name for p in packs if p not in fresh]
    if not fresh:
        return None

    voltages = [p.voltage for p in fresh]
    currents = [p.current for p in fresh if _finite(p.current)]
    socs = [p.percentage for p in fresh if _finite(p.percentage)]
    temps = [p.temperature for p in fresh if _finite(p.temperature)]

    def lowest_cell(p: PackReading) -> float:
        valid = [c for c in p.cells if _finite(c)]
        return min(valid) if valid else float("inf")

    weakest = min(fresh, key=lowest_cell)
    imbalance = (max(voltages) - min(voltages)) if len(voltages) > 1 else None

    warnings = []
    if missing:
        warnings.append(f"no recent report from {', '.join(missing)}")
    if imbalance is not None and imbalance > PACK_IMBALANCE_WARN_V:
        warnings.append(
            f"packs differ by {imbalance:.2f} V while in parallel — one may be "
            "disconnected or have a failing cell"
        )

    return CombinedBattery(
        voltage=sum(voltages) / len(voltages),
        current=sum(currents) if currents else float("nan"),
        percentage=min(socs) if socs else float("nan"),
        temperature=max(temps) if temps else float("nan"),
        cells=list(weakest.cells),
        packs_used=[p.name for p in fresh],
        packs_missing=missing,
        imbalance_v=imbalance,
        warnings=warnings,
    )
