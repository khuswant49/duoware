"""Dashboard-facing models (PROTOCOL.md §6 WebSocket, §7.4 tag documents, §7.5 layout, §8 events).

pydantic only at this boundary. `extra="forbid"` makes drift between PROTOCOL.md and the code fail the
example tests instead of going unnoticed. Fields the protocol allows to be `null` are `X | None` with no
default, so forgetting to fill one is an error too.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------------------- §6.1 / §6.2


class HelloMsg(_M):
    v: int
    t: Literal["hello"]
    server_id: str
    version: str
    mode: Literal["sim", "hardware"]
    state_hz: float


class SystemState(_M):
    estop: bool
    estop_since_wall_ms: int | None
    estop_by: str | None
    mode: Literal["sim", "hardware"]
    registry_version: int
    layout_version: int
    events_last_id: int


class WifiState(_M):
    band_ghz: float | None
    rssi_dbm: int | None
    link_mbps: int | None


class Pct(_M):
    p50: float | None
    p95: float | None


class LinkState(_M):
    mode: str | None
    transport: Literal["udp", "tcp"] | None
    interface: str | None
    wifi: WifiState | None
    latency_ms: Pct | None
    jitter_ms: float | None
    loss_pct: float | None
    send_dropped: int | None


class SyncState(_M):
    ok: bool
    rtt_ms: float | None
    samples: int
    rejected: int = 0                   # (from M2) PROTOCOL.md §3.3


class CalibState(_M):
    status: Literal["UNCALIBRATED", "WEAK", "OK", "MISALIGNED"]
    model: str | None
    rms_mm: float | None
    residual_mm: float | None
    floor_tags_used: list[int]
    floor_tags_seen: list[int]


class StagesState(_M):
    pipeline: Pct | None
    detect_full: Pct | None
    detect_roi: Pct | None
    send: Pct | None
    cap_to_sent: Pct | None


class PoseAge(_M):
    p50: float | None
    p95: float | None
    max: float | None
    n: int


class ThermalInfo(_M):
    status: int | None
    headroom: float | None
    level: int | None
    reason: str | None


class CameraState(_M):
    cam: int
    online: bool
    model: str | None
    app_mode: str | None
    link: LinkState | None
    sync: SyncState
    calib: CalibState
    fps: float | None
    fps_target: float | None
    resolution: list[int] | None
    stages_ms: StagesState | None
    pose_age_ms: PoseAge
    dropped: int
    rx_bad: int
    rx_unauth: int
    rx_late: int
    cpu_app_pct: float | None
    thermal: ThermalInfo | None
    exposure_ms: float | None
    iso: int | None
    preview_seq: int | None
    status_age_ms: float | None
    # Additive fields (PROTOCOL.md §0: adding an optional field is not a version change). §2 requires the
    # server to count them and show "protocol mismatch"; proposed for PROTOCOL.md in docs/plans/M1.md.
    rx_version: int = 0
    rx_bad_marker: int = 0
    clock: str | None = None
    frame_age_ms: float | None = None   # (from M2) PROTOCOL.md §6.2
    processing_on: list[str] | None = None


class TagState(_M):
    id: int
    role: str
    label: str | None
    seen: bool
    cams: list[int]
    x_mm: float | None
    y_mm: float | None
    heading_deg: float | None
    age_ms: float | None
    size_mm: float | None
    size_warn: bool
    placed: bool | None


class CarLinkState(_M):
    state: Literal["disconnected", "connecting", "connected", "degraded", "error"]
    rtt_ms: float | None
    fw: str | None


class CarPoseState(_M):
    x_mm: float
    y_mm: float
    heading_deg: float
    age_ms: float
    fresh: bool
    at_node: int | None
    usable_for_control: bool = True


class CarState(_M):
    name: str
    tag: int | None
    color: str
    link: CarLinkState
    pose: CarPoseState | None
    motion: dict[str, Any] | None
    stopped_reason: str | None
    battery: Literal["not_measured"]


class StateMsg(_M):
    v: int
    t: Literal["state"]
    seq: int
    wall_ms: int
    system: SystemState
    cameras: list[CameraState]
    tags: list[TagState]
    cars: list[CarState]


# ----------------------------------------------------------------------------------------- §8


class EventRecord(_M):
    id: int
    wall_ms: int
    mono_ms: int
    type: str
    car: str | None
    operator: str | None
    key: str | None
    value: str | None
    prev: str | None
    facts: dict[str, Any]
    reason: str


class EventMsg(_M):
    v: int
    t: Literal["event"]
    event: EventRecord


# ----------------------------------------------------------------------------------------- §7.4


class TagPose(_M):
    x_mm: float
    y_mm: float
    yaw_deg: float


class Station(_M):
    name: str
    kind: Literal["pickup", "dropoff", "home", "charging", "custom"]


class TagEntry(_M):
    """One entry of `GET /api/tags`: the stored document (only the fields that belong to its role) plus the
    live fields of `state.tags`. The measured floor size is `measured_size_mm` because `size_mm` is already
    the printed size in the document (PROTOCOL.md §7.4 names it twice; see "Proposed changes" in M1.md)."""

    id: int
    role: str
    size_mm: float | None
    label: str | None
    version: int
    updated_wall_ms: int | None
    updated_by: str | None
    car: str | None = None
    offset_mm: list[float] | None = None
    heading_offset_deg: float | None = None
    origin: bool | None = None
    pose: TagPose | None = None
    grid: list[int] | None = None
    station: Station | None = None
    radius_mm: float | None = None
    placed: bool | None = None
    seen: bool
    cams: list[int]
    x_mm: float | None
    y_mm: float | None
    heading_deg: float | None
    age_ms: float | None
    measured_size_mm: float | None
    size_warn: bool


class TagList(_M):
    registry_version: int
    tags: list[TagEntry]


# ----------------------------------------------------------------------------------------- §7.5


class LayoutNode(_M):
    id: int
    grid: list[int]
    x_mm: float | None
    y_mm: float | None
    station: Station | None
    state: Literal["ok", "unmeasured", "blocked", "moved", "outside_view"]
    blocked_by: str | None


class LayoutEdge(_M):
    a: int
    b: int
    length_mm: float | None
    bearing_deg: float | None
    angle_error_deg: float | None
    state: Literal["ok", "blocked"]
    blocked_by: str | None


class LayoutWarning(_M):
    code: Literal["edge_angle", "edge_short", "node_outside_view", "spacing_mismatch", "isolated_node"]
    ids: list[int]
    message: str


class Footprint(_M):
    cam: int
    polygon_mm: list[list[float]]


class Layout(_M):
    version: int
    measured_wall_ms: int | None
    grid_rotation_deg: float
    nodes: list[LayoutNode]
    edges: list[LayoutEdge]
    warnings: list[LayoutWarning]
    footprints: list[Footprint]
