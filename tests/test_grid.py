"""Grid coverage. The bug this exists to catch is silent: cells spaced too far
apart leave diamond-shaped holes at the diagonals, and businesses in the holes
never appear in any scan."""
import math
import random

import pytest

from prospect import CELL_SPACING, grid, haversine

NEWMARKET = (44.0592, -79.4613)


def test_every_point_inside_the_radius_falls_in_some_cell():
    lat, lng = NEWMARKET
    radius_m, cell_m = 6000, 900
    cells = grid(lat, lng, radius_m, cell_m)

    rng = random.Random(7)
    for _ in range(3000):
        # uniform over the disc, not the square
        d = radius_m * math.sqrt(rng.random())
        theta = rng.uniform(0, 2 * math.pi)
        dlat = (d * math.cos(theta) / 6371000.0) * (180 / math.pi)
        dlng = (d * math.sin(theta) / 6371000.0) * (180 / math.pi) / math.cos(math.radians(lat))
        plat, plng = lat + dlat, lng + dlng
        nearest = min(haversine(plat, plng, clat, clng) for clat, clng in cells)
        assert nearest <= cell_m, f"gap at {plat},{plng}: nearest cell {nearest:.0f}m"


def test_spacing_stays_under_the_geometric_limit():
    # A square lattice of circles of radius r covers the plane only when the
    # centres are at most r*sqrt(2) apart.
    assert CELL_SPACING <= math.sqrt(2)


def test_cell_count_grows_with_the_square_of_the_radius():
    lat, lng = NEWMARKET
    small = len(grid(lat, lng, 5000, 900))
    large = len(grid(lat, lng, 10000, 900))
    assert 3.0 < large / small < 5.0


def test_smaller_cells_cost_more_calls():
    lat, lng = NEWMARKET
    assert len(grid(lat, lng, 5000, 450)) > len(grid(lat, lng, 5000, 900))


def test_centre_is_always_covered():
    lat, lng = NEWMARKET
    cells = grid(lat, lng, 1000, 900)
    assert min(haversine(lat, lng, c[0], c[1]) for c in cells) < 1.0


def test_rejects_a_zero_cell():
    with pytest.raises(ValueError):
        grid(*NEWMARKET, 5000, 0)
