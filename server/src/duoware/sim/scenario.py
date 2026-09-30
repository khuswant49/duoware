"""The simulator scenario: `config/sim.toml`, the simulated PHYSICAL world (ground truth only the simulator knows;
DECISIONS.md D14). Loaded with the same strictness as `settings.py`."""

from dataclasses import dataclass
from pathlib import Path

from duoware.settings import DEFAULT_CONFIG_DIR, load_toml_as

DEFAULT_SCENARIO = DEFAULT_CONFIG_DIR / "sim.toml"


@dataclass(frozen=True)
class SimServerCfg:
    discovery: bool
    discovery_wait_s: float
    host: str
    http_port: int
    frames_port: int
    beacon_port: int
    pair_code: str


@dataclass(frozen=True)
class SimWorldCfg:
    floor_tags: tuple[tuple[float, ...], ...]       # [id, x_mm, y_mm, yaw_deg, size_mm]


@dataclass(frozen=True)
class SimCameraCfg:
    id: int
    device_id: str
    position_mm: tuple[float, float, float]
    tilt_deg: tuple[float, float]
    yaw_deg: float
    resolution: tuple[int, int]
    hfov_deg: float
    lens_k1: float
    fps: float
    timestamp_clock: str


@dataclass(frozen=True)
class PhoneModelCfg:
    corner_noise_px: float
    blur_ok_px: float
    blur_half_px: float
    blur_zero_px: float
    exposure_ms: float
    link_mode: str
    pipeline_ms: tuple[float, float]
    detect_ms: tuple[float, float]
    clock_offset_s: float
    clock_drift_ppm: float


@dataclass(frozen=True)
class NetworkCfg:
    delay_ms: tuple[float, float]
    drop_fraction: float
    burst_drop_every_s: float
    burst_drop_ms: float


@dataclass(frozen=True)
class SimCarCfg:
    name: str
    tag: int
    tag_size_mm: float
    tag_height_mm: float
    tag_offset_mm: tuple[float, float]
    start: tuple[float, float, float]
    tcp_port: int
    wheel_base_mm: float
    max_speed_mm_s: float
    dead_zone_pwm: float
    right_gain: float
    time_constant_ms: float
    link_delay_ms: tuple[float, float]


@dataclass(frozen=True)
class ScriptCfg:
    enabled: bool
    speed_mm_s: float
    turn_deg_s: float
    routes: dict[str, tuple[int, ...]]


@dataclass(frozen=True)
class Scenario:
    server: SimServerCfg
    world: SimWorldCfg
    camera: SimCameraCfg
    phone_model: PhoneModelCfg
    network: NetworkCfg
    car: tuple[SimCarCfg, ...]
    script: ScriptCfg

    def car_by_name(self, name: str) -> SimCarCfg:
        return next(c for c in self.car if c.name == name)


def load_scenario(path: Path | None = None) -> Scenario:
    return load_toml_as(Scenario, Path(path) if path else DEFAULT_SCENARIO)
