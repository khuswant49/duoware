"""The simulated phone (DECISIONS.md D14): discovers the server by its beacon, pairs over `/ws/phone`, streams marker
frames over UDP, answers clock sync from its frames socket and sends `status` at 1 Hz. It uses the real codecs, so
the server code path is identical to the one a real phone takes. It models the phone's clock (offset and drift),
pipeline/detection/network delays, packet loss and ROI scanning (D32), and reports `null` for what it does not model.

Time: `clock` is the simulator's clock (server clock when run in the same process). The phone clock is
`clock * (1 + drift) + offset`; it is the "sync clock" of PROTOCOL.md §3.1.
"""

import asyncio
import json
import logging
import socket
import urllib.request
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from websockets.asyncio.client import ClientConnection, connect

from duoware.clock import Clock
from duoware.protocol import ProtocolError
from duoware.protocol._read import Obj
from duoware.protocol.phone import (
    CameraCaps, CameraSettings, CpuInfo, Frame, Hello, Link, Status, StageStats, SyncReply, encode_frame,
    encode_hello, encode_status, encode_sync_r, parse_beacon, parse_sync_request,
)
from duoware.settings import REPO_ROOT, load_env
from duoware.sim.camera_model import PinholeCamera
from duoware.sim.detector_model import DetectorModel
from duoware.sim.frame_builder import FrameBuilder
from duoware.sim.scenario import Scenario
from duoware.sim.tcp_link import SimTcpLink
from duoware.sim.world import SimWorld

log = logging.getLogger(__name__)

NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000
MIN_SEND_GAP_NS = 200_000          # frames leave the phone in order, at least this far apart
STATUS_PERIOD_S = 1.0              # PROTOCOL.md §4.5: status at 1 Hz
STAGE_WINDOW_S = 10.0              # PROTOCOL.md §4.5: stage timings are p50/p95 over the last 10 s
TRUTH_KEEP = 20000                 # frames of ground truth kept
RECONNECT_S = 1.0
PROBE_TIMEOUT_S = 1.0               # PROTOCOL.md §4.1 wired_adb: the `GET /api/beacon` probe times out after 1 s
ADB_INTERFACE = "lo"               # the loopback interface `adb reverse` connects through
APP_VERSION = "0.0.0-sim"
SIM_MODEL = "DUO-WARE simulator"
ISO = 800                          # reported sensitivity (the sim does not model noise from ISO)


@dataclass
class TruthRecord:
    seq: int
    t_cap_ns: int                  # start of exposure, server clock
    t_mid_ns: int                  # middle of the exposure
    poses: dict[str, tuple[float, float, float]]          # true rotation-centre poses at t_mid_ns
    scan: str
    net_delay_ns: int
    arrival_ns: int | None = None  # when the datagram really left for the server; None = dropped
    dropped: bool = False
    n_markers: int = 0


@dataclass
class _Session:
    ws: ClientConnection
    sid: str
    cam: int
    frames_addr: tuple[str, int]
    tasks: list[asyncio.Task] = field(default_factory=list)
    tcp: SimTcpLink | None = None          # `transport = "tcp"`: frames and sync on one TCP connection (§2.1)


class SimPhone:
    def __init__(self, scenario: Scenario, world: SimWorld, clock: Clock, pair_code: str | None = None,
                 rng: np.random.Generator | None = None) -> None:
        self.sc, self.world, self.clock = scenario, world, clock
        self.rng = rng or np.random.default_rng(0)
        cam = scenario.camera
        self.camera = PinholeCamera(cam.position_mm, cam.tilt_deg, cam.yaw_deg, cam.resolution, cam.hfov_deg, cam.lens_k1)
        self.detector = DetectorModel(scenario.phone_model, self.rng)
        self.builder = FrameBuilder(scenario, world, self.camera, self.detector)
        self.pair_code = pair_code or scenario.server.pair_code or load_env(REPO_ROOT / ".env").get("DUO_PAIR_CODE") or None
        self.app_mode = "tracking"
        self.use_tcp = scenario.phone_model.transport == "tcp"
        self.link_mode = "wired_adb" if self.use_tcp else scenario.phone_model.link_mode
        self.token: str | None = None
        self.settings: CameraSettings | None = None
        self.truth_log: dict[int, TruthRecord] = {}
        self.dropped_seqs: list[int] = []
        self.sessions_opened = 0
        self.session: _Session | None = None
        self.server_host = scenario.server.host
        self._transport: asyncio.DatagramTransport | None = None
        self._stop = False
        self._run_task: asyncio.Task | None = None
        self._recent_frames: deque[int] = deque()
        self._stages: deque[tuple[int, float, float, float, str]] = deque()     # (t, pipeline, detect, cap_to_sent, scan)
        self._last_markers = 0
        self._lost_rescans = 0
        self._force_full = False
        self._connected = asyncio.Event()
        self._switching = False

    # ------------------------------------------------------------------------------ clocks

    def phone_ns(self, server_ns: int) -> int:
        m = self.sc.phone_model
        return int(server_ns * (1 + m.clock_drift_ppm * 1e-6) + m.clock_offset_s * NS_PER_S)

    def _now_phone(self) -> int:
        return self.phone_ns(self.clock.mono_ns())

    # ------------------------------------------------------------------------------ discovery, pairing

    async def _discover(self) -> tuple[str, int, int]:
        """(host, http_port, frames_port): a beacon if one arrives in time, else the scenario's host and ports."""
        s = self.sc.server
        if s.discovery:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", s.beacon_port))
                sock.setblocking(False)
                loop = asyncio.get_running_loop()
                deadline = loop.time() + s.discovery_wait_s
                while loop.time() < deadline:
                    try:
                        data = await asyncio.wait_for(loop.sock_recv(sock, 2048), deadline - loop.time())
                        b = parse_beacon(data)
                        return b.host, b.http_port, b.frames_port
                    except (asyncio.TimeoutError, ProtocolError):
                        continue
            except OSError as e:
                log.warning("sim phone: cannot listen for the beacon (%s); using the configured host", e)
            finally:
                sock.close()
        return s.host, s.http_port, s.frames_port

    async def _probe_beacon(self) -> tuple[str, int, int, int]:
        """`wired_adb` discovery (PROTOCOL.md §4.1): `GET /api/beacon` on the configured host, which is 127.0.0.1 through
        `adb reverse`. (host, http_port, frames_port, frames_tcp_port); `OSError` when there is no usable answer."""
        s = self.sc.server
        url = f"http://{s.host}:{s.http_port}/api/beacon"

        def get() -> bytes:
            with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as r:        # noqa: S310 - fixed http URL
                return r.read()

        data = await asyncio.get_running_loop().run_in_executor(None, get)
        try:
            b = parse_beacon(data)
        except ProtocolError as e:
            raise OSError(f"bad beacon from {url}: {e}") from e
        return b.host, b.http_port, b.frames_port, b.frames_tcp_port

    def _hello(self) -> Hello:
        cam, m = self.sc.camera, self.sc.phone_model
        w, h = cam.resolution
        caps = CameraCaps("0", "FULL", ("MANUAL_SENSOR", "READ_SENSOR_SETTINGS"), "REALTIME", (10000, 500000000),
                          (100, 6400), ((int(cam.fps), int(cam.fps)),), ((w, h, cam.fps),), False, None, None, None, (w, h))
        link = Link(self.link_mode, ADB_INTERFACE if self.use_tcp else "sim0", "127.0.0.1", None)
        return Hello(cam.device_id, self.token, self.pair_code, APP_VERSION, SIM_MODEL, "sim", 0, link,
                     CpuInfo(1, (0,)), caps)

    async def connect(self) -> None:
        """Discovers the server, pairs (or reconnects with the stored token) and starts streaming."""
        if self.use_tcp:
            host, http_port, frames_port, tcp_port = await self._probe_beacon()
        else:
            (host, http_port, frames_port), tcp_port = await self._discover(), 0
        self.server_host = host
        ws = await connect(f"ws://{host}:{http_port}/ws/phone", max_size=None)
        await ws.send(encode_hello(self._hello()))
        msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if msg.get("t") != "welcome":
            await ws.close()
            raise PairingError(msg.get("code", "unknown"), msg.get("message", ""))
        self.token = msg.get("token") or self.token
        self.settings = CameraSettings.parse(Obj(msg["settings"]))
        if self._transport is None and not self.use_tcp:
            self._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: _FramesSocket(self), local_addr=("0.0.0.0", 0))
        sess = _Session(ws, msg["sid"], msg["cam"], (host, msg["frames_port"] or frames_port))
        if self.use_tcp:
            sess.tcp = SimTcpLink(host, msg.get("frames_tcp_port") or tcp_port, sess.sid, sess.cam, self._now_phone)
            sess.tcp.start()
        self.session = sess
        self._seq = 0
        self._frame_index = 0
        self._force_full = False
        self._session_start_ns = self.clock.mono_ns()
        self.sessions_opened += 1
        loop = asyncio.get_running_loop()
        sess.tasks = [loop.create_task(self._frame_loop(sess)), loop.create_task(self._status_loop(sess)),
                      loop.create_task(self._reader(sess))]
        self._connected.set()

    async def disconnect(self) -> None:
        sess, self.session = self.session, None
        self._connected.clear()
        if sess is None:
            return
        for t in sess.tasks:
            t.cancel()
        await asyncio.gather(*sess.tasks, return_exceptions=True)
        if sess.tcp is not None:
            await sess.tcp.close()
        try:
            await sess.ws.close()
        except Exception:
            pass

    async def switch_session(self, new_link_mode: str, gap_s: float = 0.0) -> None:
        """Test hook: end this session and open a new one (new `sid`) in another connection mode, with the stored
        token, after `gap_s` off the air (a real mode switch re-discovers the server, PROTOCOL.md §4.1)."""
        self._switching = True                       # keeps `run` from reconnecting on its own during the gap
        try:
            await self.disconnect()
            self.link_mode = new_link_mode
            if gap_s > 0:
                await asyncio.sleep(gap_s)
            await self.connect()
        finally:
            self._switching = False

    def set_app_mode(self, mode: str) -> None:
        self.app_mode = mode

    # ------------------------------------------------------------------------------ running

    async def run(self) -> None:
        """Connect, and reconnect after a dropped session, until stopped."""
        while not self._stop:
            try:
                if self._switching:
                    await asyncio.sleep(0.02)
                    continue
                if self.session is None:
                    await self.connect()
                await asyncio.wait([t for t in self.session.tasks], return_when=asyncio.FIRST_COMPLETED)   # type: ignore[union-attr]
                if self.session is not None and any(t.done() for t in self.session.tasks):
                    await self.disconnect()
            except PairingError as e:
                log.error("sim phone: pairing failed (%s): %s", e.code, e)
                await asyncio.sleep(RECONNECT_S)
            except (OSError, asyncio.TimeoutError) as e:
                log.info("sim phone: server not reachable (%s)", e)
                await asyncio.sleep(RECONNECT_S)
            except asyncio.CancelledError:
                raise

    def start(self) -> None:
        self._run_task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        self._stop = True
        if self._run_task:
            self._run_task.cancel()
            await asyncio.gather(self._run_task, return_exceptions=True)
        await self.disconnect()
        if self._transport:
            self._transport.close()
            self._transport = None

    async def wait_connected(self, timeout: float = 10.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    # ------------------------------------------------------------------------------ frames

    def _exposure_ns(self) -> int:
        return self.settings.exposure_ns if self.settings else int(self.sc.phone_model.exposure_ms * NS_PER_MS)

    def _is_burst(self, now_ns: int) -> bool:
        n = self.sc.network
        if n.burst_drop_every_s <= 0:
            return False
        t = (now_ns - self._session_start_ns) / NS_PER_S
        return t >= n.burst_drop_every_s and (t % n.burst_drop_every_s) < n.burst_drop_ms / 1000.0

    async def _frame_loop(self, sess: _Session) -> None:
        period = int(NS_PER_S / self.sc.camera.fps)
        t_cap = self.clock.mono_ns() + period
        last_sched = 0
        while True:
            pipe, det = self.detector.pipeline_ns(), self.detector.detect_ns()
            mean, spread = self.sc.network.delay_ms
            net = int((mean + self.rng.uniform(-spread, spread)) * NS_PER_MS)
            sched = max(t_cap + pipe + det + net, last_sched + MIN_SEND_GAP_NS)
            last_sched = sched
            await asyncio.sleep(max(0.0, (sched - self.clock.mono_ns()) / NS_PER_S))
            now = self.clock.mono_ns()
            await self._emit_frame(sess, t_cap, pipe, det, net, now)
            t_cap += period

    async def _emit_frame(self, sess: _Session, t_cap: int, pipe: int, det: int, net: int, now: int) -> None:
        st = self.settings
        exp = self._exposure_ns()
        every = max(1, st.tracking.full_scan_every) if st else 1
        full = self._frame_index % every == 0 or self._force_full or st is None
        tracked = set(st.tracking.track_ids) if st else set()
        markers = self.builder.detect(t_cap, exp, None if full else tracked)
        found = {m.id for m in markers}
        if full:
            self._force_full = False
        elif tracked - found:                                   # a tracked tag was not found in its window: rescan
            self._force_full = True
            self._lost_rescans += 1
        seq = self._seq
        self._seq += 1
        self._frame_index += 1
        mid = t_cap + exp // 2
        rec = TruthRecord(seq, t_cap, mid, self.builder.truth(mid), "full" if full else "roi", net, n_markers=len(markers))
        self.truth_log[seq] = rec
        if len(self.truth_log) > TRUTH_KEEP:
            for k in sorted(self.truth_log)[: TRUTH_KEEP // 2]:
                del self.truth_log[k]
        self._last_markers = len(markers)
        self._recent_frames.append(now)
        self._stages.append((now, pipe / NS_PER_MS, det / NS_PER_MS, (now - net - t_cap) / NS_PER_MS, rec.scan))
        # a TCP connection does not lose packets: the phone itself drops a frame it cannot write (send_dropped)
        drop = sess.tcp is None and (self.rng.random() < self.sc.network.drop_fraction or self._is_burst(now))
        if drop:
            rec.dropped = True
            self.dropped_seqs.append(seq)
            return
        rec.arrival_ns = now
        frame = Frame(sess.cam, sess.sid, seq, self.phone_ns(t_cap), exp, 0, self.phone_ns(t_cap + pipe),
                      self.phone_ns(now - net), *self.sc.camera.resolution, rec.scan,
                      () if full else tuple(sorted(tracked)), tuple(markers))
        if sess.tcp is not None:
            if not sess.tcp.send(encode_frame(frame)):
                rec.arrival_ns, rec.dropped = None, True
                self.dropped_seqs.append(seq)
        elif self._transport is not None:
            self._transport.sendto(encode_frame(frame), sess.frames_addr)

    # ------------------------------------------------------------------------------ UDP sync

    def on_datagram(self, data: bytes, addr: tuple) -> None:
        """A `sync` request from the server: answer at once from the frames socket (PROTOCOL.md §3.2)."""
        t2 = self._now_phone()                                   # stamp as early as possible
        try:
            req = parse_sync_request(data)
        except ProtocolError:
            return
        sess = self.session
        if sess is None or req.sid != sess.sid or self._transport is None:
            return
        t3 = self._now_phone()                                   # ... and t3 as late as possible
        self._transport.sendto(encode_sync_r(SyncReply(sess.cam, sess.sid, req.n, req.t1, t2, t3)), addr)

    # ------------------------------------------------------------------------------ status and settings

    def _stats(self, now: int) -> tuple[dict, dict]:
        horizon = now - int(STAGE_WINDOW_S * NS_PER_S)
        while self._stages and self._stages[0][0] < horizon:
            self._stages.popleft()
        while self._recent_frames and self._recent_frames[0] < now - NS_PER_S:
            self._recent_frames.popleft()

        def p(vals: list[float]) -> StageStats | None:
            return StageStats(float(np.percentile(vals, 50)), float(np.percentile(vals, 95))) if vals else None

        rows = list(self._stages)
        stages = {"pipeline": p([r[1] for r in rows]), "detect_full": p([r[2] for r in rows if r[4] == "full"]),
                  "detect_roi": p([r[2] for r in rows if r[4] == "roi"]), "send": None,
                  "cap_to_sent": p([r[3] for r in rows])}
        return stages, {"fps": float(len(self._recent_frames))}

    async def _status_loop(self, sess: _Session) -> None:
        cam = self.sc.camera
        while True:
            await asyncio.sleep(STATUS_PERIOD_S)
            every = self.settings.tracking.full_scan_every if self.settings else 1
            stages, rate = self._stats(self.clock.mono_ns())
            link = Link(self.link_mode, ADB_INTERFACE if self.use_tcp else "sim0", "127.0.0.1", None)
            s = Status(sess.cam, self.app_mode, cam.timestamp_clock, link, rate["fps"], cam.fps, tuple(cam.resolution),
                       stages, {"full_every": float(every), "rois_per_frame": None, "lost_rescans": float(self._lost_rescans)},
                       0, sess.tcp.send_dropped if sess.tcp is not None else 0, self._exposure_ns(), ISO, "locked", None, None, None, None, None, None, self._last_markers)
            try:
                await sess.ws.send(encode_status(s))
            except Exception:
                return

    async def _reader(self, sess: _Session) -> None:
        try:
            async for raw in sess.ws:
                if isinstance(raw, str):
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if msg.get("t") == "settings":
                        self.settings = CameraSettings.parse(Obj(msg))
                    elif msg.get("t") == "error":
                        log.warning("sim phone: server error %s: %s", msg.get("code"), msg.get("message"))
        except Exception:
            pass


class PairingError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class _FramesSocket(asyncio.DatagramProtocol):
    def __init__(self, phone: SimPhone) -> None:
        self.phone = phone

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.phone.on_datagram(data, addr)
