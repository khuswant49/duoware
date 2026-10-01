// Mirror of PROTOCOL.md v1: §6 (WebSocket), §7.2-7.5 (REST), §8 (events). Kept by hand (DECISIONS.md D15); the server
// tests check the PROTOCOL.md examples against the server models, and src/api/types.test.ts checks this file's
// shapes against the §6.2 example. No `any` here. `null` exactly where the protocol says a value may be unmeasured.

export const PROTOCOL_VERSION = 1;

export type Mode = "sim" | "hardware";

// ---------------------------------------------------------------------------------------------- §6.1 / §6.2

export interface HelloMsg {
  v: number;
  t: "hello";
  server_id: string;
  version: string;
  mode: Mode;
  state_hz: number;
}

export interface SystemState {
  estop: boolean;
  estop_since_wall_ms: number | null;
  estop_by: string | null;
  mode: Mode;
  registry_version: number;
  layout_version: number;
  events_last_id: number;
}

export interface WifiState {
  band_ghz: number | null;
  rssi_dbm: number | null;
  link_mbps: number | null;
}

export interface Pct {
  p50: number | null;
  p95: number | null;
}

export interface LinkState {
  mode: string | null;
  transport: "udp" | "tcp" | null;
  interface: string | null;
  wifi: WifiState | null;
  latency_ms: Pct | null;
  jitter_ms: number | null;
  loss_pct: number | null;
  send_dropped: number | null;
}

export type CalibStatus = "UNCALIBRATED" | "WEAK" | "OK" | "MISALIGNED";

export interface CalibState {
  status: CalibStatus;
  model: string | null;
  rms_mm: number | null;
  residual_mm: number | null;
  floor_tags_used: number[];
  floor_tags_seen: number[];
}

export interface StagesState {
  pipeline: Pct | null;
  detect_full: Pct | null;
  detect_roi: Pct | null;
  send: Pct | null;
  cap_to_sent: Pct | null;
}

export interface PoseAge {
  p50: number | null;
  p95: number | null;
  max: number | null;
  n: number;
}

export interface ThermalInfo {
  status: number | null;
  headroom: number | null;
  level: number | null;
  reason: string | null;
}

export interface CameraState {
  cam: number;
  online: boolean;
  model: string | null;
  app_mode: string | null;
  link: LinkState | null;
  sync: { ok: boolean; rtt_ms: number | null; samples: number; rejected?: number }; // rejected: from M2 (§3.3)
  calib: CalibState;
  fps: number | null;
  fps_target: number | null;
  resolution: number[] | null;
  stages_ms: StagesState | null;
  pose_age_ms: PoseAge;
  dropped: number;
  rx_bad: number;
  rx_unauth: number;
  rx_late: number;
  cpu_app_pct: number | null;
  thermal: ThermalInfo | null;
  exposure_ms: number | null;
  iso: number | null;
  preview_seq: number | null;
  status_age_ms: number | null;
  // additive (PROTOCOL.md §0): counters §2 requires, and the phone's sync clock
  rx_version?: number;
  rx_bad_marker?: number;
  clock?: string | null;
  caps?: CameraCaps | null; // GET /api/cameras only
  frame_age_ms?: number | null; // from M2 (§6.2)
  processing_on?: string[] | null; // from M2 (§6.2, §4.5)
}

// PROTOCOL.md §4.7 bench results as the server stores them (§7.2 GET /api/cameras/{cam}/benchmarks)
export interface BenchResult {
  resolution?: number[] | null;
  fps_target?: number | null;
  pipeline?: string | null;
  full_scan_every?: number | null;
  aruco3?: boolean | null;
  duration_s?: number | null;
  fps?: number | null;
  cap_to_sent_ms?: Pct | null;
  detect_full_ms?: Pct | null;
  detect_roi_ms?: Pct | null;
  cpu_app_pct?: number | null;
  headroom_start?: number | null;
  headroom_end?: number | null;
  markers_seen?: number | null;
}

export interface BenchRun {
  v: number;
  t: "bench";
  cam: number;
  run_id: string;
  results: BenchResult[];
  received_wall_ms: number;
}

export interface CameraCaps {
  sdk: number;
  cpu: { cores: number; max_khz: number[] };
  camera: { id: string; hw_level: string; yuv_sizes: number[][]; [k: string]: unknown };
}

export interface TagState {
  id: number;
  role: string;
  label: string | null;
  seen: boolean;
  cams: number[];
  x_mm: number | null;
  y_mm: number | null;
  heading_deg: number | null;
  age_ms: number | null;
  size_mm: number | null;
  size_warn: boolean;
  placed: boolean | null;
}

export type CarLinkStateName = "disconnected" | "connecting" | "connected" | "degraded" | "error";

export interface CarPose {
  x_mm: number;
  y_mm: number;
  heading_deg: number;
  age_ms: number;
  fresh: boolean;
  at_node: number | null;
  usable_for_control?: boolean;
}

export interface CarState {
  name: string;
  tag: number | null;
  color: string;
  link: { state: CarLinkStateName; rtt_ms: number | null; fw: string | null };
  pose: CarPose | null;
  motion: { left: number; right: number; ttl_ms: number } | null;
  stopped_reason: string | null;
  battery: "not_measured";
}

export interface StateMsg {
  v: number;
  t: "state";
  seq: number;
  wall_ms: number;
  system: SystemState;
  cameras: CameraState[];
  tags: TagState[];
  cars: CarState[];
}

export interface EventRecord {
  id: number;
  wall_ms: number;
  mono_ms: number;
  type: string;
  car: string | null;
  operator: string | null;
  key: string | null;
  value: string | null;
  prev: string | null;
  facts: Record<string, unknown>;
  reason: string;
}

export interface EventMsg {
  v: number;
  t: "event";
  event: EventRecord;
}

export type ServerMsg = HelloMsg | StateMsg | EventMsg;

// ---------------------------------------------------------------------------------------------- §7.4 tags

export type Role = "car" | "node" | "anchor" | "obstacle" | "ignore";
export type StationKind = "pickup" | "dropoff" | "home" | "charging" | "custom";

export interface Station {
  name: string;
  kind: StationKind;
}

export interface TagPose {
  x_mm: number;
  y_mm: number;
  yaw_deg: number;
}

/** One entry of GET /api/tags: the stored document (only the fields of its role) plus live fields. */
export interface TagEntry {
  id: number;
  role: Role | "unassigned";
  size_mm: number | null; // printed size
  label: string | null;
  version: number;
  updated_wall_ms: number | null;
  updated_by: string | null;
  car?: string;
  offset_mm?: number[] | null;
  heading_offset_deg?: number | null;
  origin?: boolean;
  pose?: TagPose | null;
  grid?: number[] | null;
  station?: Station | null;
  radius_mm?: number | null;
  placed: boolean | null;
  seen: boolean;
  cams: number[];
  x_mm: number | null;
  y_mm: number | null;
  heading_deg: number | null;
  age_ms: number | null;
  measured_size_mm: number | null;
  size_warn: boolean;
}

export interface TagList {
  registry_version: number;
  tags: TagEntry[];
}

/** What PUT /api/tags/{id} and the batch endpoint take as the document (no live fields). */
export interface TagBody {
  role: Role;
  size_mm?: number | null;
  label?: string | null;
  car?: string;
  offset_mm?: number[] | null;
  heading_offset_deg?: number | null;
  origin?: boolean;
  pose?: TagPose | null;
  grid?: number[] | null;
  station?: Station | null;
  radius_mm?: number | null;
}

export interface BatchChange {
  id: number;
  doc: TagBody | null;
  expected_version: number;
}

export interface Suggestion {
  grid_rotation_deg: number;
  spacing_mm: number;
  suggestions: { id: number; grid: number[] }[];
  conflicts: { ids: number[]; grid: number[] }[];
}

// ---------------------------------------------------------------------------------------------- §7.5 layout

export type NodeStateName = "ok" | "unmeasured" | "blocked" | "moved" | "outside_view";

export interface LayoutNode {
  id: number;
  grid: number[];
  x_mm: number | null;
  y_mm: number | null;
  station: Station | null;
  state: NodeStateName;
  blocked_by: string | null;
}

export interface LayoutEdge {
  a: number;
  b: number;
  length_mm: number | null;
  bearing_deg: number | null;
  angle_error_deg: number | null;
  state: "ok" | "blocked";
  blocked_by: string | null;
}

export interface LayoutWarning {
  code: "edge_angle" | "edge_short" | "node_outside_view" | "spacing_mismatch" | "isolated_node";
  ids: number[];
  message: string;
}

export interface Footprint {
  cam: number;
  polygon_mm: number[][];
}

export interface Layout {
  version: number;
  measured_wall_ms: number | null;
  grid_rotation_deg: number;
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  warnings: LayoutWarning[];
  footprints: Footprint[];
}

// ---------------------------------------------------------------------------------------------- other REST

export interface VenuePreset {
  name: string;
  description: string;
  builtin: boolean;
  tag_count: number;
  has_layout: boolean;
  has_camera: boolean;
}

export interface ApplyResult {
  registry_version: number;
  layout_version: number;
  check: { matched: number[]; moved: { id: number; distance_mm: number }[]; missing: number[] };
}

export interface ConfigSummary {
  cars: { name: string; priority: number; footprint_mm: number[]; color: string; transport: string }[];
  pose: { stale_ms: number };
  markers: { dictionary: string; default_size_mm: number };
  roles: Role[];
  station_kinds: StationKind[];
  layout_rules: { validation: Record<string, number>; tracking: Record<string, number> };
}

export interface Pairing {
  pair_code: string;
  devices: { device_id: string; cam: number; model: string | null; last_seen_wall_ms: number | null }[];
}

export interface EventsResponse {
  events: EventRecord[];
  last_id: number;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> };
}
