"""Metric refinement of a floor homography (DECISIONS.md D13: nothing is measured by hand).

A homography fitted to *automatically located* floor tags only reproduces the positions those tags were given,
and a first location made from one small tag through a similarity carries the tilt of the phone with it (a 2
degree tilt is ~1.9 % scale change across 1 m: ~19 mm). What the camera does know exactly is that every tag is a
square of its printed size. This adjusts the mapping (8 parameters) and the pose of every automatically located
tag together, so that each tag's image is a square of its printed size at its pose, with the origin (and any
typed-in) tag held fixed. It is a small Levenberg-Marquardt problem (numpy only).
"""

import math

import numpy as np

from duoware.localization.floor_tags import FloorTag
from duoware.localization.geometry import square_corners

SCALE_MM = 1000.0          # parameter scaling: translations in metres keep the problem well conditioned
MAX_ITERATIONS = 30
FD_STEP = 1e-6             # finite-difference step in scaled parameter units
LAMBDA_START, LAMBDA_UP, LAMBDA_DOWN = 1e-3, 10.0, 0.3
CONVERGED_STEP = 1e-9
MAX_POSITION_CHANGE_MM = 150.0     # a refinement that moves a tag further than this is a failed fit, not a refinement


def _project(g: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.c_[pts, np.ones(len(pts))] @ g.T
    return p[:, :2] / p[:, 2:3]


def _delta(p: np.ndarray) -> np.ndarray:
    return np.array([[1 + p[0], p[1], p[4] * SCALE_MM], [p[2], 1 + p[3], p[5] * SCALE_MM], [p[6] / SCALE_MM, p[7] / SCALE_MM, 1.0]])


def refine_homography(h_img2floor: np.ndarray, imgs: dict[int, np.ndarray], tags: dict[int, FloorTag]
                      ) -> tuple[np.ndarray, dict[int, tuple[float, float, float]]] | None:
    """`imgs`: tag id -> (4, 2) image corners; `tags`: placed floor tags. Returns (new image->floor homography,
    refined (x, y, yaw) of the automatically located tags) or None if it does not converge to something sane."""
    ids = [t for t in imgs if t in tags]
    free = [t for t in ids if tags[t].source == "auto"]
    if len(ids) < 3 or not free:
        return None
    g0 = np.linalg.inv(h_img2floor)
    g0 = g0 / g0[2, 2]
    x0 = np.concatenate([np.zeros(8)] + [[tags[t].x / SCALE_MM, tags[t].y / SCALE_MM, math.radians(tags[t].yaw)] for t in free])

    def residual(x: np.ndarray) -> np.ndarray:
        g = g0 @ _delta(x[:8])
        out = []
        for t in ids:
            if t in free:
                k = 8 + 3 * free.index(t)
                corners = square_corners(x[k] * SCALE_MM, x[k + 1] * SCALE_MM, tags[t].size_mm, math.degrees(x[k + 2]))
            else:
                corners = tags[t].world_corners()
            out.append((_project(g, corners) - imgs[t]).ravel())
        return np.concatenate(out)

    x, lam, converged = x0.copy(), LAMBDA_START, False
    r = residual(x)
    cost = float(r @ r)
    for _ in range(MAX_ITERATIONS):
        jac = np.empty((len(r), len(x)))
        for k in range(len(x)):
            d = np.zeros(len(x))
            d[k] = FD_STEP
            jac[:, k] = (residual(x + d) - residual(x - d)) / (2 * FD_STEP)
        a, b = jac.T @ jac, jac.T @ r
        improved = False
        for _ in range(12):
            try:
                step = np.linalg.solve(a + lam * np.diag(np.diag(a) + 1e-12), -b)
            except np.linalg.LinAlgError:
                lam *= LAMBDA_UP
                continue
            r_new = residual(x + step)
            cost_new = float(r_new @ r_new)
            if cost_new < cost:
                x, r, lam = x + step, r_new, lam * LAMBDA_DOWN
                converged = float(np.linalg.norm(step)) < CONVERGED_STEP or cost - cost_new < 1e-12 * max(cost, 1e-30)
                cost, improved = cost_new, True
                break
            lam *= LAMBDA_UP
        if not improved or converged:
            break
    g = g0 @ _delta(x[:8])
    try:
        h = np.linalg.inv(g)
    except np.linalg.LinAlgError:
        return None
    h = h / h[2, 2]
    poses = {t: (x[8 + 3 * i] * SCALE_MM, x[9 + 3 * i] * SCALE_MM, math.degrees(x[10 + 3 * i])) for i, t in enumerate(free)}
    if any(math.hypot(poses[t][0] - tags[t].x, poses[t][1] - tags[t].y) > MAX_POSITION_CHANGE_MM for t in free):
        return None
    return h, poses
