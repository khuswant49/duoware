"""Test helper: a synthetic scene (the sim.toml world seen by the sim camera) fed into the real server classes
with a fake clock. Used by the localization, layout and API tests."""

import tomllib
from pathlib import Path

import numpy as np

from duoware.ingest.sessions import PhoneSessions
from duoware.localization.floor_tags import FloorTags
from duoware.localization.geometry import apply_offset, square_corners
from duoware.localization.world import WorldModel
from duoware.protocol.phone import Frame, Marker
from duoware.protocol.phone_session import PerfState, Status
from duoware.registry.presets import VenuePresets
from duoware.sim.camera_model import PinholeCamera

SIM_TOML = Path(__file__).resolve().parents[2] / "config" / "sim.toml"
MS = 1_000_000


def load_sim() -> dict:
    with open(SIM_TOML, "rb") as f:
        return tomllib.load(f)


def sim_camera(resolution=None) -> PinholeCamera:
    c = load_sim()["camera"]
    return PinholeCamera(c["position_mm"], c["tilt_deg"], c["yaw_deg"], resolution or c["resolution"], c["hfov_deg"],
                         c["lens_k1"])


class Scene:
    """Projects the sim world (floor tags + cars) into marker corners."""

    def __init__(self, noise_px: float = 0.0, seed: int = 0, resolution=None) -> None:
        sim = load_sim()
        self.camera = sim_camera(resolution)
        self.floor_tags = {t[0]: list(t) for t in sim["world"]["floor_tags"]}      # id -> [id, x, y, yaw, size]
        self.cars = {c["name"]: dict(c, pose=list(c["start"])) for c in sim["car"]}   # pose = [x, y, heading]
        self.hidden: set[int] = set()
        self.noise_px = noise_px
        self.rng = np.random.default_rng(seed)
        self.shift_px = (0.0, 0.0)
        self.seq = 0

    def car_tag_center(self, name: str) -> np.ndarray:
        c = self.cars[name]
        x, y, h = c["pose"]
        return apply_offset(np.array([x, y]), h, tuple(c["tag_offset_mm"]))

    def markers(self, only: set[int] | None = None) -> dict[int, np.ndarray]:
        out = {}
        for tid, x, y, yaw, size in self.floor_tags.values():
            if tid in self.hidden or (only is not None and tid not in only):
                continue
            c = square_corners(x, y, size, yaw)
            out[tid] = np.c_[c, np.zeros(4)]
        for name, c in self.cars.items():
            if only is not None and c["tag"] not in only:
                continue
            x, y, h = c["pose"]
            cen = self.car_tag_center(name)
            cor = square_corners(cen[0], cen[1], c["tag_size_mm"], h)
            out[c["tag"]] = np.c_[cor, np.full(4, c["tag_height_mm"])]
        res = {}
        for tid, pts in out.items():
            px, ok = self.camera.project(pts)
            if ok.all():
                px = px + self.rng.normal(0, self.noise_px, px.shape) + np.array(self.shift_px)
                res[tid] = px
        return res

    def frame(self, session, recv_ns: int, scan: str = "full", searched=(), only=None, delay_ms: float = 40.0,
              exp_ms: float = 3.0, size=None) -> Frame:
        w, h = size or self.camera.resolution
        cap = recv_ns - int(delay_ms * MS)
        ms = tuple(Marker(tid, tuple(float(v) for v in px.ravel())) for tid, px in self.markers(only).items())
        f = Frame(session.cam, session.sid, self.seq, cap - int(exp_ms * MS / 2), int(exp_ms * MS), 0, cap + 20 * MS,
                  recv_ns - 2 * MS, w, h, scan, tuple(searched), ms)
        self.seq += 1
        return f


def status(mode="tracking", cam=1) -> Status:
    return Status(cam, mode, "boottime", None, 30.0, 30.0, (1280, 720), {}, {}, 0, 0, 3000000, 800, "locked", None,
                  None, None, PerfState(*(None,) * 6), None, None, None)


class Rig:
    """Registry + sim_3x3 preset + floor tags + sessions + world model, with one paired synced session."""

    def __init__(self, env, apply_preset: bool = True, noise_px: float = 0.07) -> None:
        from conftest import make_hello
        self.env = env
        if apply_preset:
            VenuePresets(env.db, env.registry, env.overrides, env.events, env.settings, env.safety, env.clock) \
                .apply("sim_3x3")
        self.floor = FloorTags(env.registry)
        self.sessions = PhoneSessions(env.db, env.settings, env.events, env.clock)
        opened = self.sessions.open(make_hello(pair_code=self.sessions.pair_code), "127.0.0.1")
        self.session = opened.session
        self.world = WorldModel(env.settings, env.registry, self.floor, env.clock, env.events, self.sessions)
        self.scene = Scene(noise_px=noise_px)
        self.sync()
        self.sessions.on_status(self.session, status())

    def sync(self) -> None:
        """One clean sync exchange with the phone clock equal to the server clock (theta = 0)."""
        t = self.env.clock.mono_ns()
        self.session.clock_sync.add(t, t + MS // 2, t + MS // 2, t + MS)

    def step(self, n: int = 1, scan: str = "full", dt_ms: float = 33.0, **kw) -> None:
        for _ in range(n):
            self.env.clock.advance(int(dt_ms * MS))
            self.sync()
            now = self.env.clock.mono_ns()
            self.world.on_frame(self.session, self.scene.frame(self.session, now, scan=scan, **kw), now)

    def converge(self, n: int = 60) -> None:
        self.step(n)


def feed_frames(sv, session, scene: Scene, n: int = 1, scan: str = "full", delay_ms: float = 40.0, **kw) -> None:
    """Pushes synthetic frames into a live `Services` (real clock). Each frame has a fresh clean sync sample
    (phone clock = server clock), so the camera counts as synced."""
    for _ in range(n):
        t = sv.clock.mono_ns()
        session.clock_sync.add(t - MS, t - MS // 2, t - MS // 2, t)
        now = sv.clock.mono_ns()
        sv.world.on_frame(session, scene.frame(session, now, scan=scan, delay_ms=delay_ms, **kw), now)
