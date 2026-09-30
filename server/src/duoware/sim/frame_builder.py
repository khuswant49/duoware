"""What the simulated phone's detector reports for one capture instant: the physical tags (floor tags not covered by a
car, car tags at their true pose over the exposure) projected through the camera model, with motion blur."""

import numpy as np

from duoware.localization.geometry import apply_offset, square_corners
from duoware.protocol.phone import Marker
from duoware.sim.camera_model import PinholeCamera
from duoware.sim.detector_model import DetectorModel
from duoware.sim.scenario import Scenario
from duoware.sim.world import SimWorld

NS_PER_S = 1_000_000_000


class FrameBuilder:
    def __init__(self, scenario: Scenario, world: SimWorld, camera: PinholeCamera, detector: DetectorModel) -> None:
        self.scenario, self.world, self.camera, self.detector = scenario, world, camera, detector
        self.cars = {c.name: c for c in scenario.car}

    def _car_corners(self, name: str, t_ns: int) -> np.ndarray:
        c = self.cars[name]
        x, y, h = self.world.true_pose(name, t_ns)
        cen = apply_offset(np.array([x, y]), h, c.tag_offset_mm)
        return np.c_[square_corners(cen[0], cen[1], c.tag_size_mm, h), np.full(4, c.tag_height_mm)]

    def detect(self, t_cap_ns: int, exp_ns: int, only: set[int] | None = None) -> list[Marker]:
        """Markers a detector would report for a frame whose exposure starts at `t_cap_ns` (server clock)."""
        mid = t_cap_ns + exp_ns // 2
        end = t_cap_ns + exp_ns
        hidden = self.world.covered_floor_tags(mid)
        tags: dict[int, np.ndarray] = {}
        speeds: dict[int, float] = {}
        for tid, x, y, yaw, size in self.world.floor_tags:
            if int(tid) in hidden or (only is not None and int(tid) not in only):
                continue
            px, ok = self.camera.project(np.c_[square_corners(x, y, size, yaw), np.zeros(4)])
            if ok.all():
                tags[int(tid)], speeds[int(tid)] = px, 0.0
        for name, c in self.cars.items():
            if only is not None and c.tag not in only:
                continue
            px, ok = self.camera.project(self._car_corners(name, mid))
            if not ok.all():
                continue
            p0, _ = self.camera.project(self._car_corners(name, t_cap_ns))
            p1, _ = self.camera.project(self._car_corners(name, end))
            tags[c.tag] = px
            speeds[c.tag] = float(np.linalg.norm(p1 - p0, axis=1).mean()) * NS_PER_S / max(exp_ns, 1)
        return self.detector.observe(tags, speeds, exp_ns)

    def truth(self, t_mid_ns: int) -> dict[str, tuple[float, float, float]]:
        return {n: self.world.true_pose(n, t_mid_ns) for n in self.cars}
