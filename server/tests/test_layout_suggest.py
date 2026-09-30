"""Step 7 / A6: row/column suggestion for the sim world and for random rotated, uneven, crooked layouts."""

import math
import random

import pytest
from test_layout_graph import sim_layout

from duoware.layout.suggest import suggest_grid
from duoware.settings import load_settings

GAP = load_settings().map_rules.suggest.gap_fraction


def test_sim_world():
    grid, pos = sim_layout()
    s = suggest_grid(pos, GAP)
    assert s.grid == grid and s.conflicts == []
    assert s.rotation_deg == pytest.approx(8.0, abs=1.5) and 380 < s.spacing_mm < 470


def test_degenerate_inputs():
    assert suggest_grid({}, GAP).grid == {}
    assert suggest_grid({5: (10.0, 10.0)}, GAP).grid == {5: (0, 0)}
    s = suggest_grid({1: (0.0, 0.0), 2: (400.0, 0.0)}, GAP)
    assert s.grid == {1: (0, 0), 2: (0, 1)}


def test_two_tags_in_one_cell_are_a_conflict_and_get_no_suggestion():
    grid, pos = sim_layout()
    pos[99] = (pos[6][0] + 12, pos[6][1] - 9)                            # a stray tag next to node 6
    s = suggest_grid(pos, GAP)
    assert s.conflicts == [((6, 99), (1, 1))] and 6 not in s.grid and 99 not in s.grid
    assert len(s.grid) == 9 - 1


def random_layout(rng: random.Random):
    """(positions, expected {id: (row, col)} compressed to the present rows/columns, rotation_deg)."""
    rows, cols = rng.randint(2, 5), rng.randint(2, 5)
    spacing = rng.uniform(300, 600)
    xs, ys = [0.0], [0.0]
    for _ in range(cols - 1):
        xs.append(xs[-1] + spacing * rng.uniform(0.7, 1.3))
    for _ in range(rows - 1):
        ys.append(ys[-1] + spacing * rng.uniform(0.7, 1.3))
    rot = rng.uniform(-30, 30)
    cr, sr = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    ox, oy = rng.uniform(-500, 500), rng.uniform(-500, 500)
    pos, cells = {}, {}
    ids = rng.sample(range(50), rows * cols)
    k = 0
    for r in range(rows):
        for c in range(cols):
            nid = ids[k]
            k += 1
            if rng.random() < 0.2:
                continue
            x, y = xs[c] + rng.uniform(-15, 15), ys[r] + rng.uniform(-15, 15)
            pos[nid] = (ox + x * cr - y * sr, oy + x * sr + y * cr)
            cells[nid] = (r, c)
    used_r, used_c = sorted({v[0] for v in cells.values()}), sorted({v[1] for v in cells.values()})
    expected = {i: (used_r.index(r), used_c.index(c)) for i, (r, c) in cells.items()}
    return pos, expected, rot


def test_random_rotated_uneven_crooked_layouts_with_missing_nodes():
    rng = random.Random(7)
    checked = 0
    for trial in range(300):
        pos, expected, rot = random_layout(rng)
        if len(pos) < 4:
            continue
        s = suggest_grid(pos, GAP)
        assert s.conflicts == [], trial
        assert s.grid == expected, (trial, rot)
        assert abs(s.rotation_deg - rot) < 10.0, (trial, s.rotation_deg, rot)       # +-15 mm crooks on short edges
        checked += 1
    assert checked > 250
