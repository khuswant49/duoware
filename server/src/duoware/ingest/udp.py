"""UDP endpoint for marker frames and clock sync (PROTOCOL.md §2, §3).

Applies the §2 server rules in their table order, counts every rejection on the camera it belongs to
(or globally when no camera can be identified) and logs each cause at most once per minute. Accepted frames go to a
`FrameSink`; sync requests are sent from one asyncio task per session to the source of its latest frame.
"""

import asyncio
import json
import logging
from typing import Protocol

from duoware.clock import Clock
from duoware.ingest.sessions import PhoneSessions, Session
from duoware.protocol import ProtocolError
from duoware.protocol.phone import Frame, SyncReply, SyncRequest, encode_sync, parse_datagram
from duoware.settings import ClockSyncCfg

log = logging.getLogger(__name__)

SYNC_REPLY_MAX_AGE_S = 2      # PROTOCOL.md §3.3: a reply whose n was not sent in the last 2 s is discarded
LOG_EVERY_S = 60              # PROTOCOL.md §2: each rejection cause is logged at most once per minute per camera
NS_PER_S = 1_000_000_000


class FrameSink(Protocol):
    def on_frame(self, session: Session, frame: Frame, recv_ns: int) -> None: ...


class FramesEndpoint(asyncio.DatagramProtocol):
    def __init__(self, sessions: PhoneSessions, sink: FrameSink, clock: Clock, cfg: ClockSyncCfg) -> None:
        self.sessions, self.sink, self.clock, self.cfg = sessions, sink, clock, cfg
        self.transport: asyncio.DatagramTransport | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_logged: dict[tuple[str, str], int] = {}
        sessions.on_opened.append(self.attach)
        sessions.on_closed.append(self.detach)

    # ------------------------------------------------------------------------------ asyncio

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport                              # type: ignore[assignment]
        for s in self.sessions.live():
            self.attach(s)

    def connection_lost(self, exc: Exception | None) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()

    def error_received(self, exc: Exception) -> None:
        log.warning("frames socket error: %s", exc)

    # ------------------------------------------------------------------------------ receiving

    def _reject(self, cause: str, sess: Session | None, detail: str = "") -> None:
        if sess is None:
            self.sessions.unknown_rx += 1
            who = "unknown"
        else:
            who = f"cam {sess.cam}"
            setattr(sess.stats, cause, getattr(sess.stats, cause) + 1)
        now = self.clock.mono_ns()
        key = (who, cause)
        if now - self._last_logged.get(key, -LOG_EVERY_S * NS_PER_S) >= LOG_EVERY_S * NS_PER_S:
            self._last_logged[key] = now
            log.warning("%s: dropped a datagram (%s) %s", who, cause, detail)

    def _attribute(self, data: bytes) -> Session | None:
        """Best effort: which session does an unparseable datagram belong to (by its `sid`)?"""
        try:
            sid = json.loads(data).get("sid")
        except (ValueError, AttributeError, UnicodeDecodeError):
            return None
        return self.sessions.by_sid(sid) if isinstance(sid, str) else None

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        recv_ns = self.clock.mono_ns()                          # stamp first: this is the frame's arrival time
        try:
            msg = parse_datagram(data)
        except ProtocolError as e:
            cause = {"version": "rx_version"}.get(e.code, "rx_bad")
            self._reject(cause, self._attribute(data), e.detail)
            return
        sess = self.sessions.by_sid(msg.sid)
        if sess is None or sess.peer_ip != addr[0] or sess.cam != msg.cam:
            self._reject("rx_unauth", sess if sess is not None and sess.cam == msg.cam else None,
                         f"from {addr[0]}")
            return
        if isinstance(msg, SyncReply):
            self._on_sync_reply(sess, msg, recv_ns)
        else:
            self._on_frame(sess, msg, addr, recv_ns)

    def _on_frame(self, sess: Session, f: Frame, addr: tuple, recv_ns: int) -> None:
        if sess.last_seq is not None and f.seq <= sess.last_seq:
            self._reject("rx_late", sess, f"seq {f.seq} after {sess.last_seq}")
            return
        if sess.last_seq is not None:
            sess.stats.dropped += f.seq - sess.last_seq - 1     # PROTOCOL.md §2: seq jumps by k -> dropped += k - 1
        sess.last_seq = f.seq
        sess.frame_addr = (addr[0], addr[1])
        sess.stats.rx_bad_marker += f.bad_markers
        server_sent = sess.clock_sync.to_server_ns(f.sent_ns, recv_ns) if sess.clock_sync.ok(recv_ns) else None
        sess.link_stats.add(f.seq, recv_ns, None if server_sent is None else (recv_ns - server_sent) / 1e6)
        sess.stats.add_frame(recv_ns)
        self.sink.on_frame(sess, f, recv_ns)

    def _on_sync_reply(self, sess: Session, r: SyncReply, t4: int) -> None:
        t1 = sess.sync_sent.pop(r.n, None)
        if t1 is None or t1 != r.t1 or t4 - t1 > SYNC_REPLY_MAX_AGE_S * NS_PER_S:
            return
        sess.clock_sync.add(r.t1, r.t2, r.t3, t4)

    # ------------------------------------------------------------------------------ sending sync requests

    def attach(self, sess: Session) -> None:
        if self.transport is not None and sess.sid not in self._tasks:
            self._tasks[sess.sid] = asyncio.get_running_loop().create_task(self._sync_loop(sess))

    def detach(self, sess: Session) -> None:
        t = self._tasks.pop(sess.sid, None)
        if t is not None:
            t.cancel()

    async def _sync_loop(self, sess: Session) -> None:
        """PROTOCOL.md §3.2: every `fast_interval_ms` for `fast_period_s`, then every `interval_ms`."""
        opened = self.clock.mono_ns()
        try:
            while self.sessions.by_sid(sess.sid) is not None:
                now = self.clock.mono_ns()
                fast = now - opened < self.cfg.fast_period_s * NS_PER_S
                if sess.frame_addr is not None and self.transport is not None:
                    sess.sync_n += 1
                    sess.sync_sent = {n: t for n, t in sess.sync_sent.items()
                                      if now - t <= SYNC_REPLY_MAX_AGE_S * NS_PER_S}
                    sess.sync_sent[sess.sync_n] = now
                    self.transport.sendto(encode_sync(SyncRequest(sess.sid, sess.sync_n, now)), sess.frame_addr)
                await asyncio.sleep((self.cfg.fast_interval_ms if fast else self.cfg.interval_ms) / 1000)
        except asyncio.CancelledError:
            pass
