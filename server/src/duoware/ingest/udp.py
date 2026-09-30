"""UDP endpoint for marker frames and clock sync (PROTOCOL.md §2, §3).

Thin: a datagram is handed to `FrameIngest` (shared with the TCP listener, `ingest/tcp.py`) together with a reply
function that sends back to the datagram's source, which is where the session's `sync` requests go.
"""

import asyncio
import logging

from duoware.clock import Clock
from duoware.ingest.frames import FrameIngest, FrameSink, Reply
from duoware.ingest.sessions import PhoneSessions
from duoware.settings import ClockSyncCfg

log = logging.getLogger(__name__)

REPLY_CACHE_MAX = 64      # phones are few: this only bounds the cache if something sprays datagrams from many ports


class FramesEndpoint(asyncio.DatagramProtocol):
    def __init__(self, sessions: PhoneSessions, sink: FrameSink, clock: Clock, cfg: ClockSyncCfg,
                 ingest: FrameIngest | None = None) -> None:
        """`ingest` is shared with the TCP listener in the running server; tests may let the endpoint make its own."""
        self.sessions, self.clock = sessions, clock
        self._owns_ingest = ingest is None
        self.ingest = ingest or FrameIngest(sessions, sink, clock, cfg)
        self.transport: asyncio.DatagramTransport | None = None
        self._replies: dict[tuple, Reply] = {}

    # ------------------------------------------------------------------------------ asyncio

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport                              # type: ignore[assignment]
        self.ingest.start()

    def connection_lost(self, exc: Exception | None) -> None:
        if self._owns_ingest:
            self.ingest.stop()

    def error_received(self, exc: Exception) -> None:
        log.warning("frames socket error: %s", exc)

    # ------------------------------------------------------------------------------ receiving

    def _reply_to(self, addr: tuple) -> Reply:
        reply = self._replies.get(addr)
        if reply is None:
            if len(self._replies) >= REPLY_CACHE_MAX:
                self._replies.clear()
            transport = self.transport

            def reply(data: bytes, _addr: tuple = addr) -> None:
                if transport is not None:
                    transport.sendto(data, _addr)

            self._replies[addr] = reply
        return reply

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        recv_ns = self.clock.mono_ns()                          # stamp first: this is the frame's arrival time
        result = self.ingest.handle(data, addr[0], self._reply_to(addr), "udp", recv_ns)
        if result.frame_accepted and result.session is not None:
            result.session.frame_addr = (addr[0], addr[1])
