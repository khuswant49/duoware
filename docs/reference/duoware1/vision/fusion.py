"""
DUO-WARE Multi-Camera Fusion

Combines per-camera marker detections (image px) into one world-frame (mm) FrameTelemetry,
the same structure the single-camera pipeline produces, so the navigator, 4-stage mission,
dispatcher and recorder work unchanged.

Per marker:
  - Every usable camera's detection is mapped to the floor (with parallax correction for the
    car marker) and weighted: clean ArUco > blur-tracked, image centre > image edge, good
    calibration > weak, plus a small bonus for the camera that was primary last frame
    (smooth handoff in overlap zones).
  - Only detections from the same moment are combined (sync window): phones deliver frames with
    different delays, and averaging an old and a new position of a moving car would blur it.
  - Detections that disagree with the best one by more than conflict_mm are left out and the
    marker is flagged (localization-confidence signal).

Frames older than stale_sec are ignored: a frozen camera must not keep reporting a car position.
"""

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import numpy as np

from vision.detector import FrameTelemetry, MarkerTelemetry
from vision.world import CameraCalibration, WorldConfig, heading_from_corners, square_corners


@dataclass
class CameraObservation:
    cam_id: int
    timestamp: float
    markers: Dict[int, MarkerTelemetry]     # Image px
    calibration: CameraCalibration
    image_size: Tuple[int, int] = (640, 480)


@dataclass
class _WorldDet:
    cam_id: int
    corners: np.ndarray
    center: np.ndarray
    heading: float
    weight: float
    tracked: bool
    timestamp: float


class MultiCameraFusion:
    def __init__(self, world: WorldConfig, robot_id: int = 1, target_id: int = 2,
                 stale_sec: float = 0.25, sync_window_sec: float = 0.06, conflict_mm: float = 60.0):
        self.world = world
        self.robot_id = robot_id
        self.target_id = target_id
        self.stale_sec = stale_sec
        self.sync_window_sec = sync_window_sec
        self.conflict_mm = conflict_mm
        self._last_primary: Dict[int, int] = {}

    def _weight(self, m: MarkerTelemetry, obs: CameraObservation, marker_id: int) -> float:
        w = 1.0 if not m.tracked else 0.35 * max(0.1, float(m.confidence))
        iw, ih = obs.image_size
        dx = (m.center_x - iw / 2.0) / (iw / 2.0)
        dy = (m.center_y - ih / 2.0) / (ih / 2.0)
        w *= 1.0 / (1.0 + 1.5 * (dx * dx + dy * dy))          # Edge of view: more lens distortion/parallax
        cal = obs.calibration
        w *= 0.5 if cal.status.value == "WEAK" else 1.0
        w *= 1.0 / (1.0 + (cal.rms_mm or 0.0) / 10.0)
        if self._last_primary.get(marker_id) == obs.cam_id:
            w *= 1.3                                            # Handoff hysteresis
        return w

    def fuse(self, observations: List[CameraObservation], now: Optional[float] = None,
             manual_target: Optional[Tuple[float, float]] = None) -> Tuple[FrameTelemetry, Dict[str, Any]]:
        now = time.time() if now is None else now
        status: Dict[str, Any] = {"cameras_used": [], "stale": [], "uncalibrated": [], "misaligned": [],
                                  "unconfirmed": [], "robot": None, "conflicts": []}
        dets: Dict[int, List[_WorldDet]] = {}
        newest_ts = 0.0

        for obs in observations:
            cal = obs.calibration
            if cal.H is None:
                status["uncalibrated"].append(obs.cam_id)
                continue
            if not cal.usable:
                # RESTORED = fit from the previous run, waiting for the visible tags to confirm it
                status["unconfirmed" if cal.status.value == "RESTORED" else "misaligned"].append(obs.cam_id)
                continue
            if now - obs.timestamp > self.stale_sec:
                status["stale"].append(obs.cam_id)
                continue
            status["cameras_used"].append(obs.cam_id)
            newest_ts = max(newest_ts, obs.timestamp)
            for mid, m in obs.markers.items():
                if mid in self.world.anchors:
                    continue
                corners = cal.to_world(np.asarray(m.corners, dtype=np.float64).reshape(4, 2))
                if mid == self.robot_id and self.world.car_marker_height_mm:
                    corners = cal.correct_height(corners, self.world.car_marker_height_mm)
                dets.setdefault(mid, []).append(_WorldDet(
                    cam_id=obs.cam_id, corners=corners, center=corners.mean(axis=0),
                    heading=heading_from_corners(corners), weight=self._weight(m, obs, mid),
                    tracked=bool(m.tracked), timestamp=obs.timestamp))

        tel = FrameTelemetry(timestamp=newest_ts or now)
        for mid, group in dets.items():
            fused, info = self._fuse_marker(mid, group)
            tel.all_markers[mid] = fused
            if info["conflict"]:
                status["conflicts"].append(mid)
            if mid == self.robot_id:
                info["age_sec"] = round(now - fused.timestamp, 3)
                status["robot"] = info
        # Forget the primary camera of markers nobody sees any more
        for mid in list(self._last_primary):
            if mid not in dets:
                del self._last_primary[mid]

        tel.robot = tel.all_markers.get(self.robot_id)
        tel.target = tel.all_markers.get(self.target_id)
        if manual_target is not None:
            tx, ty = manual_target
            tel.target = MarkerTelemetry(marker_id=999, center_x=float(tx), center_y=float(ty),
                                         angle_deg=0.0, angle_rad=0.0,
                                         corners=square_corners(tx, ty, 60.0, -90.0), timestamp=tel.timestamp)
        if tel.robot is not None and tel.target is not None:
            dx = tel.target.center_x - tel.robot.center_x
            dy = tel.target.center_y - tel.robot.center_y
            tel.distance_to_target = math.hypot(dx, dy)
            tel.target_heading_deg = math.degrees(math.atan2(dy, dx))
            err = math.radians(tel.target_heading_deg) - tel.robot.angle_rad
            tel.heading_error_deg = math.degrees(math.atan2(math.sin(err), math.cos(err)))
        return tel, status

    def _fuse_marker(self, mid: int, group: List[_WorldDet]) -> Tuple[MarkerTelemetry, Dict[str, Any]]:
        latest = max(d.timestamp for d in group)
        recent = [d for d in group if d.timestamp >= latest - self.sync_window_sec]
        primary = max(recent, key=lambda d: d.weight)
        agree = [d for d in recent if np.linalg.norm(d.center - primary.center) <= self.conflict_mm]
        spread = max(float(np.linalg.norm(d.center - primary.center)) for d in recent)

        wsum = sum(d.weight for d in agree)
        center = sum(d.weight * d.center for d in agree) / wsum
        sx = sum(d.weight * math.sin(math.radians(d.heading)) for d in agree)
        cx = sum(d.weight * math.cos(math.radians(d.heading)) for d in agree)
        heading = math.degrees(math.atan2(sx, cx))
        self._last_primary[mid] = primary.cam_id

        fused = MarkerTelemetry(
            marker_id=mid, center_x=float(center[0]), center_y=float(center[1]),
            angle_deg=heading, angle_rad=math.radians(heading),
            corners=primary.corners - primary.center + center,
            timestamp=primary.timestamp,
            confidence=min(1.0, wsum),
            tracked=all(d.tracked for d in agree))
        info = {
            "seen_by": sorted(d.cam_id for d in group),
            "used": sorted(d.cam_id for d in agree),
            "primary": primary.cam_id,
            "disagreement_mm": round(spread, 1),
            "conflict": len(agree) < len(recent),
            "tracked": fused.tracked,
        }
        return fused, info
