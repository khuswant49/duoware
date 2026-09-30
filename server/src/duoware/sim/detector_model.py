"""A statistical model of the phone's marker detector (DECISIONS.md D14): it takes the exact projected corners and
returns what a real detector would: Gaussian corner noise, and a detection probability that falls with motion blur
(blur length = image speed x exposure). Fitted to DUO-WARE 1 recordings (config/sim.toml [phone_model])."""

import numpy as np

from duoware.protocol.phone import Marker
from duoware.sim.scenario import PhoneModelCfg

NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000


class DetectorModel:
    def __init__(self, cfg: PhoneModelCfg, rng: np.random.Generator) -> None:
        self.cfg, self.rng = cfg, rng

    def probability(self, blur_px: float) -> float:
        """Piecewise linear through (blur_ok_px, 1), (blur_half_px, 0.5), (blur_zero_px, 0)."""
        c = self.cfg
        return float(np.interp(blur_px, [c.blur_ok_px, c.blur_half_px, c.blur_zero_px], [1.0, 0.5, 0.0]))

    def blur_px(self, speed_px_s: float, exposure_ns: int) -> float:
        return speed_px_s * exposure_ns / NS_PER_S

    def observe(self, tags_px: dict[int, np.ndarray], speeds_px_s: dict[int, float], exposure_ns: int) -> list[Marker]:
        """`tags_px`: tag id -> (4, 2) corners of tags fully inside the image. Returns the detected markers."""
        out = []
        for tid, px in tags_px.items():
            if self.rng.random() < self.probability(self.blur_px(speeds_px_s.get(tid, 0.0), exposure_ns)):
                noisy = px + self.rng.normal(0.0, self.cfg.corner_noise_px, px.shape)
                out.append(Marker(tid, tuple(float(v) for v in noisy.ravel())))   # type: ignore[arg-type]
        return out

    def _draw(self, mean_spread_ms: tuple[float, float]) -> int:
        mean, spread = mean_spread_ms
        return int((mean + self.rng.uniform(-spread, spread)) * NS_PER_MS)

    def pipeline_ns(self) -> int:
        """Sensor capture -> frame available to the app."""
        return self._draw(self.cfg.pipeline_ms)

    def detect_ns(self) -> int:
        return self._draw(self.cfg.detect_ms)
