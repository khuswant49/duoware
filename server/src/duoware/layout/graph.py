"""The road-network graph: edges, grid rotation, edge geometry and validation warnings. Pure (no I/O).

Edges exist only between orthogonal neighbours, `(r, c)`-`(r, c+1)` and `(r, c)`-`(r+1, c)`, both nodes present
(PROTOCOL.md §7.5, DECISIONS.md D24): never diagonal, never across a missing node.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from duoware.localization.geometry import wrap_deg
from duoware.settings import CarConfig, ValidationRules

GridPos = tuple[int, int]
Edge = tuple[int, int]
Point = tuple[float, float]
COLUMN_EDGE_OFFSET_DEG = 90.0      # a column edge runs 90 degrees clockwise from the row direction (y-down frame)


@dataclass(frozen=True)
class LayoutWarning:
    code: str
    ids: tuple[int, ...]
    message: str


def build_edges(nodes: Mapping[int, GridPos]) -> list[Edge]:
    """Edges `(a, b)` with `a` the node with the smaller `(row, col)`, sorted by `a`'s then `b`'s position."""
    by_pos = {pos: nid for nid, pos in nodes.items()}
    edges = []
    for (r, c), a in sorted(by_pos.items()):
        for nb in ((r, c + 1), (r + 1, c)):
            if nb in by_pos:
                edges.append((a, by_pos[nb]))
    return edges


def is_row_edge(a: GridPos, b: GridPos) -> bool:
    return a[0] == b[0]


def bearing_deg(p: Point, q: Point) -> float:
    """Direction from p to q, degrees clockwise from +x (y down)."""
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))


def fit_grid_rotation(nodes_grid: Mapping[int, GridPos], positions: Mapping[int, Point | None],
                      edges: Sequence[Edge]) -> float:
    """DECISIONS.md D27: circular mean of the edge bearings, row edges as measured and column edges minus 90
    degrees. 0 when no edge has both positions."""
    sx = sy = 0.0
    for a, b in edges:
        pa, pb = positions.get(a), positions.get(b)
        if pa is None or pb is None:
            continue
        ang = bearing_deg(pa, pb) - (0.0 if is_row_edge(nodes_grid[a], nodes_grid[b]) else COLUMN_EDGE_OFFSET_DEG)
        sx += math.cos(math.radians(ang))
        sy += math.sin(math.radians(ang))
    return 0.0 if sx == 0 and sy == 0 else wrap_deg(math.degrees(math.atan2(sy, sx)))


def edge_geometry(a: int, b: int, positions: Mapping[int, Point | None], rotation_deg: float,
                  row_edge: bool) -> tuple[float, float, float] | None:
    """(length_mm, bearing_deg, angle_error_deg) of the edge `a`->`b`, or None if either node is unmeasured."""
    pa, pb = positions.get(a), positions.get(b)
    if pa is None or pb is None:
        return None
    bearing = bearing_deg(pa, pb)
    ideal = rotation_deg + (0.0 if row_edge else COLUMN_EDGE_OFFSET_DEG)
    return math.hypot(pb[0] - pa[0], pb[1] - pa[1]), bearing, wrap_deg(bearing - ideal)


def point_in_polygon(p: Point, poly: Sequence[Sequence[float]]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1, x2, y2 = poly[i][0], poly[i][1], poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def distance_to_polygon_edge(p: Point, poly: Sequence[Sequence[float]]) -> float:
    return min(point_segment_distance(p, (poly[i][0], poly[i][1]), (poly[(i + 1) % len(poly)][0], poly[(i + 1) % len(poly)][1]))
               for i in range(len(poly)))


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    pa, ab = np.array(p) - np.array(a), np.array(b) - np.array(a)
    denom = float(ab @ ab)
    t = 0.0 if denom == 0 else max(0.0, min(1.0, float(pa @ ab) / denom))
    return float(np.linalg.norm(pa - t * ab))


def validate(nodes_grid: Mapping[int, GridPos], positions: Mapping[int, Point | None], edges: Sequence[Edge],
             rotation_deg: float, rules: ValidationRules, cars: Sequence[CarConfig],
             footprints: Sequence[Sequence[Sequence[float]]], blocked_edge_keys: set[Edge] | None = None,
             blocked_nodes: set[int] | None = None) -> list[LayoutWarning]:
    """Warnings never change the layout (PROTOCOL.md §7.5 `warnings[].code`)."""
    out: list[LayoutWarning] = []
    longest_car = max((max(c.footprint_mm) for c in cars), default=0.0)
    min_len = longest_car + rules.edge_clearance_mm
    for a, b in edges:
        g = edge_geometry(a, b, positions, rotation_deg, is_row_edge(nodes_grid[a], nodes_grid[b]))
        if g is None:
            continue
        length, _, err = g
        if abs(err) > rules.max_edge_angle_deg:
            kind = "row" if is_row_edge(nodes_grid[a], nodes_grid[b]) else "column"
            out.append(LayoutWarning("edge_angle", (a, b), f"Edge {a}-{b} is {abs(err):.1f}° off its {kind} direction "
                                                    f"(max {rules.max_edge_angle_deg:g}°)."))
        if length < min_len:
            out.append(LayoutWarning("edge_short", (a, b), f"Edge {a}-{b} is {length:.0f} mm; a car needs at least "
                                                    f"{min_len:.0f} mm ({longest_car:g} mm car + {rules.edge_clearance_mm:g} mm)."))
        if rules.expected_spacing_mm > 0 and abs(length - rules.expected_spacing_mm) > rules.spacing_tolerance_mm:
            out.append(LayoutWarning("spacing_mismatch", (a, b), f"Edge {a}-{b} is {length:.0f} mm, expected "
                                                          f"{rules.expected_spacing_mm:g} ± {rules.spacing_tolerance_mm:g} mm."))
    if footprints:
        for nid, p in positions.items():
            if p is None or nid not in nodes_grid:
                continue
            margins = [(distance_to_polygon_edge(p, poly) if point_in_polygon(p, poly) else -distance_to_polygon_edge(p, poly))
                       for poly in footprints]
            if all(m < rules.view_margin_mm for m in margins):
                out.append(LayoutWarning("node_outside_view", (nid,), f"Node {nid} is within {rules.view_margin_mm:g} mm of "
                                                               "the edge of every camera view (or outside it)."))
    linked: set[int] = set()
    for a, b in edges:
        if (blocked_edge_keys is None or (a, b) not in blocked_edge_keys) and not (blocked_nodes and {a, b} & blocked_nodes):
            linked.update((a, b))
    for nid in sorted(nodes_grid):
        if nid not in linked:
            out.append(LayoutWarning("isolated_node", (nid,), f"Node {nid} has no usable edge to a neighbour."))
    return out
