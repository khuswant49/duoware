"""
DUO-WARE Phone Lens Calibration (item 15)

Phone lenses bend straight lines (barrel distortion). The floor calibration (vision/world.py)
assumes an ideal pinhole camera, so distortion shows up as position error that grows towards the
image edges; in the realistic simulator it was the largest source of still-car error (context.md 4b).

A lens profile is a property of the PHONE, not of the room, so it stays valid when the test area
and the tags change. It is measured once per phone with a printed ChArUco board:

  1. tools/lens_calibration.py board          -> print the board (A4, 100 %)
  2. film it from ~15 angles with that phone   (the phone camera page records to recordings/)
  3. tools/lens_calibration.py calibrate DIR   -> calibration/lens_<name>.json
  4. run.py ... --lens 1:calibration/lens_<name>.json

The board uses the 5x5 ArUco family, so its markers can never be mistaken for the 4x4 car,
station and floor tags. In the pipeline only the detected tag CORNERS are corrected
(undistort_markers), not whole images: cheap, and the dashboard still shows the real camera view.
"""

import json
import math
import os
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# Calibration board: 5 x 7 squares of 30 mm, 22 mm markers from the 5x5 family (fits on A4)
BOARD_SQUARES = (5, 7)
BOARD_SQUARE_MM = 30.0
BOARD_MARKER_MM = 22.0
BOARD_DICT = cv2.aruco.DICT_5X5_100


def make_board(square_mm: float = BOARD_SQUARE_MM, marker_mm: float = BOARD_MARKER_MM) -> "cv2.aruco.CharucoBoard":
    return cv2.aruco.CharucoBoard(BOARD_SQUARES, square_mm, marker_mm,
                                  cv2.aruco.getPredefinedDictionary(BOARD_DICT))


def board_image(board, px_per_mm: float, margin_mm: float = 10.0) -> np.ndarray:
    """The board as a greyscale image at a given print resolution."""
    w = int(round((BOARD_SQUARES[0] * board.getSquareLength() + 2 * margin_mm) * px_per_mm))
    h = int(round((BOARD_SQUARES[1] * board.getSquareLength() + 2 * margin_mm) * px_per_mm))
    return board.generateImage((w, h), marginSize=int(round(margin_mm * px_per_mm)), borderBits=1)


def detect_board(gray: np.ndarray, board) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """ChArUco corner detections of one image: (object points mm (N,3), image points px (N,2))."""
    det = cv2.aruco.CharucoDetector(board)
    corners, ids, _, _ = det.detectBoard(gray)
    if ids is None or len(ids) < 8:
        return None, None
    obj, img = board.matchImagePoints(corners, ids)
    return obj.reshape(-1, 3).astype(np.float32), img.reshape(-1, 2).astype(np.float32)


@dataclass
class LensModel:
    camera_matrix: np.ndarray            # 3x3
    dist_coeffs: np.ndarray              # k1, k2, p1, p2, k3
    image_size: Tuple[int, int]          # (w, h) the profile was measured at
    rms_px: float = 0.0
    views: int = 0
    name: str = ""

    # ------------------------------------------------------------------ correction

    def scaled_to(self, image_size: Tuple[int, int]) -> "LensModel":
        """Same lens at another resolution (the pipeline works at 640x480; phones may record larger)."""
        if tuple(image_size) == tuple(self.image_size):
            return self
        sx, sy = image_size[0] / self.image_size[0], image_size[1] / self.image_size[1]
        K = self.camera_matrix.copy()
        K[0, :] *= sx
        K[1, :] *= sy
        return replace(self, camera_matrix=K, image_size=tuple(image_size))

    def undistort_points(self, pts) -> np.ndarray:
        """Distorted pixel positions -> where an ideal (distortion-free) camera with the same K sees them."""
        p = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
        out = cv2.undistortPoints(p, self.camera_matrix, self.dist_coeffs, P=self.camera_matrix)
        return out.reshape(-1, 2)

    def edge_shift_px(self) -> float:
        """How far the image corners move when corrected: a quick 'how strong is this lens' number."""
        w, h = self.image_size
        c = np.array([[0, 0], [w, 0], [w, h], [0, h]], float)
        return float(np.linalg.norm(self.undistort_points(c) - c, axis=1).max())

    # ------------------------------------------------------------------ files

    def to_dict(self) -> dict:
        return {"name": self.name, "image_size": list(self.image_size), "rms_px": round(self.rms_px, 4),
                "views": self.views, "camera_matrix": self.camera_matrix.round(6).tolist(),
                "dist_coeffs": np.asarray(self.dist_coeffs).ravel().round(8).tolist()}

    def save(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "LensModel":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(camera_matrix=np.array(d["camera_matrix"], float), dist_coeffs=np.array(d["dist_coeffs"], float),
                   image_size=tuple(d["image_size"]), rms_px=float(d.get("rms_px", 0.0)),
                   views=int(d.get("views", 0)), name=d.get("name", os.path.basename(path)))


def calibrate_views(views: List[Tuple[np.ndarray, np.ndarray]], image_size: Tuple[int, int],
                    name: str = "") -> LensModel:
    """Lens profile from several board views [(object pts, image pts), ...]."""
    if len(views) < 5:
        raise ValueError(f"need at least 5 usable board views, got {len(views)}")
    flags = cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3     # robust with ~15 hand-held views
    rms, K, dist, _, _ = cv2.calibrateCamera([v[0] for v in views], [v[1] for v in views],
                                             tuple(image_size), None, None, flags=flags)
    return LensModel(camera_matrix=K, dist_coeffs=dist.ravel(), image_size=tuple(image_size),
                     rms_px=float(rms), views=len(views), name=name)


def undistort_markers(markers: Dict[int, "object"], lens: Optional[LensModel]) -> Dict[int, "object"]:
    """Returns the frame's marker detections with lens-corrected corners, centre and heading."""
    if lens is None or not markers:
        return markers
    out = {}
    for mid, m in markers.items():
        c = lens.undistort_points(np.asarray(m.corners, dtype=np.float64).reshape(4, 2))
        top, bot = (c[0] + c[1]) / 2.0, (c[3] + c[2]) / 2.0
        a = math.atan2(top[1] - bot[1], top[0] - bot[0])
        out[mid] = replace(m, corners=c, center_x=float(c[:, 0].mean()), center_y=float(c[:, 1].mean()),
                           angle_rad=a, angle_deg=math.degrees(a))
    return out
