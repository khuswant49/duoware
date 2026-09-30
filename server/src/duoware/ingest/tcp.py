"""TCP listener for marker frames and clock sync in connection mode `wired_adb` (PROTOCOL.md §2.1).

`adb reverse` forwards TCP only, so frames and sync share one TCP connection per session: each message is a 4-byte
big-endian length `N` (1..8192) and then `N` bytes of JSON, in both directions. Messages go to the same `FrameIngest`
as UDP datagrams; this module adds only what a byte stream needs:

- the first message must belong to a live session and arrive within 5 s of connecting, else the connection is closed;
- `N` = 0, `N` > 8192 or bad JSON is counted `rx_bad` and closes the connection (a stream cannot be resynchronised);
  so does a peer that is not the session's WebSocket peer (`rx_unauth`);
- one connection per `sid`: a newer one replaces the older; the connection closes when its session ends.
"""

import asyncio
import logging
import socket

from duoware.clock import Clock
from duoware.ingest.frames import FrameIngest
from duoware.ingest.sessions import PhoneSessions, Session
from duoware.protocol import ProtocolError
from duoware.protocol.phone import TcpDeframer, encode_tcp

log = logging.getLogger(__name__)

FIRST_MESSAGE_TIMEOUT_S = 5     # PROTOCOL.md §2.1: the first message must arrive within 5 s of connecting
READ_BYTES = 65536              # bytes requested from the socket per read (frames are at most 8192 + 4)
REPLY_BUFFER_MAX = 65536        # a `sync` request is skipped while this many bytes wait unsent (a stalled phone link)
CLOSING = frozenset({"rx_bad", "rx_unauth"})     # rejections that close the connection (PROTOCOL.md §2.1)


class _Connection:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.session: Session | None = None
        self.closed = False

    def reply(self, data: bytes) -> None:
        """Framed write of one server message (a `sync` request). Never blocks and never queues behind a stall."""
        if self.closed or self.writer.is_closing():
            return
        if self.writer.transport.get_write_buffer_size() > REPLY_BUFFER_MAX:
            return
        self.writer.write(encode_tcp(data))

    def close(self) -> None:
        self.closed = True
        if not self.writer.is_closing():
            self.writer.close()


class FramesTcpServer:
    def __init__(self, sessions: PhoneSessions, ingest: FrameIngest, clock: Clock) -> None:
        self.sessions, self.ingest, self.clock = sessions, ingest, clock
        self._server: asyncio.Server | None = None
        self._by_sid: dict[str, _Connection] = {}
        self._conns: set[_Connection] = set()
        sessions.on_closed.append(self._session_closed)

    async def start(self, host: str, port: int) -> int:
        """Listens on `host:port` (0 = any free port) and returns the bound port."""
        self._server = await asyncio.start_server(self._on_connection, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for c in list(self._conns):
            c.close()
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------------------ sessions

    def _session_closed(self, sess: Session) -> None:
        conn = self._by_sid.pop(sess.sid, None)
        if conn is not None:
            conn.close()

    def _adopt(self, sess: Session, conn: _Connection) -> None:
        """The connection's first valid message named `sess`: it becomes the session's connection."""
        conn.session = sess
        old = self._by_sid.get(sess.sid)
        self._by_sid[sess.sid] = conn
        if old is not None and old is not conn:
            old.close()                                         # PROTOCOL.md §2.1: a newer connection replaces the old

    # ------------------------------------------------------------------------------ one connection

    async def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else ""
        conn = _Connection(writer)
        self._conns.add(conn)
        deframer = TcpDeframer()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FIRST_MESSAGE_TIMEOUT_S
        try:
            while not conn.closed:
                timeout = None if conn.session is not None else max(0.0, deadline - loop.time())
                try:
                    data = await asyncio.wait_for(reader.read(READ_BYTES), timeout)
                except asyncio.TimeoutError:
                    log.info("tcp frames: no valid first message from %s within %d s: closing", peer_ip,
                             FIRST_MESSAGE_TIMEOUT_S)
                    break
                if not data:
                    break                                       # the peer closed
                recv_ns = self.clock.mono_ns()                  # stamp first: this is the messages' arrival time
                try:
                    messages = deframer.feed(data)
                except ProtocolError as e:
                    self.ingest.reject("rx_bad", conn.session, e.detail)
                    break
                for body in messages:
                    result = self.ingest.handle(body, peer_ip, conn.reply, "tcp", recv_ns)
                    if result.session is not None and conn.session is None:
                        self._adopt(result.session, conn)
                    if result.rejected in CLOSING:
                        conn.close()
                        break
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()
            self._conns.discard(conn)
            sess = conn.session
            if sess is not None:
                if self._by_sid.get(sess.sid) is conn:
                    del self._by_sid[sess.sid]
                if sess.reply == conn.reply:                    # stop sending sync requests into a dead connection
                    sess.reply = None
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
