"""Loads config/*.toml and .env into frozen dataclasses (DECISIONS.md D9).

Every dataclass mirrors one TOML table: field names are the TOML keys. Loading is strict: an unknown
key, a missing key or a value of the wrong type raises `SettingsError` naming the file and key path.
"""

from __future__ import annotations

import os
import tomllib
import types
import typing
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_DATA_DIR = REPO_ROOT / "data"


class SettingsError(Exception):
    """A config file is wrong. The message names the file, the key path and what was expected."""


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


# ---------------------------------------------------------------------------------------- strict builder


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", None) if isinstance(tp, type) else str(tp).replace("typing.", "")


def _convert(tp: Any, value: Any, path: str) -> Any:
    origin = typing.get_origin(tp)
    if is_dataclass(tp):
        if not isinstance(value, dict):
            raise SettingsError(f"{path}: expected a table, got {type(value).__name__}")
        return _build(tp, value, path)
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(value, list):
            raise SettingsError(f"{path}: expected an array, got {type(value).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert(args[0], v, f"{path}[{i}]") for i, v in enumerate(value))
        if len(value) != len(args):
            raise SettingsError(f"{path}: expected an array of {len(args)} values, got {len(value)}")
        return tuple(_convert(a, v, f"{path}[{i}]") for i, (a, v) in enumerate(zip(args, value)))
    if origin is dict:
        _, vt = typing.get_args(tp)
        if not isinstance(value, dict):
            raise SettingsError(f"{path}: expected a table, got {type(value).__name__}")
        return {str(k): _convert(vt, v, f"{path}.{k}") for k, v in value.items()}
    if origin in (typing.Union, types.UnionType):
        raise SettingsError(f"{path}: unsupported union type in settings.py")
    if tp is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"{path}: expected bool, got {value!r}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{path}: expected int, got {value!r}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{path}: expected a number, got {value!r}")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise SettingsError(f"{path}: expected str, got {value!r}")
        return value
    raise SettingsError(f"{path}: unsupported type {_type_name(tp)} in settings.py")


def _build(cls: type, data: dict, path: str) -> Any:
    hints = typing.get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    for key in data:
        if key not in names:
            raise SettingsError(f"{path}.{key}: unknown key (expected one of {sorted(names)})")
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            raise SettingsError(f"{path}.{f.name}: missing key (expected {_type_name(hints[f.name])})")
        kwargs[f.name] = _convert(hints[f.name], data[f.name], f"{path}.{f.name}")
    return cls(**kwargs)


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as e:
        raise SettingsError(f"{path}: file not found") from e
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path}: invalid TOML ({e})") from e


def load_toml_as(cls: type, path: Path) -> Any:
    """Strictly loads one TOML file into the frozen dataclass `cls` (used by the simulator scenario too)."""
    return _load(cls, path)


def _load(cls: type, path: Path) -> Any:
    try:
        return _build(cls, _read_toml(path), path.name)
    except SettingsError as e:
        if str(e).startswith(path.name):
            raise SettingsError(f"{path}: {str(e)[len(path.name):].lstrip('.')}") from None
        raise


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
