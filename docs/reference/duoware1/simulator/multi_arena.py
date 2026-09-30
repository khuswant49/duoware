"""
DUO-WARE Multi-Camera Virtual Arena

A larger virtual floor (millimetres) watched by several virtual overhead phones. Each virtual
camera looks at its own part of the floor with a slight rotation and perspective tilt, and
neighbouring views overlap. The engine is NOT told these camera poses: it has to calibrate
every camera from the floor anchor markers painted on the floor, exactly as with real phones.
This lets the whole multi-camera pipeline (detection, calibration, handoff, fusion, navigation)
be tested without hardware.

Layout: N cameras in one row (N <= 3) or in rows of ceil(N / 2) otherwise.

Realism (optional, SimRealism): by default the virtual cameras are ideal. With a SimRealism the
frames get what real phones add: motion blur (the car is averaged over the exposure time),
per-camera capture delay, sensor noise, JPEG compression and barrel lens distortion. The defaults
of SimRealism were fitted to the team's real recordings (tools/analyze_recordings.py, 25 Sep 2026);
lens distortion was NOT measured and is a stand-in value.
"""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from vision.world import Anchor, WorldConfig, square_corners


def radial_lens_maps(image_size: Tuple[int, int], k1: float):
    """Remap tables that bend an ideal image with radial distortion k1 (focal length = image width,
    centre = image centre). Shared by the virtual phones and the synthetic lens-calibration views."""
    w, h = image_size
    f = float(w)
    u, v = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    xn, yn = (u - w / 2.0) / f, (v - h / 2.0) / f
    r2 = xn * xn + yn * yn
    # Output pixel at distorted radius samples the ideal image at the undistorted radius
    scale = 1.0 / (1.0 + k1 * r2)
    return (xn * scale * f + w / 2.0).astype(np.float32), (yn * scale * f + h / 2.0).astype(np.float32)


def ideal_from_distorted(pts, image_size: Tuple[int, int], k1: float) -> np.ndarray:
    """Exact inverse used by radial_lens_maps: distorted pixel -> ideal pixel (ground truth for tests)."""
    w, h = image_size
    f = float(w)
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    xn, yn = (p[:, 0] - w / 2.0) / f, (p[:, 1] - h / 2.0) / f
    scale = 1.0 / (1.0 + k1 * (xn * xn + yn * yn))
    return np.stack([xn * scale * f + w / 2.0, yn * scale * f + h / 2.0], axis=1)


@dataclass
class SimRealism:
    exposure_s: float = 0.026         # Motion blur: car averaged over this time (fitted to recordings)
    exposure_jitter_s: float = 0.012  # Phone auto-exposure varies frame to frame: +- uniform (fitted)
    blur_samples: int = 7
    latency_s: float = 0.06           # Capture -> server delay (mean)
    latency_jitter_s: float = 0.02    # +- uniform per frame and camera
    pixel_noise_sd: float = 3.0       # Sensor noise, grey levels
    shake_px_sd: float = 0.05         # Mount vibration: random sub-pixel image shift per frame
    jpeg_quality: int = 70            # The phone page streams JPEG at quality 0.7
    lens_k1: float = -0.05            # Barrel distortion (NOT measured: stand-in value)
    seed: int = 11


def realistic_phone_arena(num_cameras: int = 2, **kw) -> "MultiCameraArena":
    """Virtual phones matching the recorded setup: a phone view ~1.1 m wide (62 mm tags ~37 px),
    27 fps-like delays, blur, noise, JPEG and some lens distortion."""
    kw.setdefault("footprint_mm", (1100.0, 825.0))
    kw.setdefault("overlap_mm", 250.0)
    kw.setdefault("marker_size_mm", 62.0)
    kw.setdefault("raster_mm_per_px", 1.0)
    kw.setdefault("anchor_spacing_mm", 450.0)
    kw.setdefault("realism", SimRealism())
    return MultiCameraArena(num_cameras=num_cameras, **kw)


@dataclass
class VirtualCamera:
    id: int
    center: Tuple[float, float]
    world_quad: np.ndarray        # Floor points seen at the image corners TL, TR, BR, BL
    H_world_to_image: np.ndarray


class MultiCameraArena:
    def __init__(self, num_cameras: int = 3, footprint_mm: Tuple[float, float] = (1600.0, 1200.0),
                 overlap_mm: float = 300.0, image_size: Tuple[int, int] = (640, 480),
                 marker_size_mm: float = 100.0, anchor_spacing_mm: float = 600.0,
                 robot_id: int = 1, object_id: int = 2, dest_id: int = 4,
                 raster_mm_per_px: float = 2.0, seed: int = 7, anchors: Optional[List[Anchor]] = None,
                 realism: Optional[SimRealism] = None):
        self.num_cameras = max(1, int(num_cameras))
        self.image_size = image_size
        self.marker_size = marker_size_mm
        self.robot_id, self.object_id, self.dest_id = robot_id, object_id, dest_id
        self.rs = 1.0 / raster_mm_per_px                   # Raster px per mm
        fw, fh = footprint_mm
        cols = self.num_cameras if self.num_cameras <= 3 else math.ceil(self.num_cameras / 2)
        rows = math.ceil(self.num_cameras / cols)
        self.floor_w = cols * fw - (cols - 1) * overlap_mm
        self.floor_h = rows * fh - (rows - 1) * overlap_mm

        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self._aruco_dict = aruco_dict
        self._bitmaps: Dict[int, np.ndarray] = {}

        # Stations (object / destination) and the car's start pose
        self.object_pos = (self.floor_w * 0.45, self.floor_h * 0.28)
        self.dest_pos = (self.floor_w - 450.0, self.floor_h * 0.62)
        self.start_pose = (400.0, self.floor_h - 350.0, -90.0)

        # Virtual cameras (deterministic small rotation + perspective jitter)
        rng = np.random.default_rng(seed)
        iw, ih = image_size
        img_corners = np.float32([[0, 0], [iw, 0], [iw, ih], [0, ih]])
        self.cameras: List[VirtualCamera] = []
        for i in range(self.num_cameras):
            r, c = divmod(i, cols)
            cx = fw / 2.0 + c * (fw - overlap_mm)
            cy = fh / 2.0 + r * (fh - overlap_mm)
            yaw = math.radians(rng.uniform(-4.0, 4.0))
            quad = []
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                lx, ly = sx * fw / 2.0, sy * fh / 2.0
                x = cx + lx * math.cos(yaw) - ly * math.sin(yaw) + rng.uniform(-35, 35)
                y = cy + lx * math.sin(yaw) + ly * math.cos(yaw) + rng.uniform(-35, 35)
                quad.append((x, y))
            quad = np.float32(quad)
            H = cv2.getPerspectiveTransform(quad, img_corners).astype(np.float64)
            self.cameras.append(VirtualCamera(i + 1, (cx, cy), quad, H))

        # Default: a grid of floor anchors; or exactly the given ones (e.g. one shared tag in the overlap)
        self.anchors = {a.id: a for a in anchors} if anchors is not None else self._layout_anchors(anchor_spacing_mm)
        self._static = self._render_static()
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_pose: Optional[Tuple[float, float, float]] = None

        # Realism state: pose history (for blur and delay), per-camera random streams, lens maps
        self.realism = realism
        self._history: deque = deque(maxlen=400)          # (t, x, y, heading)
        if realism is not None:
            self._rngs = [np.random.default_rng(realism.seed + i) for i in range(self.num_cameras)]
            self._lens_maps = self._make_lens_maps(realism.lens_k1) if realism.lens_k1 else None

    # ------------------------------------------------------------------ layout

    def _layout_anchors(self, spacing: float) -> Dict[int, Anchor]:
        keep_clear = [self.object_pos, self.dest_pos, self.start_pose[:2]]
        anchors: Dict[int, Anchor] = {}
        next_id = 10
        margin = 180.0
        nx = max(2, int(round((self.floor_w - 2 * margin) / spacing)) + 1)
        ny = max(2, int(round((self.floor_h - 2 * margin) / spacing)) + 1)
        for j in range(ny):
            for i in range(nx):
                x = margin + i * (self.floor_w - 2 * margin) / (nx - 1)
                y = margin + j * (self.floor_h - 2 * margin) / (ny - 1)
                if any(math.hypot(x - kx, y - ky) < 260.0 for kx, ky in keep_clear):
                    continue
                if next_id > 49:
                    break
                anchors[next_id] = Anchor(id=next_id, x=x, y=y, size=self.marker_size, yaw=-90.0)
                next_id += 1
        return anchors

    def world_config(self) -> WorldConfig:
        return WorldConfig(anchors=dict(self.anchors), floor=(0.0, 0.0, self.floor_w, self.floor_h),
                           car_marker_height_mm=0.0)

    def drive_bounds(self) -> Tuple[float, float, float, float]:
        return (80.0, 80.0, self.floor_w - 80.0, self.floor_h - 80.0)

    def set_pattern_ids(self, robot_id: int, object_id: int, dest_id: int):
        changed = (object_id, dest_id) != (self.object_id, self.dest_id)
        self.robot_id, self.object_id, self.dest_id = int(robot_id), int(object_id), int(dest_id)
        if changed:
            with self._lock:
                self._static = self._render_static()
                self._frame_pose = None

    # ------------------------------------------------------------------ drawing

    def _bitmap(self, marker_id: int) -> np.ndarray:
        """Marker with a white quiet zone; the black square is the inner 60/84 of the bitmap."""
        if marker_id not in self._bitmaps:
            inner = cv2.aruco.generateImageMarker(self._aruco_dict, int(marker_id), 60)
            self._bitmaps[marker_id] = cv2.copyMakeBorder(inner, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        return self._bitmaps[marker_id]

    def _paint_marker(self, raster: np.ndarray, marker_id: int, x: float, y: float, yaw: float):
        bmp = self._bitmap(marker_id)
        s = bmp.shape[0]
        padded_size = self.marker_size * s / 60.0
        dst = square_corners(x, y, padded_size, yaw) * self.rs
        x0, y0 = np.floor(dst.min(axis=0)).astype(int)
        x1, y1 = np.ceil(dst.max(axis=0)).astype(int) + 1
        h, w = raster.shape[:2]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            return
        src = np.float32([[0, 0], [s, 0], [s, s], [0, s]])
        M = cv2.getPerspectiveTransform(src, np.float32(dst - [x0, y0]))
        size = (x1 - x0, y1 - y0)
        warped = cv2.warpPerspective(bmp, M, size, flags=cv2.INTER_AREA, borderValue=0)
        mask = cv2.warpPerspective(np.full_like(bmp, 255), M, size, flags=cv2.INTER_NEAREST, borderValue=0) > 0
        roi = raster[y0:y1, x0:x1]
        roi[mask] = cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)[mask]

    def _render_static(self) -> np.ndarray:
        W, H = int(math.ceil(self.floor_w * self.rs)), int(math.ceil(self.floor_h * self.rs))
        img = np.full((H, W, 3), (92, 98, 104), np.uint8)          # Concrete-grey floor
        step = int(250 * self.rs)
        for x in range(0, W, step):
            cv2.line(img, (x, 0), (x, H), (84, 90, 96), 1)
        for y in range(0, H, step):
            cv2.line(img, (0, y), (W, y), (84, 90, 96), 1)
        for (px, py), mid, color in ((self.object_pos, self.object_id, (40, 120, 200)),
                                     (self.dest_pos, self.dest_id, (40, 160, 70))):
            half = 110.0 * self.rs
            cx, cy = px * self.rs, py * self.rs
            cv2.rectangle(img, (int(cx - half), int(cy - half)), (int(cx + half), int(cy + half)), color, -1)
            self._paint_marker(img, mid, px, py, -90.0)
        # Floor tags last: a tag lying on the floor is on top of station pads, never hidden by them
        for a in self.anchors.values():
            self._paint_marker(img, a.id, a.x, a.y, a.yaw)
        return img

    def _render_world(self, x: float, y: float, heading: float) -> np.ndarray:
        img = self._static.copy()
        body = ((x * self.rs, y * self.rs), (190 * self.rs, 150 * self.rs), heading)
        cv2.drawContours(img, [np.int32(cv2.boxPoints(body))], 0, (30, 32, 36), -1)
        self._paint_marker(img, self.robot_id, x, y, heading)
        return img

    # ------------------------------------------------------------------ realism

    def _make_lens_maps(self, k1: float):
        return radial_lens_maps(self.image_size, k1)

    def _record(self, t: float, x: float, y: float, heading: float):
        with self._lock:
            if not self._history or t > self._history[-1][0]:
                self._history.append((t, x, y, heading))

    def _pose_at(self, t: float) -> Tuple[float, float, float]:
        with self._lock:
            hist = list(self._history)
        if not hist:
            return self.start_pose
        if t <= hist[0][0]:
            return hist[0][1:]
        for (t0, x0, y0, h0), (t1, x1, y1, h1) in zip(hist, hist[1:]):
            if t0 <= t <= t1:
                a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                dh = (h1 - h0 + 180.0) % 360.0 - 180.0
                return x0 + a * (x1 - x0), y0 + a * (y1 - y0), h0 + a * dh
        return hist[-1][1:]

    def _paint_car(self, img: np.ndarray, x: float, y: float, heading: float):
        body = ((x * self.rs, y * self.rs), (190 * self.rs, 150 * self.rs), heading)
        cv2.drawContours(img, [np.int32(cv2.boxPoints(body))], 0, (30, 32, 36), -1)
        self._paint_marker(img, self.robot_id, x, y, heading)

    def _render_world_blurred(self, poses: List[Tuple[float, float, float]]) -> np.ndarray:
        """World raster with the car averaged over several poses (exposure-time motion blur)."""
        img = self._static.copy()
        if len(poses) == 1 or max(math.hypot(p[0] - poses[0][0], p[1] - poses[0][1]) for p in poses) < 0.5:
            self._paint_car(img, *poses[-1])
            return img
        m = 150.0                                            # mm around the car body
        xs, ys = [p[0] for p in poses], [p[1] for p in poses]
        h, w = img.shape[:2]
        x0, y0 = max(0, int((min(xs) - m) * self.rs)), max(0, int((min(ys) - m) * self.rs))
        x1, y1 = min(w, int((max(xs) + m) * self.rs) + 1), min(h, int((max(ys) + m) * self.rs) + 1)
        if x1 <= x0 or y1 <= y0:
            return img
        acc = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
        for (px, py, ph) in poses:
            patch = self._static[y0:y1, x0:x1].copy()
            self._paint_car(patch, px - x0 / self.rs, py - y0 / self.rs, ph)
            acc += patch
        img[y0:y1, x0:x1] = np.clip(acc / len(poses), 0, 255).astype(np.uint8)
        return img

    def _render_camera_realistic(self, index: int, t: float) -> np.ndarray:
        r = self.realism
        rng = self._rngs[index]
        t_cap = t - max(0.0, r.latency_s + rng.uniform(-r.latency_jitter_s, r.latency_jitter_s))
        exposure = max(0.0, r.exposure_s + rng.uniform(-r.exposure_jitter_s, r.exposure_jitter_s))
        n = max(1, r.blur_samples) if exposure > 0 else 1
        poses = [self._pose_at(t_cap - exposure * k / max(1, n - 1)) for k in range(n)]
        world_img = self._render_world_blurred(poses)
        cam = self.cameras[index]
        S_inv = np.diag([1.0 / self.rs, 1.0 / self.rs, 1.0])
        H = cam.H_world_to_image @ S_inv
        if r.shake_px_sd > 0:
            dx, dy = rng.normal(0.0, r.shake_px_sd, 2)
            H = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]]) @ H
        img = cv2.warpPerspective(world_img, H, self.image_size, flags=cv2.INTER_AREA, borderValue=(20, 20, 20))
        if self._lens_maps is not None:
            img = cv2.remap(img, self._lens_maps[0], self._lens_maps[1], cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(20, 20, 20))
        if r.pixel_noise_sd > 0:
            img = np.clip(img.astype(np.float32) + rng.normal(0.0, r.pixel_noise_sd, img.shape), 0, 255).astype(np.uint8)
        if r.jpeg_quality:
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(r.jpeg_quality)])
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return img

    def render_camera(self, index: int, x: float, y: float, heading: float, t: Optional[float] = None) -> np.ndarray:
        """Frame of virtual camera `index` (0-based) with the car at (x, y) mm, heading in degrees.
        With realism, t (default: now) is the moment the frame reaches the server; the picture shows
        the car as it was one capture delay earlier, blurred over the exposure time."""
        if self.realism is not None:
            t = time.time() if t is None else t
            self._record(t, x, y, heading)
            return self._render_camera_realistic(index, t)
        pose = (round(x, 1), round(y, 1), round(heading, 1))
        with self._lock:
            if self._frame is None or self._frame_pose != pose:
                self._frame = self._render_world(x, y, heading)
                self._frame_pose = pose
            world_img = self._frame
        cam = self.cameras[index]
        S_inv = np.diag([1.0 / self.rs, 1.0 / self.rs, 1.0])
        return cv2.warpPerspective(world_img, cam.H_world_to_image @ S_inv, self.image_size,
                                   flags=cv2.INTER_LINEAR, borderValue=(20, 20, 20))
