"""Pure geometry in the floor frame (PROTOCOL.md §0): millimetres, +x right, +y down, degrees clockwise."""

import math

import cv2
import numpy as np

CORNERS = 4       # an ArUco marker has four corners (PROTOCOL.md §0 order: TL, TR, BR, BL)


def wrap_deg(a: float) -> float:
    """Normalise to (-180, 180]."""
    a = math.fmod(a, 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a


def square_corners(cx: float, cy: float, size: float, yaw_deg: float) -> np.ndarray:
    """Corners (TL, TR, BR, BL) of a square marker whose top edge faces `yaw_deg`, shape (4, 2).
    Ported from DUO-WARE 1 vision/world.py."""
    u = np.array([math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))])
    r = np.array([-u[1], u[0]])                      # 90 degrees clockwise from u
    h = size / 2.0
    c = np.array([cx, cy], dtype=np.float64)
    return np.array([c + h * (-r + u), c + h * (r + u), c + h * (r - u), c + h * (-r - u)])


def heading_from_corners(corners: np.ndarray) -> float:
    """Marker heading: from the midpoint of the bottom edge (3-2) to the midpoint of the top edge (0-1)."""
    top = (corners[0] + corners[1]) / 2.0
    bottom = (corners[3] + corners[2]) / 2.0
    return math.degrees(math.atan2(top[1] - bottom[1], top[0] - bottom[0]))


def side_length_mm(corners: np.ndarray) -> float:
    return float(np.mean([np.linalg.norm(corners[i] - corners[(i + 1) % CORNERS]) for i in range(CORNERS)]))


def to_floor(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Maps image points through a 3x3 homography. `pts` (N, 2) -> (N, 2)."""
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, h).reshape(-1, 2)


def parallax_correct(pts: np.ndarray, nadir: np.ndarray, scale: float) -> np.ndarray:
    """A point `scale` = H / (H - h) times too far from the nadir (h above the floor) is pulled back towards it."""
    return nadir + (np.asarray(pts, dtype=np.float64) - nadir) / scale


def apply_offset(center: np.ndarray, heading_deg: float, offset_mm: tuple[float, float]) -> np.ndarray:
    """`center` + `offset_mm` = (forward, left) in the car frame at `heading_deg`. In this clockwise, y-down
    frame "left" is 90 degrees counter-clockwise from the heading, i.e. heading - 90."""
    h = math.radians(heading_deg)
    fwd = np.array([math.cos(h), math.sin(h)])
    left = np.array([math.sin(h), -math.cos(h)])            # heading - 90 degrees
    return np.asarray(center, dtype=np.float64) + offset_mm[0] * fwd + offset_mm[1] * left
