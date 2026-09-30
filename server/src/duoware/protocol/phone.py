"""Phone <-> server datagram messages (PROTOCOL.md §2 frames, §2.1 TCP framing, §3 clock sync,
§4.2 beacon, §4.6 preview). The WebSocket session messages (§4.3-4.7) live in `phone_session`; this
module re-exports them so callers can import everything phone-related from here."""

import json
import struct
from dataclasses import dataclass, field

from duoware import PROTOCOL_VERSION
from duoware.protocol import ProtocolError
from duoware.protocol._read import Obj, bad, is_int, is_num
from duoware.protocol.phone_session import (  # noqa: F401
    Bench, BenchResult, CameraCaps, CameraSettings, CpuInfo, Hello, Link, PerfState, PhoneError,
    PreviewSettings, Status, StageStats, ThermalPolicy, ThermalState, Tracking, Welcome, WifiInfo,
    encode_error, encode_hello, encode_settings, encode_status, encode_welcome, parse_ws_message,
)

MAX_DATAGRAM_BYTES = 8192       # PROTOCOL.md §2: hard maximum message size
MAX_PREVIEW_BYTES = 512 * 1024  # PROTOCOL.md §4.6: the server drops larger previews
MARKER_ID_MAX = 49              # PROTOCOL.md §1: DICT_4X4_50 has IDs 0-49
MARKER_BOUNDS_FACTOR = 2        # PROTOCOL.md §2: corners must lie in [-w, 2w] x [-h, 2h]
PREVIEW_MAGIC = b"DWP1"         # PROTOCOL.md §4.6
PREVIEW_HEADER = struct.Struct(">4sQHH")  # magic, cap_ns, width, height (big-endian)
TCP_LEN = struct.Struct(">I")   # PROTOCOL.md §2.1: 4-byte big-endian length prefix


@dataclass(frozen=True, slots=True)
class Marker:
    id: int
    corners: tuple[float, float, float, float, float, float, float, float]  # x0 y0 x1 y1 x2 y2 x3 y3, px

    @property
    def center_y(self) -> float:
        return (self.corners[1] + self.corners[3] + self.corners[5] + self.corners[7]) / 4.0


@dataclass(frozen=True, slots=True)
class Frame:
    cam: int
    sid: str
    seq: int
    cap_ns: int
    exp_ns: int
    skew_ns: int
    avail_ns: int
    sent_ns: int
    w: int
    h: int
    scan: str                      # "full" | "roi"
    searched: tuple[int, ...]
    markers: tuple[Marker, ...]
    bad_markers: int = 0           # markers dropped by the §2 validation rules (not part of the wire format)


@dataclass(frozen=True, slots=True)
class SyncRequest:
    sid: str
    n: int
    t1: int


@dataclass(frozen=True, slots=True)
class SyncReply:
    cam: int
    sid: str
    n: int
    t1: int
    t2: int
    t3: int


@dataclass(frozen=True, slots=True)
class Beacon:
    server_id: str
    name: str
    host: str
    http_port: int
    frames_port: int
    frames_tcp_port: int


@dataclass(frozen=True, slots=True)
class PreviewHeader:
    cap_ns: int
    w: int
    h: int


# ----------------------------------------------------------------------------------------- parsing


def _check_version(o: Obj) -> None:
    if not o.has("v") or not is_int(o.d["v"]):
        raise bad("v: expected int")
    if o.d["v"] != PROTOCOL_VERSION:
        raise ProtocolError("version", f"v={o.d['v']}")


def _parse_marker(entry: object, w: int, h: int) -> Marker | None:
    """None = this marker breaks a §2 rule (counted as rx_bad_marker)."""
    if not isinstance(entry, list) or len(entry) != 9 or not is_int(entry[0]):
        return None
    if not 0 <= entry[0] <= MARKER_ID_MAX or not all(is_num(v) for v in entry[1:]):
        return None
    xs, ys = entry[1::2], entry[2::2]
    lo_x, hi_x = -w * (MARKER_BOUNDS_FACTOR - 1), w * MARKER_BOUNDS_FACTOR
    lo_y, hi_y = -h * (MARKER_BOUNDS_FACTOR - 1), h * MARKER_BOUNDS_FACTOR
    if not (all(lo_x <= x <= hi_x for x in xs) and all(lo_y <= y <= hi_y for y in ys)):
        return None
    return Marker(entry[0], tuple(float(v) for v in entry[1:]))  # type: ignore[arg-type]


def _parse_frame(o: Obj) -> Frame:
    w, h = o.int("w"), o.int("h")
    scan = o.str("scan")
    if scan not in ("full", "roi"):
        raise bad("scan: expected 'full' or 'roi'")
    markers, n_bad = [], 0
    for entry in o.list("m"):
        mk = _parse_marker(entry, w, h)
        if mk is None:
            n_bad += 1
        else:
            markers.append(mk)
    return Frame(o.int("cam"), o.str("sid"), o.int("seq"), o.int("cap_ns"), o.int("exp_ns"), o.int("skew_ns"),
                 o.int("avail_ns"), o.int("sent_ns"), w, h, scan, tuple(o.int_list("searched")),
                 tuple(markers), n_bad)


def parse_datagram(data: bytes) -> Frame | SyncReply:
    """One UDP datagram (or one TCP message body): a `frame` or a `sync_r`. Raises `ProtocolError` with
    code `bad`, `version` or `too_big` (PROTOCOL.md §2 error table)."""
    if len(data) > MAX_DATAGRAM_BYTES:
        raise ProtocolError("too_big", f"{len(data)} bytes")
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise bad("not JSON") from e
    o = Obj(obj)
    _check_version(o)
    t = o.str("t")
    if t == "frame":
        return _parse_frame(o)
    if t == "sync_r":
        return SyncReply(o.int("cam"), o.str("sid"), o.int("n"), o.int("t1"), o.int("t2"), o.int("t3"))
    raise bad(f"t: unexpected message type {t!r}")


def marker_capture_ns(frame: Frame, marker: Marker) -> int:
    """PROTOCOL.md §2: cap_ns + exp_ns / 2 + skew_ns x (cy / h), phone sync clock."""
    return int(frame.cap_ns + frame.exp_ns / 2 + frame.skew_ns * (marker.center_y / frame.h))


def parse_preview(data: bytes) -> tuple[PreviewHeader, bytes]:
    if len(data) > MAX_PREVIEW_BYTES:
        raise ProtocolError("too_big", f"{len(data)} bytes")
    if len(data) <= PREVIEW_HEADER.size:
        raise bad("preview too short")
    magic, cap_ns, w, h = PREVIEW_HEADER.unpack_from(data)
    if magic != PREVIEW_MAGIC or w == 0 or h == 0:
        raise bad("preview header")
    return PreviewHeader(cap_ns, w, h), data[PREVIEW_HEADER.size:]


# ----------------------------------------------------------------------------------------- encoding


def _dumps(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).encode()


def _round_px(v: float) -> float:
    return round(v, 1)


def encode_frame(f: Frame) -> bytes:
    m = [[mk.id, *(_round_px(c) for c in mk.corners)] for mk in f.markers]
    return _dumps({"v": PROTOCOL_VERSION, "t": "frame", "cam": f.cam, "sid": f.sid, "seq": f.seq, "cap_ns": f.cap_ns,
                   "exp_ns": f.exp_ns, "skew_ns": f.skew_ns, "avail_ns": f.avail_ns, "sent_ns": f.sent_ns,
                   "w": f.w, "h": f.h, "scan": f.scan, "searched": list(f.searched), "m": m})


def encode_sync(req: SyncRequest) -> bytes:
    return _dumps({"v": PROTOCOL_VERSION, "t": "sync", "sid": req.sid, "n": req.n, "t1": req.t1})


def encode_sync_r(r: SyncReply) -> bytes:
    return _dumps({"v": PROTOCOL_VERSION, "t": "sync_r", "cam": r.cam, "sid": r.sid, "n": r.n, "t1": r.t1,
                   "t2": r.t2, "t3": r.t3})


def encode_beacon(b: Beacon) -> bytes:
    return _dumps({"v": PROTOCOL_VERSION, "t": "beacon", "server_id": b.server_id, "name": b.name, "host": b.host,
                   "http_port": b.http_port, "frames_port": b.frames_port, "frames_tcp_port": b.frames_tcp_port})


def parse_sync_request(data: bytes) -> SyncRequest:
    """Used by the simulated phone."""
    o = Obj(_loads(data))
    _check_version(o)
    if o.str("t") != "sync":
        raise bad("expected a sync request")
    return SyncRequest(o.str("sid"), o.int("n"), o.int("t1"))


def parse_beacon(data: bytes) -> Beacon:
    """Used by the simulated phone. A beacon with another `v` raises `ProtocolError("version")`."""
    o = Obj(_loads(data))
    _check_version(o)
    if o.str("t") != "beacon":
        raise bad("expected a beacon")
    return Beacon(o.str("server_id"), o.str("name"), o.str("host"), o.int("http_port"), o.int("frames_port"),
                  o.int("frames_tcp_port"))


def _loads(data: bytes | str) -> object:
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise bad("not JSON") from e


def encode_preview(cap_ns: int, w: int, h: int, jpeg: bytes) -> bytes:
    return PREVIEW_HEADER.pack(PREVIEW_MAGIC, cap_ns, w, h) + jpeg


# ----------------------------------------------------------------------------------------- TCP framing


def encode_tcp(message: bytes) -> bytes:
    """PROTOCOL.md §2.1: 4-byte big-endian length N (1..8192), then N bytes."""
    if not 1 <= len(message) <= MAX_DATAGRAM_BYTES:
        raise ProtocolError("bad", f"message length {len(message)} outside 1..{MAX_DATAGRAM_BYTES}")
    return TCP_LEN.pack(len(message)) + message


@dataclass
class TcpDeframer:
    """Splits a TCP byte stream into messages. A bad length cannot be resynchronised: the caller closes."""

    _buf: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out: list[bytes] = []
        while len(self._buf) >= TCP_LEN.size:
            (n,) = TCP_LEN.unpack_from(self._buf)
            if n == 0 or n > MAX_DATAGRAM_BYTES:
                raise bad(f"TCP message length {n}")
            if len(self._buf) < TCP_LEN.size + n:
                break
            out.append(bytes(self._buf[TCP_LEN.size:TCP_LEN.size + n]))
            del self._buf[:TCP_LEN.size + n]
        return out
