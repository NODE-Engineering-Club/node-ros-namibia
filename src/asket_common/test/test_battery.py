"""LiPo state of charge and parallel-pack combination (asket_common.battery)."""

import math

import pytest

from asket_common.battery import (
    LIPO_CELL_CURVE,
    PackReading,
    cell_state_of_charge,
    combine_parallel_packs,
    pack_state_of_charge,
)


def test_curve_is_ascending_and_spans_empty_to_full():
    volts = [v for v, _ in LIPO_CELL_CURVE]
    socs = [s for _, s in LIPO_CELL_CURVE]
    assert volts == sorted(volts) and len(set(volts)) == len(volts)
    assert socs == sorted(socs)
    assert socs[0] == 0.0 and socs[-1] == 1.0


@pytest.mark.parametrize("volts,expected", [
    (4.20, 1.00), (3.84, 0.50), (3.27, 0.00), (4.30, 1.00), (3.00, 0.00),
])
def test_cell_points_on_and_beyond_the_curve(volts, expected):
    assert cell_state_of_charge(volts) == pytest.approx(expected)


def test_cell_is_interpolated_between_points():
    # halfway between 3.84 V (50 %) and 3.85 V (55 %)
    assert cell_state_of_charge(3.845) == pytest.approx(0.525)


@pytest.mark.parametrize("volts", [0.0, -1.0, 5.0, float("nan"), None])
def test_implausible_cell_reading_is_unknown_not_empty(volts):
    assert cell_state_of_charge(volts) is None


def test_pack_uses_the_weakest_cell():
    # three cells near full, one near empty: the pack is near empty
    assert pack_state_of_charge([4.15, 4.15, 4.15, 3.61]) == pytest.approx(0.05)


def test_example_report_from_bms_reader_docstring_is_about_80_percent():
    # bms_reader.py's example block: 3.999 3.987 3.986 4.000 V
    soc = pack_state_of_charge([3.999, 3.987, 3.986, 4.000], expected_cells=4)
    assert 0.75 < soc < 0.80


def test_pack_with_a_missing_cell_is_unknown():
    assert pack_state_of_charge([4.0, 4.0, 4.0], expected_cells=4) is None


def test_pack_with_no_cells_is_unknown_not_zero():
    assert pack_state_of_charge([]) is None
    assert pack_state_of_charge(None) is None


def _pack(name, v, cur, soc, cells, age=1.0, temp=25.0):
    return PackReading(name, v, cur, soc, temp, cells, age)


def test_parallel_packs_combine_conservatively():
    combined = combine_parallel_packs([
        _pack("pack1", 16.0, 3.0, 0.80, [4.0, 4.0, 4.0, 4.0], temp=30.0),
        _pack("pack2", 15.9, 2.5, 0.75, [3.98, 3.98, 3.98, 3.96], temp=28.0),
    ])
    assert combined.voltage == pytest.approx(15.95)
    assert combined.current == pytest.approx(5.5)
    assert combined.percentage == pytest.approx(0.75)  # lowest pack
    assert combined.temperature == pytest.approx(30.0)  # hottest pack
    assert combined.cells == [3.98, 3.98, 3.98, 3.96]   # weakest pack's cells
    assert combined.packs_used == ["pack1", "pack2"]
    assert combined.warnings == []


def test_a_stale_pack_is_left_out_and_reported():
    combined = combine_parallel_packs([
        _pack("pack1", 16.0, 3.0, 0.80, [4.0] * 4),
        _pack("pack2", 15.0, 2.0, 0.10, [3.7] * 4, age=60.0),
    ])
    assert combined.packs_used == ["pack1"]
    assert combined.packs_missing == ["pack2"]
    assert combined.percentage == pytest.approx(0.80)
    assert any("pack2" in w for w in combined.warnings)


def test_imbalance_between_parallel_packs_is_warned():
    combined = combine_parallel_packs([
        _pack("pack1", 16.4, 1.0, 0.9, [4.1] * 4),
        _pack("pack2", 15.6, 1.0, 0.6, [3.9] * 4),
    ])
    assert combined.imbalance_v == pytest.approx(0.8)
    assert any("differ" in w for w in combined.warnings)


def test_no_fresh_pack_means_no_battery_reading():
    assert combine_parallel_packs([_pack("pack1", 16.0, 1.0, 0.8, [4.0] * 4, age=99)]) is None


def test_unknown_current_stays_unknown():
    combined = combine_parallel_packs([_pack("pack1", 16.0, float("nan"), 0.8, [4.0] * 4)])
    assert math.isnan(combined.current)
