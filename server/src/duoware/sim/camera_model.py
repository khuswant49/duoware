"""A pinhole camera with radial distortion looking down at the floor (the simulated phone's camera).

Frames: floor (x right, y down the map, height h above the floor). Camera: X right, Y down, Z forward (towards
the floor). A level, unrotated camera has X = floor +x and Y = floor +y, so a floor map seen from above looks
like the image. `yaw_deg` turns the camera clockwise about the vertical axis; `tilt_deg` = (pitch, roll) tips
it about its own X and Y axes. Radial distortion follows DUO-WARE 1 radial_lens_maps: normalised coordinates
(in units of the focal length) are scaled by 1 + k1 r^2.
"""

import math

import numpy as np


def _rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class PinholeCamera:
    def __init__(self, position_mm: tuple[float, float, float], tilt_deg: tuple[float, float], yaw_deg: float,
                 resolution: tuple[int, int], hfov_deg: float, k1: float) -> None:
        self.position = np.asarray(position_mm, dtype=np.float64)
        self.resolution = (int(resolution[0]), int(resolution[1]))
        self.k1 = k1
        self.f_px = (self.resolution[0] / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
        self.rot = (_rot_z(math.radians(yaw_deg)) @ _rot_x(math.radians(tilt_deg[0])) @ _rot_y(math.radians(tilt_deg[1])))

    def project(self, points_xyz_mm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(N, 3) points (x, y, height above floor) -> pixels (N, 2) and a visibility mask (in front of the
        camera and inside the image)."""
        p = np.asarray(points_xyz_mm, dtype=np.float64).reshape(-1, 3)
        v = np.stack([p[:, 0] - self.position[0], p[:, 1] - self.position[1], self.position[2] - p[:, 2]], axis=1)
        c = v @ self.rot                                     # = (R^T v) for each row
        z = np.where(c[:, 2] > 1e-6, c[:, 2], np.nan)
        a, b = c[:, 0] / z, c[:, 1] / z
        scale = 1.0 + self.k1 * (a * a + b * b)
        u = self.f_px * a * scale + self.resolution[0] / 2.0
        w = self.f_px * b * scale + self.resolution[1] / 2.0
        px = np.stack([u, w], axis=1)
        ok = np.isfinite(px).all(axis=1) & (u >= 0) & (u <= self.resolution[0]) & (w >= 0) & (w <= self.resolution[1])
        return px, ok
