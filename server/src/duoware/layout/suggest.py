"""Row/column suggestion from detected positions (PROTOCOL.md §7.2 `POST /api/layout/suggest`, map.toml [suggest]).

Pure. Finds the grid direction from nearest-neighbour vectors (folded with the 4-theta circular mean, so a row
and a column direction count the same), rotates the positions into that frame, sorts each axis and starts a new
row/column where the gap exceeds `gap_fraction` x the median nearest-neighbour distance. Works for rotated,
unevenly spaced, slightly crooked layouts with missing nodes.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

FOLD = 4            # a square grid repeats every 90 degrees, so angles are folded with 4 x theta


@dataclass(frozen=True)
class Suggestion:
    rotation_deg: float                                   # direction of the columns in the floor frame, in (-45, 45]
    spacing_mm: float                                     # median nearest-neighbour distance
    grid: dict[int, tuple[int, int]] = field(default_factory=dict)
    conflicts: list[tuple[tuple[int, ...], tuple[int, int]]] = field(default_factory=list)   # (ids, (row, col))


def _groups(values: np.ndarray, threshold: float) -> np.ndarray:
    """Index of the group each value belongs to: sorted values start a new group after a gap > threshold."""
    order = np.argsort(values)
    idx = np.zeros(len(values), dtype=int)
    g = 0
    for k in range(1, len(order)):
        if values[order[k]] - values[order[k - 1]] > threshold:
            g += 1
        idx[order[k]] = g
    return idx


def suggest_grid(positions: Mapping[int, tuple[float, float]], gap_fraction: float) -> Suggestion:
    ids = sorted(positions)
    if len(ids) == 0:
        return Suggestion(0.0, 0.0)
    if len(ids) == 1:
        return Suggestion(0.0, 0.0, {ids[0]: (0, 0)})
    pts = np.array([positions[i] for i in ids], dtype=np.float64)
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
    np.fill_diagonal(d, np.inf)
    nn = d.argmin(axis=1)
    nn_dist = d[np.arange(len(ids)), nn]
    vec = pts[nn] - pts
    ang = np.arctan2(vec[:, 1], vec[:, 0])
    theta = math.atan2(np.sin(FOLD * ang).sum(), np.cos(FOLD * ang).sum()) / FOLD
    theta_deg = math.degrees(theta)
    spacing = float(np.median(nn_dist))
    c, s = math.cos(theta), math.sin(theta)
    rot = np.c_[pts[:, 0] * c + pts[:, 1] * s, -pts[:, 0] * s + pts[:, 1] * c]        # rotate by -theta
    threshold = gap_fraction * spacing
    col, row = _groups(rot[:, 0], threshold), _groups(rot[:, 1], threshold)
    cells: dict[tuple[int, int], list[int]] = {}
    for k, nid in enumerate(ids):
        cells.setdefault((int(row[k]), int(col[k])), []).append(nid)
    grid = {ids_[0]: cell for cell, ids_ in cells.items() if len(ids_) == 1}
    conflicts = [(tuple(sorted(ids_)), cell) for cell, ids_ in sorted(cells.items()) if len(ids_) > 1]
    return Suggestion(theta_deg, spacing, grid, conflicts)
