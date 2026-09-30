"""Loads config/*.toml and .env into frozen dataclasses (DECISIONS.md D9).

Every dataclass mirrors one TOML table: field names are the TOML keys. Loading is strict: an unknown
key, a missing key or a value of the wrong type raises `SettingsError` naming the file and key path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from duoware.tomlload import SettingsError, _build, _load, load_toml_as  # noqa: F401  (re-exported)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_DATA_DIR = REPO_ROOT / "data"


# ---------------------------------------------------------------------------------------- server.toml


@dataclass(frozen=True)
class HttpCfg:
    host: str
    port: int


@dataclass(frozen=True)
class UdpCfg:
    frames_port: int
    beacon_port: int
    beacon_interval_s: float


@dataclass(frozen=True)
class TcpCfg:
    frames_port: int


@dataclass(frozen=True)
class WiredCfg:
    adb_reverse: bool
    adb_path: str
    adb_poll_s: float


@dataclass(frozen=True)
class StorageCfg:
    data_dir: str


@dataclass(frozen=True)
class AccessCfg:
    allow_remote_dashboard: bool
    pair_max_failures: int
    pair_lock_s: float
    allowed_origins: tuple[str, ...]


@dataclass(frozen=True)
class MonitorCfg:
    lag_probe_ms: float
    stall_ms: float
    window_s: float


@dataclass(frozen=True)
class DashboardCfg:
    state_hz: float


@dataclass(frozen=True)
class ServerConfig:
    http: HttpCfg
    udp: UdpCfg
    tcp: TcpCfg
    wired: WiredCfg
    storage: StorageCfg
    access: AccessCfg
    monitor: MonitorCfg
    dashboard: DashboardCfg


# ---------------------------------------------------------------------------------------- cars.toml


@dataclass(frozen=True)
class CarPrior:
    min_pwm: int
    kick_pwm: int
    kick_ms: int
    trim: float


@dataclass(frozen=True)
class CarConfig:
    name: str
    firmware: str
    transport: str
    mac: str
    rfcomm_channel: int
    com_port: str
    priority: int
    footprint_mm: tuple[float, float]
    color: str
    prior: CarPrior


@dataclass(frozen=True)
class CarsFile:
    car: tuple[CarConfig, ...]


# ---------------------------------------------------------------------------------------- map.toml


@dataclass(frozen=True)
class ValidationRules:
    max_edge_angle_deg: float
    edge_clearance_mm: float
    view_margin_mm: float
    expected_spacing_mm: float
    spacing_tolerance_mm: float


@dataclass(frozen=True)
class SuggestRules:
    gap_fraction: float


@dataclass(frozen=True)
class TrackingRules:
    at_node_tol_mm: float
    node_move_tol_mm: float
    node_move_confirm_s: float
    obstacle_block_radius_mm: float


@dataclass(frozen=True)
class BlockedCfg:
    nodes: tuple[int, ...]
    edges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class MapRules:
    validation: ValidationRules
    suggest: SuggestRules
    tracking: TrackingRules
    blocked: BlockedCfg


# ---------------------------------------------------------------------------------------- tuning.toml


@dataclass(frozen=True)
class MarkersCfg:
    dictionary: str
    default_size_mm: float
    size_warn_fraction: float


@dataclass(frozen=True)
class PoseCfg:
    stale_ms: float
    stats_window_s: float


@dataclass(frozen=True)
class ClockSyncCfg:
    interval_ms: float
    fast_interval_ms: float
    fast_period_s: float
    max_rtt_ms: float
    window_s: float
    best_fraction: float
    unsynced_after_s: float
    slope_min_span_fraction: float


@dataclass(frozen=True)
class LocalizationCfg:
    settle_frames: int
    move_tol_px: float
    ransac_threshold_mm: float
    max_fit_rms_mm: float
    drift_tol_mm: float
    drift_frames: int
    place_frames: int
    place_move_tol_mm: float
    parallax_ema: float
    parallax_max_scale: float


@dataclass(frozen=True)
class LinkCfg:
    stats_window_s: float


@dataclass(frozen=True)
class CameraCfg:
    resolution: tuple[int, int]
    fps: float
    exposure_ms: float
    iso: int
    focus: str
    awb: str
    preview_fps: float
    preview_width: int
    preview_jpeg_quality: int


@dataclass(frozen=True)
class ThermalCfg:
    forecast_s: float
    headroom_down: float
    headroom_up: float
    up_after_s: float
    status_down: int
    fps_steps: tuple[float, ...]
    resolution_steps: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class PhoneCfg:
    full_scan_every: int
    roi_margin: float
    roi_min_px: int
    threads: int
    thermal: ThermalCfg


@dataclass(frozen=True)
class Tuning:
    markers: MarkersCfg
    pose: PoseCfg
    clock_sync: ClockSyncCfg
    localization: LocalizationCfg
    link: LinkCfg
    camera: CameraCfg
    phone: PhoneCfg


@dataclass(frozen=True)
class Settings:
    server: ServerConfig
    cars: tuple[CarConfig, ...]
    map_rules: MapRules
    tuning: Tuning
    config_dir: Path
    data_dir: Path
    env: dict[str, str]

    def car(self, name: str) -> CarConfig | None:
        return next((c for c in self.cars if c.name == name), None)


# ---------------------------------------------------------------------------------------- .env


def load_env(path: Path | None) -> dict[str, str]:
    """Parses KEY=VALUE lines (# comments, optional quotes). Values in the real environment win."""
    values: dict[str, str] = {}
    if path is not None and path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            values[key.strip()] = val
    for key in list(values) + ["DUO_PAIR_CODE", "DUO_ACCESS_CODE", "GROQ_API_KEY"]:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


# ---------------------------------------------------------------------------------------- validation


def _validate(server: ServerConfig, cars: tuple[CarConfig, ...], tuning: Tuning) -> None:
    names = [c.name for c in cars]
    if len(set(names)) != len(names):
        raise SettingsError("cars.toml: car names must be unique")
    prios = [c.priority for c in cars]
    if len(set(prios)) != len(prios):
        raise SettingsError("cars.toml: car priorities must be unique")
    if not tuning.pose.stale_ms > 0:
        raise SettingsError("tuning.toml.pose.stale_ms: must be > 0")
    if not 0 < tuning.clock_sync.best_fraction <= 1:
        raise SettingsError("tuning.toml.clock_sync.best_fraction: must be in (0, 1]")
    if not all(v > 0 for v in tuning.camera.resolution):
        raise SettingsError("tuning.toml.camera.resolution: two positive integers expected")
    if not 0 <= tuning.clock_sync.slope_min_span_fraction <= 1:
        raise SettingsError("tuning.toml.clock_sync.slope_min_span_fraction: must be in [0, 1]")
    m = server.monitor
    if not (m.lag_probe_ms > 0 and m.stall_ms > 0 and m.window_s > 0):
        raise SettingsError("server.toml.monitor: lag_probe_ms, stall_ms and window_s must be > 0")
    if not server.dashboard.state_hz > 0:
        raise SettingsError("server.toml.dashboard.state_hz: must be > 0")


def load_settings(config_dir: Path | None = None, data_dir: Path | None = None) -> Settings:
    cfg = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    server = _load(ServerConfig, cfg / "server.toml")
    cars = _load(CarsFile, cfg / "cars.toml").car
    map_rules = _load(MapRules, cfg / "map.toml")
    tuning = _load(Tuning, cfg / "tuning.toml")
    _validate(server, cars, tuning)
    if data_dir is not None:
        data = Path(data_dir)
    else:
        data = Path(server.storage.data_dir)
        if not data.is_absolute():
            data = REPO_ROOT / data
    env = load_env(REPO_ROOT / ".env" if cfg == DEFAULT_CONFIG_DIR else cfg.parent / ".env")
    return Settings(server=server, cars=cars, map_rules=map_rules, tuning=tuning,
                    config_dir=cfg, data_dir=data, env=env)
