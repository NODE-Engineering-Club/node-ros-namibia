"""The survey geofence: validated from its file, rasterised into a keep-out mask."""

import pytest

from asket_common.geofence import (
    FREE,
    LETHAL,
    GeofenceError,
    contains,
    load_geofence,
    rasterize,
    to_map_frame,
)

#: Roughly 1.0 x 0.9 km off Walvis Bay.
SQUARE = {"geofence": {"name": "test block", "polygon": [
    [-22.9500, 14.4800], [-22.9500, 14.4900], [-22.9580, 14.4900], [-22.9580, 14.4800],
]}}


def test_a_valid_fence_loads():
    fence = load_geofence(SQUARE)
    assert fence.name == "test block"
    assert len(fence.polygon) == 4


def test_a_closed_polygon_is_accepted_and_not_doubled():
    doc = {"geofence": {"polygon": SQUARE["geofence"]["polygon"] + [[-22.9500, 14.4800]]}}
    assert len(load_geofence(doc).polygon) == 4


@pytest.mark.parametrize("doc,why", [
    ({}, "no 'geofence:'"),
    ({"geofence": {"polygon": [[0, 0], [0, 1]]}}, "at least three"),
    ({"geofence": {"polygon": [[0, 0], [0, 1], ["x", 2]]}}, "not a [lat, lon]"),
    ({"geofence": {"polygon": [[-122.9, 14.48], [-22.95, 14.49], [-22.96, 14.49]]}}, "not a valid"),
    # lat/lon swapped on one corner: tens of km away
    ({"geofence": {"polygon": [[-22.95, 14.48], [-22.95, 14.49], [14.49, -22.958]]}}, "spans more"),
    ({"geofence": {"polygon": [[-22.95, 14.48], [-22.95, 14.48001], [-22.95001, 14.48]]}}, "no area"),
    # bow-tie: corners out of order
    ({"geofence": {"polygon": [[-22.950, 14.480], [-22.958, 14.490],
                               [-22.950, 14.490], [-22.958, 14.480]]}}, "crosses itself"),
])
def test_a_bad_fence_is_refused_with_a_reason(doc, why):
    with pytest.raises(GeofenceError, match=why.replace("[", r"\[").replace("]", r"\]")):
        load_geofence(doc)


def test_the_mask_is_free_inside_and_lethal_outside():
    fence = load_geofence(SQUARE)
    poly = to_map_frame(fence, -22.9540, 14.4850)        # datum in the middle
    mask = rasterize(poly, resolution_m=5.0, margin_m=30.0)

    def value(x, y):
        col = int((x - mask.origin_x_m) / mask.resolution_m)
        row = int((y - mask.origin_y_m) / mask.resolution_m)
        return mask.data[row * mask.width + col]

    assert value(0.0, 0.0) == FREE                       # the datum, inside
    xs = [p[0] for p in poly]
    assert value(max(xs) + 15.0, 0.0) == LETHAL          # the ring outside
    assert value(min(xs) - 15.0, 0.0) == LETHAL
    assert len(mask.data) == mask.width * mask.height
    # roughly the right number of free cells (area / cell area, edges aside)
    free = mask.data.count(FREE)
    assert abs(free * 25.0 - mask.area_m2) / mask.area_m2 < 0.05


def test_a_concave_fence_keeps_its_notch_lethal():
    # an L shape: the inner corner is outside the fence
    doc = {"geofence": {"polygon": [
        [-22.9500, 14.4800], [-22.9500, 14.4900], [-22.9540, 14.4900],
        [-22.9540, 14.4850], [-22.9580, 14.4850], [-22.9580, 14.4800],
    ]}}
    poly = to_map_frame(load_geofence(doc), -22.9500, 14.4800)
    mask = rasterize(poly, resolution_m=5.0)
    notch_x, notch_y = 0.75 * max(p[0] for p in poly), 0.75 * min(p[1] for p in poly)
    assert not contains(poly, notch_x, notch_y)
    col = int((notch_x - mask.origin_x_m) / mask.resolution_m)
    row = int((notch_y - mask.origin_y_m) / mask.resolution_m)
    assert mask.data[row * mask.width + col] == LETHAL


def test_a_huge_fence_gets_a_coarser_grid_not_a_huge_one():
    doc = {"geofence": {"polygon": [[-22.90, 14.40], [-22.90, 14.55], [-23.03, 14.55], [-23.03, 14.40]]}}
    mask = rasterize(to_map_frame(load_geofence(doc), -22.95, 14.45), resolution_m=1.0)
    assert mask.width * mask.height <= 4_000_000
    assert mask.resolution_m > 1.0
