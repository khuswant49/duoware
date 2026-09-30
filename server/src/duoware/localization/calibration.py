"""Image -> floor mapping for one camera, fitted from floor tags (PROTOCOL.md §7.3, DECISIONS.md D13, D28).

Ported from DUO-WARE 1 vision/world.py `CameraCalibration` with these changes: floor tags come from
`FloorTags` (registry roles, automatic location), there are no saved fits, and a floor tag that stops lining
up is handled tag by tag (D28): a tag that RANSAC rejects, or that alone drifts from a locked fit, has moved, so
it is un-placed and located again while the camera stays calibrated; when most tags disagree the camera was
bumped and is `MISALIGNED` until it refits.

  1 visible placed tag : similarity   (a homography from one small tag extrapolates badly)
  2 tags               : affine
  3 or more            : homography with RANSAC
"""

import logging
import math
from collections import deque
from collections.abc import Callable
from enum import Enum

import cv2
import numpy as np

from duoware.localization.floor_tags import FloorTags
from duoware.localization.geometry import heading_from_corners, side_length_mm, to_floor
from duoware.localization.refine import refine_homography
from duoware.settings import LocalizationCfg

log = logging.getLogger(__name__)

Emit = Callable[[str, object, object, dict, str], None]       # (key, value, prev, facts, reason) -> camera event
CORNERS = 4
MIN_TAGS_SIMILARITY, MIN_TAGS_HOMOGRAPHY = 1, 3               # DUO-WARE 1: 1 -> similarity, 2 -> affine, >=3 -> homography
MIN_TAGS_OK = 2                                               # fewer used tags = WEAK
RANSAC_MAX_ITERS = 2000


class CalibStatus(str, Enum):
    UNCALIBRATED = "UNCALIBRATED"
    WEAK = "WEAK"                     # fitted from a single tag: fine near it, less accurate far away
    OK = "OK"
    MISALIGNED = "MISALIGNED"         # tags no longer line up with the locked fit (camera moved, or resolution changed)


class CameraCalibration:
    def __init__(self, cam: int, floor: FloorTags, cfg: LocalizationCfg, size_warn_fraction: float,
                 image_size: tuple[int, int], emit: Emit | None = None) -> None:
        self.cam, self.floor, self.cfg = cam, floor, cfg
        self.size_warn_fraction = size_warn_fraction
        self.image_size = image_size
        self._emit = emit or (lambda *a: None)
        self.h: np.ndarray | None = None              # image -> floor
        self.h_inv: np.ndarray | None = None
        self.rms_mm: float | None = None
        self.residual_mm: float | None = None
        self.tags_used: list[int] = []
        self.tags_seen: list[int] = []
        self.status = CalibStatus.UNCALIBRATED
        self.model = ""
        self.fit_version = 0                          # bumped on every successful fit
        self.fits = 0
        self._floor_version = floor.version
        self._drift_count = 0
        self._force = False
        self._want_fit = False
        self._fit_reason = ""
        self._buf: deque = deque(maxlen=max(1, cfg.settle_frames))
        self._placing: dict[int, list[tuple[float, float, float, float]]] = {}
        self.size_warn: dict[int, bool] = {}          # floor tag -> measured size differs from printed

    # ------------------------------------------------------------------------------ public

    @property
    def usable(self) -> bool:
        return self.h is not None and self.status in (CalibStatus.OK, CalibStatus.WEAK)

    def request_recalibration(self) -> None:
        self._force = True

    def _set_status(self, status: CalibStatus, reason: str, **facts) -> None:
        if status != self.status:
            self._emit("calibration", status.value, self.status.value, {"cam": self.cam, **facts}, reason)
            self.status = status

    def reset(self, reason: str) -> None:
        """Drops the fit (floor tags changed, or an operator asked): it is refitted from the visible tags."""
        self.h = self.h_inv = None
        self.rms_mm = self.residual_mm = None
        self.tags_used = []
        self.model = ""
        self._buf.clear()
        self._placing.clear()
        self._want_fit = False
        self._force = False
        self._drift_count = 0
        self._floor_version = self.floor.version
        self._set_status(CalibStatus.UNCALIBRATED, reason)

    def on_resolution(self, w: int, h: int) -> None:
        """PROTOCOL.md §2 / D34: scale the fit by the pixel ratio and mark it MISALIGNED until the floor tags
        confirm it; a different aspect ratio drops the fit."""
        ow, oh = self.image_size
        if (w, h) == (ow, oh):
            return
        self._buf.clear()
        if self.h is None:
            self.image_size = (w, h)
            return
        if abs(w / h - ow / oh) > 1e-3 * (ow / oh):
            self.image_size = (w, h)
            self.reset(f"resolution changed to {w}x{h} with another aspect ratio")
            return
        s = np.diag([w / ow, h / oh, 1.0])
        self.h = self.h @ np.linalg.inv(s)
        self.h_inv = np.linalg.inv(self.h)
        self.image_size = (w, h)
        self._set_status(CalibStatus.MISALIGNED, f"resolution changed to {w}x{h}; checking the fit against the floor tags")

    def to_world(self, pts_img) -> np.ndarray:
        return to_floor(self.h, pts_img)

    def to_image(self, pts_world) -> np.ndarray:
        return to_floor(self.h_inv, pts_world)

    def nadir(self) -> np.ndarray | None:
        """The floor point straight below the lens, taken as the image centre mapped through the fit."""
        if self.h is None:
            return None
        w, h = self.image_size
        return self.to_world(np.array([[w / 2.0, h / 2.0]]))[0]

    def footprint(self) -> np.ndarray | None:
        if self.h is None:
            return None
        w, h = self.image_size
        return self.to_world(np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64))

    def info(self) -> dict:
        return {"status": self.status.value, "model": self.model or None,
                "rms_mm": None if self.rms_mm is None else round(self.rms_mm, 1),
                "residual_mm": None if self.residual_mm is None else round(self.residual_mm, 1),
                "floor_tags_used": list(self.tags_used), "floor_tags_seen": list(self.tags_seen)}

    # ------------------------------------------------------------------------------ one full frame

    def observe(self, markers: dict[int, np.ndarray]) -> None:
        """Floor-tag detections of one FULL frame: tag id -> (4, 2) image corners."""
        if self.floor.version != self._floor_version:
            self.reset("floor tags changed")
        visible = [i for i in markers if self.floor.is_floor(i)]
        placed_all = self.floor.placed()
        placed = sorted(i for i in visible if i in placed_all)
        self.tags_seen = placed
        if self.usable:
            self._measure_unplaced([i for i in visible if i not in placed_all], markers)
        if not placed:
            self.residual_mm = None
            self._buf.clear()
            return
        img = np.concatenate([np.asarray(markers[i], dtype=np.float64).reshape(4, 2) for i in placed])
        wld = np.concatenate([placed_all[i].world_corners() for i in placed])
        key = tuple(placed)
        if self._buf and (self._buf[-1][0] != key or np.abs(img - self._buf[-1][1]).mean() > self.cfg.move_tol_px):
            self._buf.clear()
        self._buf.append((key, img))

        if self.h is None or self._force:
            self._want_fit, self._fit_reason = True, "initial" if self.h is None else "requested"
        else:
            self._check_locked_fit(placed, markers, img, wld)
        if self._want_fit and len(self._buf) >= self.cfg.settle_frames:
            avg = np.mean([b[1] for b in self._buf], axis=0)
            if self._fit(avg, wld, placed):
                self._want_fit = False

    # ------------------------------------------------------------------------------ locked fit: drift and moved tags

    def _tag_residual(self, tag_id: int, corners: np.ndarray) -> float:
        proj = to_floor(self.h, corners)
        return float(np.sqrt(np.mean(np.sum((proj - self.floor.tags[tag_id].world_corners()) ** 2, axis=1))))

    def _check_locked_fit(self, placed: list[int], markers: dict, img: np.ndarray, wld: np.ndarray) -> None:
        tol = max(self.cfg.drift_tol_mm, 3.0 * (self.rms_mm or 0.0))
        res = {i: self._tag_residual(i, np.asarray(markers[i], dtype=np.float64).reshape(4, 2)) for i in placed}
        bad = [i for i, r in res.items() if r > tol]
        good = [i for i in placed if i not in bad]
        self.residual_mm = float(np.mean([res[i] for i in (good or placed)]))
        if bad and len(bad) * 2 <= len(placed) and all(self.floor.tags[i].source == "auto" for i in bad):
            for i in bad:                               # a few tags moved, the camera did not (D28)
                if self.floor.unplace(i, "moved"):
                    self._emit("floor_tag_moved", i, None, {"cam": self.cam, "residual_mm": round(res[i], 1)},
                               f"floor tag {i} is {res[i]:.0f} mm off the locked fit while the others agree: it moved, locating it again")
            self._drift_count = 0
            self._restore_status()
        elif bad:
            self._drift_count += 1
            self._set_status(CalibStatus.MISALIGNED, f"floor tags are off by {self.residual_mm:.0f} mm: the camera moved?",
                             residual_mm=round(self.residual_mm, 1), tags=bad)
            if self._drift_count >= self.cfg.drift_frames and not self._want_fit:
                self._want_fit, self._fit_reason = True, "drift"
        else:
            self._drift_count = 0
            self._restore_status()
            if self.status == CalibStatus.WEAK and len(placed) >= MIN_TAGS_OK and not self._want_fit:
                self._want_fit, self._fit_reason = True, "more tags"      # a single-tag fit is upgraded

    def _restore_status(self) -> None:
        if self.status == CalibStatus.MISALIGNED:
            self._set_status(CalibStatus.OK if len(self.tags_used) >= MIN_TAGS_OK else CalibStatus.WEAK,
                             "the floor tags line up with the fit again")

    # ------------------------------------------------------------------------------ automatic location

    def _measure_unplaced(self, ids: list[int], markers: dict[int, np.ndarray]) -> None:
        """Measures unplaced floor tags through this calibrated camera and places them once steady."""
        for tid in list(self._placing):
            if tid not in ids:
                del self._placing[tid]
        for tid in ids:
            c = self.to_world(np.asarray(markers[tid], dtype=np.float64).reshape(4, 2))
            cx, cy = c.mean(axis=0)
            est = self._placing.setdefault(tid, [])
            if est and math.hypot(cx - est[-1][0], cy - est[-1][1]) > self.cfg.place_move_tol_mm:
                est.clear()                              # the tag is moving: start again
            est.append((cx, cy, heading_from_corners(c), side_length_mm(c)))
            if len(est) >= self.cfg.place_frames:
                e = np.array(est)
                yaw = math.degrees(math.atan2(np.sin(np.radians(e[:, 2])).mean(), np.cos(np.radians(e[:, 2])).mean()))
                printed = self.floor.tags[tid].size_mm
                self.size_warn[tid] = abs(e[:, 3].mean() - printed) > self.size_warn_fraction * printed
                if self.size_warn[tid]:
                    log.warning("cam %d: tag %d measures %.0f mm but is set to %.0f mm", self.cam, tid, e[:, 3].mean(), printed)
                if self.floor.place(tid, float(e[:, 0].mean()), float(e[:, 1].mean()), yaw):
                    self._emit("floor_tag_located", tid, None, {"cam": self.cam, "x_mm": round(float(e[:, 0].mean()), 1),
                                                                "y_mm": round(float(e[:, 1].mean()), 1), "yaw_deg": round(yaw, 1)},
                               f"floor tag {tid} located from tags {self.tags_used}")
                del self._placing[tid]

    # ------------------------------------------------------------------------------ fitting

    @staticmethod
    def _rms(h: np.ndarray, img: np.ndarray, wld: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.sum((to_floor(h, img) - wld) ** 2, axis=1))))

    def _fit(self, img: np.ndarray, wld: np.ndarray, seen: list[int]) -> bool:
        thr = self.cfg.ransac_threshold_mm
        if len(seen) < MIN_TAGS_HOMOGRAPHY - 1:
            s, mask = cv2.estimateAffinePartial2D(img, wld, method=cv2.RANSAC, ransacReprojThreshold=thr)
            h = None if s is None else np.vstack([s, [0.0, 0.0, 1.0]])
            model = "similarity"
        elif len(seen) == MIN_TAGS_HOMOGRAPHY - 1:
            a, mask = cv2.estimateAffine2D(img, wld, method=cv2.RANSAC, ransacReprojThreshold=thr)
            h = None if a is None else np.vstack([a, [0.0, 0.0, 1.0]])
            model = "affine"
        else:
            h, mask = cv2.findHomography(img, wld, cv2.RANSAC, thr, maxIters=RANSAC_MAX_ITERS)
            model = "homography"
        if h is None:
            return False
        inl = mask.ravel().astype(bool) if mask is not None else np.ones(len(img), bool)
        rms = self._rms(h, img[inl], wld[inl])
        if rms > self.cfg.max_fit_rms_mm:
            log.warning("cam %d: calibration rejected: %.0f mm rms", self.cam, rms)
            return False
        ok_tags = inl.reshape(-1, CORNERS).all(axis=1)
        used = [t for t, ok in zip(seen, ok_tags) if ok]
        for t, ok in zip(seen, ok_tags):
            if not ok and self.floor.unplace(t, "rejected by the fit"):            # D28: moved, the fit is kept
                self._emit("floor_tag_moved", t, None, {"cam": self.cam},
                           f"floor tag {t} was rejected by the floor fit while the others agree: locating it again")
        self.h, self.h_inv = h, np.linalg.inv(h)
        self.rms_mm = rms
        self.residual_mm = rms
        self.tags_used = used
        self.model = model
        self.fit_version += 1
        self.fits += 1
        self._drift_count = 0
        self._force = False
        self._set_status(CalibStatus.OK if len(used) >= MIN_TAGS_OK else CalibStatus.WEAK,
                         f"fitted from floor tags {used} ({model}, {rms:.1f} mm rms, reason: {self._fit_reason})",
                         rms_mm=round(rms, 1), model=model, tags=used)
        if self.status == CalibStatus.OK and model == "homography":
            self._refine(used)
        return True

    def _refine(self, used: list[int]) -> None:
        """Metric refinement: the homography fitted to automatically located positions inherits their errors, so
        adjust the mapping and those positions together until every tag image is a square of its printed size."""
        key = self._buf[-1][0]
        avg = np.mean([b[1] for b in self._buf], axis=0)
        imgs = {t: avg[key.index(t) * CORNERS:(key.index(t) + 1) * CORNERS] for t in key if t in used}
        if len(imgs) < MIN_TAGS_HOMOGRAPHY:
            return
        result = refine_homography(self.h, imgs, self.floor.placed())
        if result is None:
            return
        h, poses = result
        self.h, self.h_inv = h, np.linalg.inv(h)
        self.floor.refine({t: p for t, p in poses.items() if self.floor.tags[t].source == "auto"})
        ids = list(imgs)
        self.rms_mm = self._rms(h, np.concatenate([imgs[t] for t in ids]),
                                np.concatenate([self.floor.tags[t].world_corners() for t in ids]))
        self.residual_mm = self.rms_mm
        self.model = "homography+metric"
