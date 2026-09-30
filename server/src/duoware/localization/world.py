"""The world model: what every camera currently sees, in floor millimetres (DECISIONS.md D1, D13, D32, D35, D36).

`WorldModel` is the `FrameSink` of the UDP endpoint. Per accepted frame it (1) rescales the camera's fit if the
resolution changed, (2) feeds the floor tags of FULL frames to the camera's calibration, (3) marks tags seen or
not seen only where the phone looked (`scan`/`searched`, PROTOCOL.md §2), and (4) turns car tags into poses:
floor position, parallax correction towards the nadir, heading, and the rotation centre via the tag offset.
No pose is produced while the camera is unsynced or uncalibrated; poses from a phone that is not in `tracking`
mode are recorded but flagged `usable_for_control = False`.
"""

import threading
from dataclasses import dataclass

import numpy as np

from duoware.clock import Clock
from duoware.ingest.sessions import Session
from duoware.localization.calibration import CameraCalibration
from duoware.localization.floor_tags import FloorTags
from duoware.localization.geometry import (
    apply_offset, heading_from_corners, parallax_correct, side_length_mm, to_floor, wrap_deg,
)
from duoware.protocol.phone import Frame, marker_capture_ns
from duoware.registry.model import CarRole
from duoware.registry.service import TagRegistry
from duoware.settings import Settings
from duoware.store.events import EventLog

NS_PER_MS = 1_000_000
TRACKING_MODE = "tracking"        # PROTOCOL.md §4.5: only `tracking` frames produce poses for control


@dataclass(frozen=True)
class TagObs:
    id: int
    seen: bool
    cams: tuple[int, ...]
    x: float | None
    y: float | None
    heading: float | None
    capture_ns: int | None
    size_mm: float | None          # measured side on the floor plane
    size_warn: bool
    last_seen_ns: int


@dataclass(frozen=True)
class CarObs:
    name: str
    tag: int
    x: float                       # rotation centre
    y: float
    heading: float
    capture_ns: int
    cam: int
    age_ms: float
    fresh: bool
    usable_for_control: bool


@dataclass(frozen=True)
class CamObs:
    cam: int
    calib: dict
    usable: bool                   # calibrated (OK/WEAK)
    usable_for_control: bool       # calibrated, synced, session live and in `tracking` mode
    app_mode: str | None
    image_size: tuple[int, int]
    footprint: list[list[float]] | None


@dataclass(frozen=True)
class WorldSnapshot:
    now_ns: int
    tags: dict[int, TagObs]
    cars: dict[str, CarObs]
    cameras: dict[int, CamObs]


class _Obs:
    """Mutable per-tag record: what each camera last saw of it."""

    def __init__(self, tag_id: int) -> None:
        self.id = tag_id
        self.cams: dict[int, int] = {}           # cam -> server time (ns) of its latest sighting
        self.x = self.y = self.heading = self.capture_ns = self.size_mm = None
        self.size_warn = False
        self.last_seen_ns = 0
        self.best_cam: int | None = None


class WorldModel:
    def __init__(self, settings: Settings, registry: TagRegistry, floor: FloorTags, clock: Clock, events: EventLog,
                 sessions=None) -> None:
        self.settings, self.registry, self.floor, self.clock, self.events = settings, registry, floor, clock, events
        self.sessions = sessions
        self._lock = threading.RLock()
        self.calibs: dict[int, CameraCalibration] = {}
        self._obs: dict[int, _Obs] = {}
        self._cars: dict[str, tuple[int, float, float, float, int, int, bool]] = {}   # name -> tag,x,y,hdg,capture,cam,usable
        self._scale: dict[str, float] = {}
        self._sessions: dict[int, Session] = {}
        self._last_frame: dict[int, int] = {}      # cam -> server time (ns) of its latest accepted frame
        self.full_frames_seen = 0                 # for tests: frames that reached the floor-tag logic

    # ------------------------------------------------------------------------------ calibration access

    def calib(self, cam: int, image_size: tuple[int, int] | None = None) -> CameraCalibration | None:
        if cam not in self.calibs and image_size is not None:
            def emit(key, value, prev, facts, reason, cam=cam):
                self.events.log("camera", key=key, value=value, prev=prev, facts=facts, reason=reason)
            self.calibs[cam] = CameraCalibration(cam, self.floor, self.settings.tuning.localization,
                                                 self.settings.tuning.markers.size_warn_fraction, image_size, emit)
        return self.calibs.get(cam)

    def recalibrate(self, cam: int) -> bool:
        """`POST /api/cameras/{cam}/recalibrate`: drop the fit; it is refitted from the visible floor tags."""
        with self._lock:
            c = self.calibs.get(cam)
            if c is None:
                return False
            c.reset("operator asked for a new calibration")
            return True

    # ------------------------------------------------------------------------------ frames

    def on_frame(self, session: Session, frame: Frame, recv_ns: int) -> None:
        with self._lock:
            self._on_frame(session, frame, recv_ns)

    def _on_frame(self, session: Session, frame: Frame, recv_ns: int) -> None:
        cam = session.cam
        self._sessions[cam] = session
        self._last_frame[cam] = recv_ns
        calib = self.calib(cam, (frame.w, frame.h))
        calib.on_resolution(frame.w, frame.h)
        snap = self.registry.snapshot()
        synced = session.clock_sync.ok(recv_ns)
        markers = {m.id: np.array(m.corners, dtype=np.float64).reshape(4, 2) for m in frame.markers}
        full = frame.scan == "full"
        if full:
            self.full_frames_seen += 1
            calib.observe({i: c for i, c in markers.items() if self.floor.is_floor(i)})
        if synced:
            cap = session.clock_sync.to_server_ns(frame.cap_ns + frame.exp_ns // 2, recv_ns)
            if cap is not None:
                session.stats.add_pose_age((recv_ns - cap) / 1e6, recv_ns)
        tracking = session.app_mode == TRACKING_MODE
        searched = set(frame.searched)
        for m in frame.markers:
            o = self._obs.setdefault(m.id, _Obs(m.id))
            o.cams[cam] = recv_ns
            o.last_seen_ns = recv_ns
            if not (calib.usable and synced):
                continue
            cap_ns = session.clock_sync.to_server_ns(marker_capture_ns(frame, m), recv_ns)
            if cap_ns is None:
                continue
            fc = to_floor(calib.h, markers[m.id])
            tag = snap.get(m.id)
            if isinstance(tag and tag.fields, CarRole):
                self._car_pose(tag, fc, calib, cam, cap_ns, tracking)
            o.x, o.y = (float(v) for v in fc.mean(axis=0))
            o.heading, o.capture_ns, o.best_cam = heading_from_corners(fc), cap_ns, cam
            o.size_mm = side_length_mm(fc)
            printed = tag.size_mm if tag else None
            o.size_warn = bool(tag and self.floor.is_floor(m.id)
                               and abs(o.size_mm - printed) > self.settings.tuning.markers.size_warn_fraction * printed)
        # Absence means "not seen" only where the phone looked (PROTOCOL.md §2, D32).
        present = set(markers)
        for tid, o in self._obs.items():
            if tid in present or cam not in o.cams:
                continue
            if full or tid in searched:
                del o.cams[cam]
        for tid in snap.tags:
            self._obs.setdefault(tid, _Obs(tid))

    def _car_pose(self, tag, fc: np.ndarray, calib: CameraCalibration, cam: int, cap_ns: int, tracking: bool) -> None:
        cfg = self.settings.tuning.localization
        f: CarRole = tag.fields
        s = side_length_mm(fc) / tag.size_mm
        if not (1.0 / cfg.parallax_max_scale <= s <= cfg.parallax_max_scale):
            return                                              # bad detection: keep the previous pose
        prev = self._scale.get(f.car)
        self._scale[f.car] = s if prev is None else prev + cfg.parallax_ema * (s - prev)
        centre = parallax_correct(fc.mean(axis=0), calib.nadir(), self._scale[f.car])
        heading = wrap_deg(heading_from_corners(fc) + (f.heading_offset_deg or 0.0))
        fwd, left = f.offset_mm or (0.0, 0.0)
        rc = apply_offset(centre, heading, (-fwd, -left))        # tag centre = rotation centre + offset
        old = self._cars.get(f.car)
        if old is None or cap_ns >= old[4] or old[5] == cam:
            self._cars[f.car] = (tag.id, float(rc[0]), float(rc[1]), heading, cap_ns, cam, tracking)

    # ------------------------------------------------------------------------------ queries

    def _cam_usable_for_control(self, cam: int, calib: CameraCalibration, now_ns: int) -> tuple[bool, str | None]:
        s = self._sessions.get(cam)
        live = s is not None and (self.sessions is None or self.sessions.by_sid(s.sid) is not None)
        mode = s.app_mode if live else None
        ok = live and calib.usable and s.clock_sync.ok(now_ns) and mode == TRACKING_MODE
        return bool(ok), mode

    def snapshot(self, now_ns: int | None = None) -> WorldSnapshot:
        now = self.clock.mono_ns() if now_ns is None else now_ns
        stale_ns = int(self.settings.tuning.pose.stale_ms * NS_PER_MS)
        with self._lock:
            cams = {}
            for cam, c in self.calibs.items():
                ok, mode = self._cam_usable_for_control(cam, c, now)
                fp = c.footprint()
                cams[cam] = CamObs(cam, c.info(), c.usable, ok, mode, c.image_size,
                                   None if fp is None else [[float(a), float(b)] for a, b in fp])
            tags = {}
            for tid, o in self._obs.items():
                live_cams = tuple(sorted(k for k in o.cams if now - self._last_frame.get(k, -stale_ns * 2) <= stale_ns))
                if not live_cams and self.registry.snapshot().get(tid) is None:
                    continue                                    # an unregistered tag not seen recently
                tags[tid] = TagObs(tid, bool(live_cams), live_cams, o.x, o.y, o.heading, o.capture_ns, o.size_mm,
                                   o.size_warn, o.last_seen_ns)
            cars = {}
            for name, (tid, x, y, hdg, cap, cam, tracking) in self._cars.items():
                age_ms = (now - cap) / NS_PER_MS
                cam_ok = cams[cam].usable_for_control if cam in cams else False
                cars[name] = CarObs(name, tid, x, y, hdg, cap, cam, age_ms, age_ms <= self.settings.tuning.pose.stale_ms,
                                    cam_ok and tracking)
            return WorldSnapshot(now, tags, cars, cams)

    def node_position(self, tag_id: int, now_ns: int | None = None) -> tuple[float, float] | None:
        """Averaged floor position of a floor tag that a usable camera sees right now, else None."""
        now = self.clock.mono_ns() if now_ns is None else now_ns
        with self._lock:
            o, t = self._obs.get(tag_id), self.floor.get(tag_id)
            if o is None or t is None or not t.placed:
                return None
            stale_ns = int(self.settings.tuning.pose.stale_ms * NS_PER_MS)
            for cam in o.cams:
                c = self.calibs.get(cam)
                if c is not None and c.usable and now - self._last_frame.get(cam, -stale_ns * 2) <= stale_ns:
                    return (t.x, t.y)
            return None

    def live_position(self, tag_id: int, now_ns: int | None = None) -> tuple[float, float] | None:
        """Where the camera sees a tag right now (single-frame, not the averaged position)."""
        now = self.clock.mono_ns() if now_ns is None else now_ns
        with self._lock:
            o = self._obs.get(tag_id)
            stale_ns = int(self.settings.tuning.pose.stale_ms * NS_PER_MS)
            if o is None or o.x is None or not any(now - self._last_frame.get(c, -stale_ns * 2) <= stale_ns for c in o.cams):
                return None
            return (o.x, o.y)

