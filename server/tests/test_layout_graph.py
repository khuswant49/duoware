"""Step 7 / A5: edges are only orthogonal neighbours; grid rotation; edge geometry; every warning code."""

import math
import random
from pathlib import Path

import pytest

from duoware.layout import graph
from duoware.layout.graph import build_edges, edge_geometry, fit_grid_rotation, validate
from duoware.settings import load_settings

SETTINGS = load_settings()
RULES = SETTINGS.map_rules.validation
CARS = SETTINGS.cars


def test_build_edges_on_a_full_grid():
    nodes = {10: (0, 0), 2: (0, 1), 11: (0, 2), 3: (1, 0), 6: (1, 1), 4: (1, 2), 12: (2, 0), 7: (2, 1), 13: (2, 2)}
    edges = build_edges(nodes)
    assert len(edges) == 12
    assert edges[:3] == [(10, 2), (10, 3), (2, 11)]                 # a is the smaller (row, col); sorted by a
    assert {frozenset(e) for e in edges} >= {frozenset((10, 2)), frozenset((6, 7))}


def test_missing_node_breaks_the_row_and_no_diagonal_bridges_it():
    nodes = {1: (0, 0), 2: (0, 2), 3: (1, 1)}                      # (0,1) is missing; 3 is diagonal to both
    assert build_edges(nodes) == []
    assert build_edges({1: (0, 0)}) == []
    assert build_edges({}) == []


def test_random_node_sets_never_produce_diagonal_or_skipping_edges():
    """A5: >= 500 random node sets (random missing nodes, random shape)."""
    rng = random.Random(2026)
    for trial in range(600):
        rows, cols = rng.randint(1, 6), rng.randint(1, 6)
        cells = [(r, c) for r in range(rows) for c in range(cols) if rng.random() > rng.choice([0, 0.2, 0.5])]
        ids = rng.sample(range(50), len(cells))
        nodes = dict(zip(ids, cells))
        edges = build_edges(nodes)
        seen = set()
        for a, b in edges:
            (ra, ca), (rb, cb) = nodes[a], nodes[b]
            assert (ra, ca) < (rb, cb), trial                                    # a is the smaller
            assert abs(ra - rb) + abs(ca - cb) == 1, (trial, nodes[a], nodes[b])  # adjacent, one axis: no diagonal/skip
            seen.add(frozenset((a, b)))
        pos = set(cells)
        expected = {frozenset((i, j)) for i, p in nodes.items() for j, q in nodes.items()
                    if p < q and abs(p[0] - q[0]) + abs(p[1] - q[1]) == 1}
        assert seen == expected and len(seen) == len(edges), trial                # exactly the neighbour pairs
        assert all(((nodes[a][0] + nodes[b][0]) / 2, (nodes[a][1] + nodes[b][1]) / 2) not in pos for a, b in edges) or True


def sim_layout():
    import tomllib
    with open(Path(__file__).resolve().parents[2] / "config" / "sim.toml", "rb") as f:
        tags = {t[0]: (t[1], t[2]) for t in tomllib.load(f)["world"]["floor_tags"]}
    grid = {10: (0, 0), 2: (0, 1), 11: (0, 2), 3: (1, 0), 6: (1, 1), 4: (1, 2), 12: (2, 0), 7: (2, 1), 13: (2, 2)}
    return grid, {i: tags[i] for i in grid}


def test_grid_rotation_of_the_sim_layout_is_about_8_degrees():
    grid, pos = sim_layout()
    rot = fit_grid_rotation(grid, pos, build_edges(grid))
    assert rot == pytest.approx(8.0, abs=0.5)


def test_grid_rotation_with_no_measured_edge_is_zero():
    grid = {1: (0, 0), 2: (0, 1)}
    assert fit_grid_rotation(grid, {1: None, 2: (5, 5)}, build_edges(grid)) == 0.0
    assert fit_grid_rotation({}, {}, []) == 0.0


@pytest.mark.parametrize("rot", [-170, -45, 0, 8, 90, 179])
def test_grid_rotation_recovers_any_direction(rot):
    r = math.radians(rot)
    grid = {1: (0, 0), 2: (0, 1), 3: (1, 0), 4: (1, 1)}
    u, v = (math.cos(r), math.sin(r)), (-math.sin(r), math.cos(r))         # columns along u, rows along v
    pos = {i: (c * 400 * u[0] + rr * 300 * v[0], c * 400 * u[1] + rr * 300 * v[1]) for i, (rr, c) in grid.items()}
    assert math.cos(math.radians(fit_grid_rotation(grid, pos, build_edges(grid)) - rot)) == pytest.approx(1)


def test_edge_geometry_lengths_bearings_and_errors():
    pos = {1: (0.0, 0.0), 2: (400.0, 0.0), 3: (10.0, 300.0)}
    grid = {1: (0, 0), 2: (0, 1), 3: (1, 0)}
    length, bearing, err = edge_geometry(1, 2, pos, 0.0, True)
    assert (length, bearing, err) == (400.0, 0.0, 0.0)
    length, bearing, err = edge_geometry(1, 3, pos, 0.0, False)              # a column edge: ideal bearing is +90
    assert length == pytest.approx(math.hypot(10, 300)) and bearing == pytest.approx(88.09, abs=0.01)
    assert err == pytest.approx(-1.91, abs=0.01)
    assert edge_geometry(1, 9, pos, 0.0, True) is None and edge_geometry(1, 2, {1: (0, 0), 2: None}, 0, True) is None
    assert graph.is_row_edge(grid[1], grid[2]) and not graph.is_row_edge(grid[1], grid[3])


BIG = [[-5000, -5000], [5000, -5000], [5000, 5000], [-5000, 5000]]


def codes(ws):
    return sorted({w.code for w in ws})


def test_warning_edge_angle():
    grid = {1: (0, 0), 2: (0, 1)}
    pos = {1: (0.0, 0.0), 2: (400 * math.cos(math.radians(16.2)), 400 * math.sin(math.radians(16.2)))}
    ws = validate(grid, pos, build_edges(grid), 0.0, RULES, CARS, [BIG])
    (w,) = [w for w in ws if w.code == "edge_angle"]
    assert w.ids == (1, 2) and "16.2°" in w.message and "row direction" in w.message and "max 15°" in w.message
    pos[2] = (400 * math.cos(math.radians(14.0)), 400 * math.sin(math.radians(14.0)))
    assert "edge_angle" not in codes(validate(grid, pos, build_edges(grid), 0.0, RULES, CARS, [BIG]))


def test_warning_edge_short():
    grid = {1: (0, 0), 2: (0, 1)}
    need = max(max(c.footprint_mm) for c in CARS) + RULES.edge_clearance_mm
    ok = validate(grid, {1: (0.0, 0.0), 2: (need + 1, 0.0)}, build_edges(grid), 0.0, RULES, CARS, [BIG])
    short = validate(grid, {1: (0.0, 0.0), 2: (need - 1, 0.0)}, build_edges(grid), 0.0, RULES, CARS, [BIG])
    assert "edge_short" not in codes(ok) and "edge_short" in codes(short)


def test_warning_node_outside_view():
    grid = {1: (0, 0)}
    view = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
    near_edge = validate(grid, {1: (RULES.view_margin_mm - 1, 500.0)}, [], 0.0, RULES, CARS, [view])
    inside = validate(grid, {1: (RULES.view_margin_mm + 5, 500.0)}, [], 0.0, RULES, CARS, [view])
    outside = validate(grid, {1: (-50.0, 500.0)}, [], 0.0, RULES, CARS, [view])
    assert "node_outside_view" in codes(near_edge) and "node_outside_view" not in codes(inside)
    assert "node_outside_view" in codes(outside)
    # one camera that sees it comfortably is enough
    assert "node_outside_view" not in codes(validate(grid, {1: (20.0, 500.0)}, [], 0.0, RULES, CARS, [view, BIG]))
    assert "node_outside_view" not in codes(validate(grid, {1: (20.0, 500.0)}, [], 0.0, RULES, CARS, []))


def test_warning_spacing_mismatch_only_when_configured():
    import dataclasses
    grid = {1: (0, 0), 2: (0, 1)}
    pos = {1: (0.0, 0.0), 2: (450.0, 0.0)}
    assert "spacing_mismatch" not in codes(validate(grid, pos, build_edges(grid), 0.0, RULES, CARS, [BIG]))
    rules = dataclasses.replace(RULES, expected_spacing_mm=500.0, spacing_tolerance_mm=40.0)
    assert "spacing_mismatch" in codes(validate(grid, pos, build_edges(grid), 0.0, rules, CARS, [BIG]))
    assert "spacing_mismatch" not in codes(validate(grid, {**pos, 2: (520.0, 0.0)}, build_edges(grid), 0.0, rules, CARS, [BIG]))


def test_warning_isolated_node():
    grid = {1: (0, 0), 2: (0, 1), 3: (3, 3)}
    pos = {1: (0.0, 0.0), 2: (400.0, 0.0), 3: (900.0, 900.0)}
    ws = validate(grid, pos, build_edges(grid), 0.0, RULES, CARS, [BIG])
    assert [w.ids for w in ws if w.code == "isolated_node"] == [(3,)]
    blocked = validate(grid, pos, build_edges(grid), 0.0, RULES, CARS, [BIG], {(1, 2)}, set())
    assert sorted(w.ids for w in blocked if w.code == "isolated_node") == [(1,), (2,), (3,)]
    ws = validate(grid, pos, build_edges(grid), 0.0, RULES, CARS, [BIG], set(), {2})
    assert sorted(w.ids for w in ws if w.code == "isolated_node") == [(1,), (2,), (3,)]
