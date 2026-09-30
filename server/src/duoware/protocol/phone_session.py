"""Phone WebSocket session messages (PROTOCOL.md §4.3 hello/welcome/error, §4.4 settings, §4.5 status,
§4.7 bench). Receivers ignore unknown fields (§0); `null` is accepted exactly where a phone may be unable
to measure a value."""

import json
from dataclasses import dataclass, field

from duoware import PROTOCOL_VERSION
from duoware.protocol import ProtocolError
from duoware.protocol._read import Obj, bad, is_int


def _dumps(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False)


# ----------------------------------------------------------------------------------------- hello


@dataclass(frozen=True)
class WifiInfo:
    band_ghz: float | None
    rssi_dbm: int | None
    link_mbps: int | None

    @staticmethod
    def parse(o: Obj) -> "WifiInfo":
        return WifiInfo(o.opt_num("band_ghz"), o.opt_int("rssi_dbm"), o.opt_int("link_mbps"))

    def to_dict(self) -> dict:
        return {"band_ghz": self.band_ghz, "rssi_dbm": self.rssi_dbm, "link_mbps": self.link_mbps}


@dataclass(frozen=True)
class Link:
    mode: str                       # wired_tether | wired_adb | wireless
    interface: str | None
    local_ip: str | None
    wifi: WifiInfo | None           # null unless mode == "wireless"

    @staticmethod
    def parse(o: Obj) -> "Link":
        w = o.opt_obj("wifi")
        return Link(o.str("mode"), o.opt_str("interface"), o.opt_str("local_ip"), WifiInfo.parse(w) if w else None)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "interface": self.interface, "local_ip": self.local_ip,
                "wifi": self.wifi.to_dict() if self.wifi else None}


@dataclass(frozen=True)
class CpuInfo:
    cores: int
    max_khz: tuple[int, ...]

    def to_dict(self) -> dict:
        return {"cores": self.cores, "max_khz": list(self.max_khz)}


@dataclass(frozen=True)
class CameraCaps:
    id: str
    hw_level: str
    capabilities: tuple[str, ...]
    ts_source: str
    exposure_ns_range: tuple[int, int] | None
    iso_range: tuple[int, int] | None
    fps_ranges: tuple[tuple[int, int], ...]
    yuv_sizes: tuple[tuple[int, int, float], ...]     # (w, h, max_fps)
    rolling_shutter_skew: bool
    min_focus_diopters: float | None
    intrinsics: tuple[float, ...] | None
    distortion: tuple[float, ...] | None
    active_array: tuple[int, int] | None

    @staticmethod
    def parse(o: Obj) -> "CameraCaps":
        def pair(key: str) -> tuple[int, int] | None:
            v = o.opt_list(key)
            if v is None:
                return None
            if len(v) != 2 or not all(is_int(x) for x in v):
                raise bad(f"camera.{key}: expected two ints or null")
            return (v[0], v[1])

        fps = tuple((r[0], r[1]) for r in o.list("fps_ranges") if isinstance(r, list) and len(r) == 2)
        yuv = tuple((int(r[0]), int(r[1]), float(r[2])) for r in o.list("yuv_sizes")
                    if isinstance(r, list) and len(r) == 3)
        inv = o.opt_num_list("intrinsics")
        dist = o.opt_num_list("distortion")
        return CameraCaps(o.str("id"), o.str("hw_level"), tuple(str(c) for c in o.list("capabilities")),
                          o.str("ts_source"), pair("exposure_ns_range"), pair("iso_range"), fps, yuv,
                          o.bool("rolling_shutter_skew"), o.opt_num("min_focus_diopters"),
                          tuple(inv) if inv else None, tuple(dist) if dist else None, pair("active_array"))

    def to_dict(self) -> dict:
        return {"id": self.id, "hw_level": self.hw_level, "capabilities": list(self.capabilities),
                "ts_source": self.ts_source,
                "exposure_ns_range": list(self.exposure_ns_range) if self.exposure_ns_range else None,
                "iso_range": list(self.iso_range) if self.iso_range else None,
                "fps_ranges": [list(r) for r in self.fps_ranges], "yuv_sizes": [list(r) for r in self.yuv_sizes],
                "rolling_shutter_skew": self.rolling_shutter_skew, "min_focus_diopters": self.min_focus_diopters,
                "intrinsics": list(self.intrinsics) if self.intrinsics else None,
                "distortion": list(self.distortion) if self.distortion else None,
                "active_array": list(self.active_array) if self.active_array else None}


@dataclass(frozen=True)
class Hello:
    device_id: str
    token: str | None
    pair_code: str | None
    app_version: str
    model: str
    android: str
    sdk: int
    link: Link
    cpu: CpuInfo
    camera: CameraCaps
    v: int = PROTOCOL_VERSION

    @staticmethod
    def parse(o: Obj) -> "Hello":
        cpu = o.obj("cpu")
        return Hello(o.str("device_id"), o.opt_str("token"), o.opt_str("pair_code"), o.str("app_version"),
                     o.str("model"), o.str("android"), o.int("sdk"), Link.parse(o.obj("link")),
                     CpuInfo(cpu.int("cores"), tuple(cpu.int_list("max_khz"))), CameraCaps.parse(o.obj("camera")))


def encode_hello(h: Hello) -> str:
    return _dumps({"v": PROTOCOL_VERSION, "t": "hello", "device_id": h.device_id, "token": h.token,
                   "pair_code": h.pair_code, "app_version": h.app_version, "model": h.model,
                   "android": h.android, "sdk": h.sdk, "link": h.link.to_dict(), "cpu": h.cpu.to_dict(),
                   "camera": h.camera.to_dict()})


# ----------------------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class Tracking:
    track_ids: tuple[int, ...]
    full_scan_every: int
    roi_margin: float
    roi_min_px: int
    threads: int


@dataclass(frozen=True)
class ThermalPolicy:
    forecast_s: float
    headroom_down: float
    headroom_up: float
    up_after_s: float
    status_down: int
    fps_steps: tuple[float, ...]
    resolution_steps: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class PreviewSettings:
    fps: float
    width: int
    quality: int


@dataclass(frozen=True)
class CameraSettings:
    resolution: tuple[int, int]
    fps: float
    exposure_ns: int
    iso: int
    focus: str
    awb: str
    tracking: Tracking
    thermal: ThermalPolicy
    preview: PreviewSettings

    def to_dict(self) -> dict:
        t, th, pv = self.tracking, self.thermal, self.preview
        return {"v": PROTOCOL_VERSION, "t": "settings", "resolution": list(self.resolution), "fps": self.fps,
                "exposure_ns": self.exposure_ns, "iso": self.iso, "focus": self.focus, "awb": self.awb,
                "tracking": {"track_ids": list(t.track_ids), "full_scan_every": t.full_scan_every,
                             "roi_margin": t.roi_margin, "roi_min_px": t.roi_min_px, "threads": t.threads},
                "thermal": {"forecast_s": th.forecast_s, "headroom_down": th.headroom_down,
                            "headroom_up": th.headroom_up, "up_after_s": th.up_after_s,
                            "status_down": th.status_down, "fps_steps": list(th.fps_steps),
                            "resolution_steps": [list(r) for r in th.resolution_steps]},
                "preview": {"fps": pv.fps, "width": pv.width, "quality": pv.quality}}

    @staticmethod
    def parse(o: Obj) -> "CameraSettings":
        res = o.int_list("resolution")
        if len(res) != 2:
            raise bad("resolution: expected two ints")
        t, th, pv = o.obj("tracking"), o.obj("thermal"), o.obj("preview")
        steps = tuple((r[0], r[1]) for r in th.list("resolution_steps"))
        return CameraSettings(
            (res[0], res[1]), o.num("fps"), o.int("exposure_ns"), o.int("iso"), o.str("focus"), o.str("awb"),
            Tracking(tuple(t.int_list("track_ids")), t.int("full_scan_every"), t.num("roi_margin"),
                     t.int("roi_min_px"), t.int("threads")),
            ThermalPolicy(th.num("forecast_s"), th.num("headroom_down"), th.num("headroom_up"),
                          th.num("up_after_s"), th.int("status_down"), tuple(th.num_list("fps_steps")), steps),
            PreviewSettings(pv.num("fps"), pv.int("width"), pv.int("quality")))


def encode_settings(s: CameraSettings) -> str:
    return _dumps(s.to_dict())


@dataclass(frozen=True)
class Welcome:
    cam: int
    sid: str
    token: str | None
    frames_port: int
    frames_tcp_port: int
    server_id: str
    settings: CameraSettings

    @staticmethod
    def parse(o: Obj) -> "Welcome":
        return Welcome(o.int("cam"), o.str("sid"), o.opt_str("token"), o.int("frames_port"),
                       o.int("frames_tcp_port"), o.str("server_id"), CameraSettings.parse(o.obj("settings")))


def encode_welcome(w: Welcome) -> str:
    return _dumps({"v": PROTOCOL_VERSION, "t": "welcome", "cam": w.cam, "sid": w.sid, "token": w.token,
                   "frames_port": w.frames_port, "frames_tcp_port": w.frames_tcp_port, "server_id": w.server_id,
                   "settings": w.settings.to_dict()})


@dataclass(frozen=True)
class PhoneError:
    code: str
    message: str


def encode_error(code: str, message: str) -> str:
    return _dumps({"v": PROTOCOL_VERSION, "t": "error", "code": code, "message": message})


# ----------------------------------------------------------------------------------------- status


@dataclass(frozen=True)
class StageStats:
    p50: float | None
    p95: float | None

    @staticmethod
    def parse(o: Obj | None) -> "StageStats | None":
        return None if o is None else StageStats(o.opt_num("p50"), o.opt_num("p95"))

    def to_dict(self) -> dict:
        return {"p50": self.p50, "p95": self.p95}


@dataclass(frozen=True)
class ThermalState:
    status: int | None
    headroom: float | None
    level: int | None
    reason: str | None


@dataclass(frozen=True)
class PerfState:
    foreground_service: bool | None
    wake_lock: bool | None
    wifi_low_latency: bool | None
    sustained_mode: bool | None
    hint_session: bool | None
    battery_opt_exempt: bool | None


STAGE_NAMES = ("pipeline", "detect_full", "detect_roi", "send", "cap_to_sent")


@dataclass(frozen=True)
class Status:
    cam: int
    app_mode: str                              # tracking | setup | benchmark
    clock: str                                 # boottime | monotonic | unknown
    link: Link | None
    fps: float | None
    fps_target: float | None
    resolution: tuple[int, int] | None
    stages_ms: dict[str, StageStats | None]
    scan: dict[str, float | None]
    frames_skipped: int | None
    send_dropped: int | None
    exposure_ns: int | None
    iso: int | None
    focus: str | None
    cpu_app_pct: float | None
    cpu_max_cur_khz: int | None
    thermal: ThermalState | None
    perf: PerfState | None
    battery_pct: int | None
    charging: bool | None
    markers_seen: int | None

    @staticmethod
    def parse(o: Obj) -> "Status":
        """Lenient: a value the phone cannot measure is null, and an absent key counts as null."""
        res = o.opt_list("resolution")
        stages = o.opt_obj("stages_ms")
        scan = o.opt_obj("scan")
        cpu, th, perf, link = o.opt_obj("cpu"), o.opt_obj("thermal"), o.opt_obj("perf"), o.opt_obj("link")
        return Status(
            o.int("cam"), o.str("app_mode"), o.str("clock"), Link.parse(link) if link else None,
            o.opt_num("fps"), o.opt_num("fps_target"), (int(res[0]), int(res[1])) if res and len(res) == 2 else None,
            {n: StageStats.parse(stages.opt_obj(n)) if stages else None for n in STAGE_NAMES},
            {k: scan.opt_num(k) if scan else None for k in ("full_every", "rois_per_frame", "lost_rescans")},
            o.opt_int("frames_skipped"), o.opt_int("send_dropped"), o.opt_int("exposure_ns"), o.opt_int("iso"),
            o.opt_str("focus"), cpu.opt_num("app_pct") if cpu else None, cpu.opt_int("max_cur_khz") if cpu else None,
            ThermalState(th.opt_int("status"), th.opt_num("headroom"), th.opt_int("level"), th.opt_str("reason"))
            if th else None,
            PerfState(*(perf.opt_bool(k) for k in ("foreground_service", "wake_lock", "wifi_low_latency",
                                                   "sustained_mode", "hint_session", "battery_opt_exempt")))
            if perf else None,
            o.opt_int("battery_pct"), o.opt_bool("charging"), o.opt_int("markers_seen"))


def encode_status(s: Status) -> str:
    return _dumps({
        "v": PROTOCOL_VERSION, "t": "status", "cam": s.cam, "app_mode": s.app_mode, "clock": s.clock,
        "link": s.link.to_dict() if s.link else None, "fps": s.fps, "fps_target": s.fps_target,
        "resolution": list(s.resolution) if s.resolution else None,
        "stages_ms": {k: (v.to_dict() if v else None) for k, v in s.stages_ms.items()},
        "scan": s.scan, "frames_skipped": s.frames_skipped, "send_dropped": s.send_dropped,
        "exposure_ns": s.exposure_ns, "iso": s.iso, "focus": s.focus,
        "cpu": {"app_pct": s.cpu_app_pct, "max_cur_khz": s.cpu_max_cur_khz},
        "thermal": None if s.thermal is None else {"status": s.thermal.status, "headroom": s.thermal.headroom,
                                                    "level": s.thermal.level, "reason": s.thermal.reason},
        "perf": None if s.perf is None else {k: getattr(s.perf, k) for k in (
            "foreground_service", "wake_lock", "wifi_low_latency", "sustained_mode", "hint_session",
            "battery_opt_exempt")},
        "battery_pct": s.battery_pct, "charging": s.charging, "markers_seen": s.markers_seen})


# ----------------------------------------------------------------------------------------- bench


@dataclass(frozen=True)
class BenchResult:
    data: dict = field(default_factory=dict)   # stored as received (PROTOCOL.md §4.7), shown in M2


@dataclass(frozen=True)
class Bench:
    cam: int
    run_id: str
    results: tuple[BenchResult, ...]


def parse_ws_message(text: str) -> Hello | Status | Bench:
    """A phone -> server WebSocket text message. Raises `ProtocolError` (`bad` or `version`)."""
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise bad("not JSON") from e
    o = Obj(obj)
    if not o.has("v") or not is_int(o.d["v"]):
        raise bad("v: expected int")
    if o.d["v"] != PROTOCOL_VERSION:
        raise ProtocolError("version", f"v={o.d['v']}")
    t = o.str("t")
    if t == "hello":
        return Hello.parse(o)
    if t == "status":
        return Status.parse(o)
    if t == "bench":
        results = tuple(BenchResult(dict(Obj(r, "results[]").d)) for r in o.list("results"))
        return Bench(o.int("cam"), o.str("run_id"), results)
    raise bad(f"t: unexpected message type {t!r}")
