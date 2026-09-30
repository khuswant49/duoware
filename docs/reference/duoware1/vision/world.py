"""
DUO-WARE World Frame & Per-Camera Floor Calibration (multi-camera mode)

World frame: millimetres on the floor, seen from above. +x points right, +y points down the
map (towards the viewer), angles are degrees measured clockwise from +x. This is the same
handedness as a camera image, so the navigator's sign convention (positive heading error =
target to the RIGHT = positive gyro yaw) is unchanged.

Calibration: fixed "anchor" ArUco markers (IDs 10-49 recommended) are taped to the floor at
measured positions (calibration/world.json, or one shared tag via run.py --anchor ID:SIZE).
Each anchor gives 4 exact corner correspondences, so a camera that sees an anchor can compute
its image -> floor mapping by itself:
  - 1 anchor:   similarity (position, rotation, scale). A full homography from the 4 corners of
                one small tag extrapolates very badly (tested: ~10 cm mean error, metres with
                pixel noise); a similarity stays at a few cm for a phone pointing straight down.
  - 2 anchors:  affine (tested best for two tags lying close together: ~1-2 cm mean)
  - 3+ anchors: full homography with RANSAC, so one misplaced anchor is rejected

Unplaced anchors: tags whose position nobody measured (e.g. run.py --anchor 4:62 --anchor 2:62,
any direction). The first tag defines the map; a calibrated camera that sees an unplaced tag
measures its position and direction (averaged over steady frames) and places it, after which it
is used for calibration like any other anchor.
Anchor corners are averaged over settle_frames steady frames before fitting (less pixel noise).
Two phones that both see the SAME tag in their overlap are calibrated into one shared frame.
The fit is locked and then re-checked on every frame: if the anchors stop lining up (phone
bumped or slipped), the camera is marked MISALIGNED (fusion ignores it) and is refitted once
the misalignment persists.

Parallax: the homography maps the FLOOR plane. The car marker sits car_marker_height_mm above
the floor, so it appears shifted away from the point below the camera. With the camera height
known, marker points are pulled back towards the camera nadir by (H - h) / H.
"""

import json
import math
import os
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("duo_ware.world")

ANCHOR_ID_MIN = 10
ANCHOR_ID_MAX = 49


@dataclass
class Anchor:
    """A fixed floor marker. size = side of the black square in mm."""
    id: int
    x: float
    y: float
    size: float = 100.0
    yaw: float = -90.0   # Direction the marker's top edge faces; -90 = towards -y (up on the map)
    placed: bool = True  # False = position not known yet; measured automatically by the cameras
    confirmed: bool = True  # False = position restored from a previous run: re-check before relying on it

    def world_corners(self) -> np.ndarray:
        """Corners in ArUco order (TL, TR, BR, BL) as seen from above, shape (4, 2)."""
        return square_corners(self.x, self.y, self.size, self.yaw)


def square_corners(cx: float, cy: float, size: float, yaw_deg: float) -> np.ndarray:
    """Corners (TL, TR, BR, BL) of a square marker whose top edge faces yaw_deg (y-down frame)."""
    u = np.array([math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))])
    r = np.array([-u[1], u[0]])  # 90 deg clockwise from u
    h = size / 2.0
    c = np.array([cx, cy], dtype=np.float64)
    return np.array([c + h * (-r + u), c + h * (r + u), c + h * (r - u), c + h * (-r - u)])


def heading_from_corners(corners: np.ndarray) -> float:
    """Marker heading (deg): vector from the bottom-edge centre to the top-edge centre."""
    top = (corners[0] + corners[1]) / 2.0
    bot = (corners[3] + corners[2]) / 2.0
    return math.degrees(math.atan2(top[1] - bot[1], top[0] - bot[0]))


@dataclass
class CameraMount:
    height_mm: Optional[float] = None               # Lens height above the floor
    nadir: Optional[Tuple[float, float]] = None     # Floor point straight below the lens (default: image centre)


@dataclass
class WorldConfig:
    anchors: Dict[int, Anchor] = field(default_factory=dict)
    floor: Optional[Tuple[float, float, float, float]] = None   # x0, y0, x1, y1 (mm)
    car_marker_height_mm: float = 0.0
    default_camera_height_mm: Optional[float] = None
    cameras: Dict[int, CameraMount] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "WorldConfig":
        anchors: Dict[int, Anchor] = {}
        for a in data.get("anchors", []):
            placed = bool(a.get("placed", "x" in a and "y" in a))
            anchor = Anchor(id=int(a["id"]), x=float(a.get("x", 0.0)), y=float(a.get("y", 0.0)),
                            size=float(a.get("size", 100.0)), yaw=float(a.get("yaw", -90.0)), placed=placed)
            if not (0 <= anchor.id <= ANCHOR_ID_MAX):
                raise ValueError(f"Anchor ID {anchor.id} is not a DICT_4X4_50 marker (0-{ANCHOR_ID_MAX})")
            if anchor.id < ANCHOR_ID_MIN:
                logger.warning(f"[WORLD] Anchor ID {anchor.id} is a car/station ID: it now marks the floor "
                               f"and cannot also be used as a station")
            anchors[anchor.id] = anchor
        cams = {}
        for cid, c in (data.get("cameras") or {}).items():
            nadir = c.get("nadir_mm")
            cams[int(cid)] = CameraMount(height_mm=c.get("height_mm"),
                                         nadir=(float(nadir[0]), float(nadir[1])) if nadir else None)
        floor = data.get("floor_mm")
        return cls(anchors=anchors,
                   floor=tuple(float(v) for v in floor) if floor else None,
                   car_marker_height_mm=float(data.get("car_marker_height_mm", 0.0)),
                   default_camera_height_mm=data.get("camera_height_mm"),
                   cameras=cams)

    @classmethod
    def load(cls, path: str) -> "WorldConfig":
        if not os.path.exists(path):
            logger.warning(f"[WORLD] {path} not found - cameras cannot calibrate until anchors are defined "
                           f"(see calibration/world.example.json)")
            return cls()
        with open(path, encoding="utf-8") as f:
            world = cls.from_dict(json.load(f))
        logger.info(f"[WORLD] Loaded {len(world.anchors)} floor anchors from {path}")
        return world

    def __post_init__(self):
        self._lock = threading.Lock()
        self.version = 0          # Bumped whenever an anchor is placed, confirmed or reset (for saving)
        # Fingerprint of the tag setup as configured (ids, sizes, operator-given positions); positions
        # found automatically later do not change it. Saved state from another setup is not reused.
        self._spec = sorted([a.id, round(a.size, 1)] + ([round(a.x, 1), round(a.y, 1), round(a.yaw, 1)] if a.placed else [])
                            for a in self.anchors.values())

    def anchor_spec(self) -> list:
        return [list(x) for x in self._spec]

    def confirm_anchor(self, anchor_id: int):
        with self._lock:
            a = self.anchors.get(anchor_id)
            if a is None or a.confirmed:
                return
            a.confirmed = True
            self.version += 1
        logger.info(f"[WORLD] Tag {anchor_id}: restored position confirmed")

    def unplace_anchor(self, anchor_id: int, reason: str = ""):
        """Forget a (restored) position: the tag will be measured again by the cameras."""
        with self._lock:
            a = self.anchors.get(anchor_id)
            if a is None or not a.placed:
                return
            a.placed, a.confirmed = False, True
            self.version += 1
        logger.warning(f"[WORLD] Tag {anchor_id}: saved position no longer matches{' (' + reason + ')' if reason else ''} "
                       f"- measuring it again")

    def placed_anchors(self) -> Dict[int, Anchor]:
        return {k: a for k, a in self.anchors.items() if a.placed}

    def place_anchor(self, anchor_id: int, x: float, y: float, yaw: float) -> bool:
        """Fixes the position of an unplaced tag (first camera to measure it wins)."""
        with self._lock:
            a = self.anchors.get(anchor_id)
            if a is None or a.placed:
                return False
            a.x, a.y, a.yaw, a.placed, a.confirmed = float(x), float(y), float(yaw), True, True
            self.version += 1
        logger.info(f"[WORLD] Tag {anchor_id} placed automatically at ({x:.0f}, {y:.0f}) mm, facing {yaw:.0f} deg")
        return True

    def mount(self, cam_id: int) -> CameraMount:
        m = self.cameras.get(cam_id, CameraMount())
        if m.height_mm is None and self.default_camera_height_mm:
            m = CameraMount(height_mm=float(self.default_camera_height_mm), nadir=m.nadir)
        return m

    def bounds(self, margin: float = 300.0) -> Tuple[float, float, float, float]:
        if self.floor:
            return self.floor
        placed = self.placed_anchors()
        if placed:
            pts = np.concatenate([a.world_corners() for a in placed.values()])
            return (float(pts[:, 0].min() - margin), float(pts[:, 1].min() - margin),
                    float(pts[:, 0].max() + margin), float(pts[:, 1].max() + margin))
        return (0.0, 0.0, 2000.0, 1500.0)


class CalibStatus(str, Enum):
    UNCALIBRATED = "UNCALIBRATED"   # No anchor seen yet
    WEAK = "WEAK"                   # Fitted from a single anchor: fine near it, less accurate far away
    OK = "OK"
    MISALIGNED = "MISALIGNED"       # Anchors no longer line up with the locked fit (camera moved)
    RESTORED = "RESTORED"           # Fit loaded from a previous run: NOT used until visible tags confirm it


class CameraCalibration:
    """Image (px) <-> floor (mm) homography for one camera, fitted from floor anchors."""

    def __init__(self, cam_id: int, world: WorldConfig, image_size: Tuple[int, int] = (640, 480),
                 drift_tol_mm: float = 20.0, drift_frames: int = 15, max_fit_rms_mm: float = 30.0,
                 settle_frames: int = 10, move_tol_px: float = 1.5):
        self.cam_id = cam_id
        self.world = world
        self.image_size = image_size
        self.drift_tol_mm = drift_tol_mm
        self.drift_frames = drift_frames
        self.max_fit_rms_mm = max_fit_rms_mm
        self.settle_frames = settle_frames      # Steady frames averaged before a fit
        self.move_tol_px = move_tol_px          # Anchor corners moving more than this = not steady

        self.H: Optional[np.ndarray] = None       # image -> world
        self.H_inv: Optional[np.ndarray] = None   # world -> image
        self.rms_mm: Optional[float] = None       # Fit error on the anchors used
        self.residual_mm: Optional[float] = None  # Current error of the locked fit on visible anchors
        self.anchors_used: List[int] = []
        self.anchors_seen: List[int] = []
        self.anchors_rejected: List[int] = []     # Outliers in the last fit (anchor moved / misplaced)
        self.status = CalibStatus.UNCALIBRATED
        self.fitted_at: float = 0.0
        self.recalibrations: int = 0
        self._drift_count = 0
        self._force = False
        self._want_fit = False
        self._fit_reason = ""
        self._buf: deque = deque(maxlen=max(1, settle_frames))   # (anchor ids, image corners)
        self.model = ""                            # "similarity", "affine" or "homography"
        self._placing: Dict[int, List[Tuple[float, float, float, float]]] = {}   # Unplaced tag -> estimates
        self.place_frames = 15
        # Raw image px -> lens-corrected px (set when the camera has a lens profile, vision/lens.py).
        # Detections arrive already corrected; this is for the image border / centre used below.
        self.image_to_ideal = None
        self.version = 0          # Bumped on every fit / confirm / discard (for saving)

    # ------------------------------------------------------------------ public

    @property
    def usable(self) -> bool:
        return self.H is not None and self.status in (CalibStatus.OK, CalibStatus.WEAK)

    def restore(self, H, model: str, rms_mm: float, anchors_used: List[int]):
        """Loads a fit from a previous run. It stays RESTORED (unused) until visible tags confirm it:
        the test area and tags may have changed since."""
        self.H = np.asarray(H, dtype=np.float64)
        self.H_inv = np.linalg.inv(self.H)
        self.model, self.rms_mm, self.anchors_used = model, float(rms_mm), list(anchors_used)
        self.status = CalibStatus.RESTORED

    def _anchor_residual(self, mid: int, markers) -> float:
        img = np.asarray(markers[mid].corners, dtype=np.float64).reshape(4, 2)
        return self._rms(self.H, img, self.world.anchors[mid].world_corners())

    def _check_restored(self, visible_placed: List[int], markers) -> None:
        """RESTORED camera: confirm it if the visible tags line up, otherwise throw the old fit away."""
        tol = max(self.drift_tol_mm, 3.0 * (self.rms_mm or 0.0))
        res = {mid: self._anchor_residual(mid, markers) for mid in visible_placed}
        good = [m for m, r in res.items() if r <= tol]
        bad = [m for m, r in res.items() if r > tol]
        confirmed_bad = [m for m in bad if self.world.anchors[m].confirmed]
        if good and not confirmed_bad:
            self.status = CalibStatus.OK if len(self.anchors_used) >= 2 else CalibStatus.WEAK
            self.residual_mm = float(np.mean([res[m] for m in good]))
            for m in good:
                self.world.confirm_anchor(m)
            for m in bad:                                       # Only restored tags can be here: they moved
                self.world.unplace_anchor(m, f"off by {res[m]:.0f} mm in camera {self.cam_id}")
            self.version += 1
            logger.info(f"[CAM {self.cam_id}] Restored calibration confirmed by tags {good}")
        else:
            logger.warning(f"[CAM {self.cam_id}] Restored calibration does not match the tags "
                           f"({', '.join(f'{m}: {r:.0f} mm' for m, r in res.items())}) - camera moved, re-fitting")
            self.H = self.H_inv = None
            self.rms_mm = None
            self.status = CalibStatus.UNCALIBRATED
            self.version += 1

    def _check_provisional(self, ids: List[int], markers) -> None:
        """A confirmed camera checks restored tag positions it can see."""
        tol = max(self.drift_tol_mm, 3.0 * (self.rms_mm or 0.0))
        for mid in ids:
            r = self._anchor_residual(mid, markers)
            if r <= tol:
                self.world.confirm_anchor(mid)
            else:
                self.world.unplace_anchor(mid, f"off by {r:.0f} mm in camera {self.cam_id}")

    def request_recalibration(self):
        self._force = True

    def observe(self, markers: Dict[int, "object"], now: float):
        """Feed one frame's detections (image px). Fits, checks and refits the floor mapping."""
        visible = [mid for mid, m in markers.items()
                   if mid in self.world.anchors and not getattr(m, "tracked", False)]
        placed = sorted(mid for mid in visible if self.world.anchors[mid].placed)
        if self.status == CalibStatus.RESTORED and placed:
            self._check_restored(placed, markers)
        # Only confirmed tag positions are used for fitting / drift checks
        seen = [mid for mid in placed if self.world.anchors[mid].confirmed]
        self.anchors_seen = seen
        if self.usable:
            self._check_provisional([mid for mid in placed if not self.world.anchors[mid].confirmed], markers)
            self._measure_unplaced([mid for mid in visible if not self.world.anchors[mid].placed], markers)
        if self.status == CalibStatus.RESTORED:
            return                                              # Nothing visible to confirm it with yet
        if not seen:
            self.residual_mm = None
            self._buf.clear()
            return
        img = np.concatenate([np.asarray(markers[m].corners, dtype=np.float64).reshape(4, 2) for m in seen])
        wld = np.concatenate([self.world.anchors[m].world_corners() for m in seen])

        # Steady-frame buffer: averaging the corners over several frames removes pixel noise
        key = tuple(seen)
        if self._buf and (self._buf[-1][0] != key or np.abs(img - self._buf[-1][1]).mean() > self.move_tol_px):
            self._buf.clear()
        self._buf.append((key, img))

        if self.H is None or self._force:
            self._want_fit, self._fit_reason = True, "initial" if self.H is None else "requested"
        else:
            self.residual_mm = self._rms(self.H, img, wld)
            tol = max(self.drift_tol_mm, 3.0 * (self.rms_mm or 0.0))
            if self.residual_mm > tol:
                self._drift_count += 1
                if self.status != CalibStatus.MISALIGNED:
                    logger.warning(f"[CAM {self.cam_id}] Anchors off by {self.residual_mm:.0f} mm - camera moved? "
                                   f"Ignoring this camera until it is re-fitted.")
                self.status = CalibStatus.MISALIGNED
                if self._drift_count >= self.drift_frames and not self._want_fit:
                    self._want_fit, self._fit_reason = True, "drift"
            else:
                self._drift_count = 0
                if self.status == CalibStatus.MISALIGNED:
                    self.status = CalibStatus.OK if len(self.anchors_used) >= 2 else CalibStatus.WEAK
                # A single-anchor fit is upgraded as soon as more anchors are visible
                if self.status == CalibStatus.WEAK and len(seen) >= 2 and not self._want_fit:
                    self._want_fit, self._fit_reason = True, "more anchors"
        if self._want_fit and len(self._buf) >= self.settle_frames:
            avg = np.mean([b[1] for b in self._buf], axis=0)
            if self._fit(avg, wld, seen, now):
                if self._fit_reason == "drift":
                    self.recalibrations += 1
                self._want_fit = False

    def _measure_unplaced(self, ids: List[int], markers: Dict[int, "object"]):
        """Measures unplaced tags through this camera's calibration and places them once steady."""
        for mid in list(self._placing):
            if mid not in ids:
                del self._placing[mid]
        for mid in ids:
            c = self.to_world(np.asarray(markers[mid].corners, dtype=np.float64).reshape(4, 2))
            cx, cy = c.mean(axis=0)
            side = float(np.mean([np.linalg.norm(c[i] - c[(i + 1) % 4]) for i in range(4)]))
            est = self._placing.setdefault(mid, [])
            if est and math.hypot(cx - est[-1][0], cy - est[-1][1]) > 15.0:
                est.clear()                                  # Tag moving: start again
            est.append((cx, cy, heading_from_corners(c), side))
            if len(est) >= self.place_frames:
                e = np.array(est)
                yaw = math.degrees(math.atan2(np.sin(np.radians(e[:, 2])).mean(), np.cos(np.radians(e[:, 2])).mean()))
                expected = self.world.anchors[mid].size
                if abs(e[:, 3].mean() - expected) > 0.15 * expected:
                    logger.warning(f"[CAM {self.cam_id}] Tag {mid} measures {e[:, 3].mean():.0f} mm but is set to "
                                   f"{expected:.0f} mm - are all tags the same size?")
                if self.world.place_anchor(mid, float(e[:, 0].mean()), float(e[:, 1].mean()), yaw):
                    logger.info(f"[CAM {self.cam_id}] measured tag {mid} (from anchors {self.anchors_used})")
                del self._placing[mid]

    def to_world(self, pts_img) -> np.ndarray:
        pts = np.asarray(pts_img, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H).reshape(-1, 2)

    def to_image(self, pts_world) -> np.ndarray:
        pts = np.asarray(pts_world, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H_inv).reshape(-1, 2)

    def footprint(self) -> Optional[np.ndarray]:
        """The camera's view on the floor as a world polygon (4, 2)."""
        if self.H is None:
            return None
        w, h = self.image_size
        pts = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
        return self.to_world(self.image_to_ideal(pts) if self.image_to_ideal else pts)

    def nadir(self) -> Optional[np.ndarray]:
        mount = self.world.mount(self.cam_id)
        if mount.nadir is not None:
            return np.array(mount.nadir, dtype=np.float64)
        if self.H is None:
            return None
        w, h = self.image_size
        c = np.array([[w / 2.0, h / 2.0]])
        return self.to_world(self.image_to_ideal(c) if self.image_to_ideal else c)[0]

    def correct_height(self, pts_world: np.ndarray, height_mm: float) -> np.ndarray:
        """Removes parallax for points height_mm above the floor (needs the camera height)."""
        cam_h = self.world.mount(self.cam_id).height_mm
        if not height_mm or not cam_h or height_mm >= cam_h:
            return pts_world
        n = self.nadir()
        if n is None:
            return pts_world
        return n + (np.asarray(pts_world, dtype=np.float64) - n) * ((cam_h - height_mm) / cam_h)

    def info(self) -> dict:
        fp = self.footprint()
        return {
            "status": self.status.value,
            "rms_mm": round(self.rms_mm, 1) if self.rms_mm is not None else None,
            "residual_mm": round(self.residual_mm, 1) if self.residual_mm is not None else None,
            "anchors_seen": self.anchors_seen,
            "anchors_used": self.anchors_used,
            "anchors_rejected": self.anchors_rejected,
            "recalibrations": self.recalibrations,
            "model": self.model or None,
            "settling": bool(self._want_fit),
            "placing_tags": {k: len(v) for k, v in self._placing.items()},
            "footprint_mm": fp.round(1).tolist() if fp is not None else None,
        }

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _rms(H: np.ndarray, img: np.ndarray, wld: np.ndarray) -> float:
        proj = cv2.perspectiveTransform(img.reshape(-1, 1, 2), H).reshape(-1, 2)
        return float(np.sqrt(np.mean(np.sum((proj - wld) ** 2, axis=1))))

    def _fit(self, img: np.ndarray, wld: np.ndarray, seen: List[int], now: float) -> bool:
        if len(seen) == 1:
            # One tag: similarity only (a full homography from 4 close corners extrapolates badly)
            S, mask = cv2.estimateAffinePartial2D(img, wld, method=cv2.RANSAC, ransacReprojThreshold=15.0)
            H = np.vstack([S, [0.0, 0.0, 1.0]]) if S is not None else None
            model = "similarity"
        elif len(seen) == 2:
            # Two tags (often close together): affine extrapolates far better than a homography
            A, mask = cv2.estimateAffine2D(img, wld, method=cv2.RANSAC, ransacReprojThreshold=15.0)
            H = np.vstack([A, [0.0, 0.0, 1.0]]) if A is not None else None
            model = "affine"
        elif len(seen) >= 3:
            # RANSAC needs redundancy: with 3+ anchors a moved/misplaced anchor is rejected
            H, mask = cv2.findHomography(img, wld, cv2.RANSAC, 15.0)
            model = "homography"
        if H is None:
            return False
        inl = mask.ravel().astype(bool) if mask is not None else np.ones(len(img), bool)
        rms = self._rms(H, img[inl], wld[inl])
        if rms > self.max_fit_rms_mm:
            logger.warning(f"[CAM {self.cam_id}] Calibration rejected: {rms:.0f} mm error "
                           f"(check anchor positions in world.json)")
            return False
        # An anchor counts as used if all its 4 corners were inliers
        per_anchor = inl.reshape(-1, 4).all(axis=1)
        used = [a for a, ok in zip(seen, per_anchor) if ok]
        self.H, self.H_inv = H, np.linalg.inv(H)
        self.rms_mm = rms
        self.residual_mm = rms
        self.anchors_used = used
        self.anchors_rejected = [a for a, ok in zip(seen, per_anchor) if not ok]
        self.status = CalibStatus.OK if len(used) >= 2 else CalibStatus.WEAK
        self.model = model
        self.version += 1
        self.fitted_at = now
        self._drift_count = 0
        self._force = False
        logger.info(f"[CAM {self.cam_id}] Calibrated from anchors {used} ({model}, {rms:.1f} mm rms, {self.status.value})"
                    + (f", rejected {self.anchors_rejected}" if self.anchors_rejected else ""))
        return True
