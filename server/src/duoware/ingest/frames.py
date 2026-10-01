"""Marker-frame ingest shared by both transports (PROTOCOL.md §2 server rules, §3.2 sync requests).

`FrameIngest.handle` takes one message body (a UDP datagram, or one TCP message after de-framing) and applies the
§2 rules in their table order: it counts every rejection on the camera it belongs to (or globally when no camera can
be identified) and logs each cause at most once per minute. Accepted frames go to a `FrameSink`. It also runs one sync
task per live session that sends `sync` requests through `session.reply`, which every accepted frame sets: UDP's reply
is `sendto` the frame's source, TCP's is a framed write on that connection.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
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

Reply = Callable[[bytes], None]


class FrameSink(Protocol):
    def on_frame(self, session: Session, frame: Frame, recv_ns: int) -> None: ...


@dataclass(frozen=True, slots=True)
class Handled:
    """What `FrameIngest.handle` did with one message."""

    session: Session | None    # the live session the message belongs to (right `sid`, `cam` and source IP), else None
    rejected: str | None       # the `rx_*` counter a rejection was counted on (`rx_late` included), else None
    frame_accepted: bool = False


class FrameIngest:
    def __init__(self, sessions: PhoneSessions, sink: FrameSink, clock: Clock, cfg: ClockSyncCfg) -> None:
        self.sessions, self.sink, self.clock, self.cfg = sessions, sink, clock, cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._last_logged: dict[tuple[str, str], int] = {}
        sessions.on_opened.append(self.attach)
        sessions.on_closed.append(self.detach)

    # ------------------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Binds to the running loop and starts the sync task of every live session (idempotent)."""
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        for s in self.sessions.live():
            self.attach(s)

    def stop(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()
        self._loop = None

    # ------------------------------------------------------------------------------ receiving

    def reject(self, cause: str, sess: Session | None, detail: str = "") -> None:
        """Counts one rejected message on `sess` (or globally) and logs the cause at most once a minute."""
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
            log.warning("%s: dropped a message (%s) %s", who, cause, detail)

    def attribute(self, data: bytes) -> Session | None:
        """Best effort: which session does an unparseable message belong to (by its `sid`)?"""
        try:
            sid = json.loads(data).get("sid")
        except (ValueError, AttributeError, UnicodeDecodeError):
            return None
        return self.sessions.by_sid(sid) if isinstance(sid, str) else None

    def handle(self, data: bytes, peer_ip: str, reply: Reply, transport: str, recv_ns: int,
               known: Session | None = None) -> Handled:
        """One message body. `recv_ns` is the arrival time, stamped by the transport before anything else. `known` is
        the session the transport already ties the bytes to (an adopted TCP connection), used to count an unparseable
        message on its camera (PROTOCOL.md §2: per camera whenever the camera can be identified)."""
        try:
            msg = parse_datagram(data)
        except ProtocolError as e:
            cause = {"version": "rx_version"}.get(e.code, "rx_bad")
            self.reject(cause, self.attribute(data) or known, e.detail)
            return Handled(None, cause)
        sess = self.sessions.by_sid(msg.sid)
        if sess is None or sess.peer_ip != peer_ip or sess.cam != msg.cam:
            self.reject("rx_unauth", sess if sess is not None and sess.cam == msg.cam else None, f"from {peer_ip}")
            return Handled(None, "rx_unauth")
        if isinstance(msg, SyncReply):
            self._on_sync_reply(sess, msg, recv_ns)
            return Handled(sess, None)
        return self._on_frame(sess, msg, reply, transport, recv_ns)

    def _on_frame(self, sess: Session, f: Frame, reply: Reply, transport: str, recv_ns: int) -> Handled:
        if sess.last_seq is not None and f.seq <= sess.last_seq:
            self.reject("rx_late", sess, f"seq {f.seq} after {sess.last_seq}")
            return Handled(sess, "rx_late")
        if sess.last_seq is not None:
            sess.stats.dropped += f.seq - sess.last_seq - 1     # PROTOCOL.md §2: seq jumps by k -> dropped += k - 1
        sess.last_seq, sess.last_frame_ns = f.seq, recv_ns
        sess.reply, sess.transport = reply, transport
        sess.stats.rx_bad_marker += f.bad_markers
        server_sent = sess.clock_sync.to_server_ns(f.sent_ns, recv_ns) if sess.clock_sync.ok(recv_ns) else None
        sess.link_stats.add(f.seq, recv_ns, None if server_sent is None else (recv_ns - server_sent) / 1e6)
        sess.stats.add_frame(recv_ns)
        self.sink.on_frame(sess, f, recv_ns)
        return Handled(sess, None, frame_accepted=True)

    def _on_sync_reply(self, sess: Session, r: SyncReply, t4: int) -> None:
        t1 = sess.sync_sent.pop(r.n, None)
        if t1 is None or t1 != r.t1 or t4 - t1 > SYNC_REPLY_MAX_AGE_S * NS_PER_S:
            sess.clock_sync.note_rejected(t4)
            return
        sess.clock_sync.add(r.t1, r.t2, r.t3, t4)

    # ------------------------------------------------------------------------------ sending sync requests

    def _on_loop(self, fn, *args) -> None:
        """Sessions may be opened from another thread (a test, a worker): run on the ingest's loop."""
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if self._loop is None:
            return
        if here is self._loop:
            fn(*args)
        else:
            self._loop.call_soon_threadsafe(fn, *args)

    def attach(self, sess: Session) -> None:
        self._on_loop(self._start_sync, sess)

    def _start_sync(self, sess: Session) -> None:
        if sess.sid not in self._tasks:
            self._tasks[sess.sid] = asyncio.get_running_loop().create_task(self._sync_loop(sess))

    def detach(self, sess: Session) -> None:
        self._on_loop(self._stop_sync, sess)

    def _stop_sync(self, sess: Session) -> None:
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
                if sess.reply is not None:
                    sess.sync_n += 1
                    sess.sync_sent = {n: t for n, t in sess.sync_sent.items()
                                      if now - t <= SYNC_REPLY_MAX_AGE_S * NS_PER_S}
                    sess.sync_sent[sess.sync_n] = now
                    try:
                        sess.reply(encode_sync(SyncRequest(sess.sid, sess.sync_n, now)))
                    except Exception as e:                      # a closing socket must not end the sync task
                        log.debug("cam %d: sync request not sent (%s)", sess.cam, e)
                await asyncio.sleep((self.cfg.fast_interval_ms if fast else self.cfg.interval_ms) / 1000)
        except asyncio.CancelledError:
            pass
