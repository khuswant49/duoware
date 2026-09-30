"""The simulated phone's TCP frames connection (PROTOCOL.md §2.1, connection mode `wired_adb`).

Frames and sync share one connection: 4-byte big-endian length + JSON in both directions. The phone writes a frame only
when the previous write has fully drained (otherwise it drops the frame and counts `send_dropped`), answers `sync` on
the same connection, and reconnects 1 s after the connection closes while its WebSocket session is live.
"""

import asyncio
import logging
import socket
from collections.abc import Callable

from duoware.protocol import ProtocolError
from duoware.protocol.phone import SyncReply, TcpDeframer, encode_sync_r, encode_tcp, parse_sync_request

log = logging.getLogger(__name__)

RECONNECT_S = 1.0           # PROTOCOL.md §2.1: the phone reconnects after 1 s while its session is live
READ_BYTES = 65536
SEND_BUFFER_BYTES = 16384   # PROTOCOL.md §2.1: small send buffer (16 KB)


class SimTcpLink:
    def __init__(self, host: str, port: int, sid: str, cam: int, phone_now: Callable[[], int]) -> None:
        self.host, self.port, self.sid, self.cam, self._phone_now = host, port, sid, cam, phone_now
        self.send_dropped = 0
        self.connects = 0
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def send(self, message: bytes) -> bool:
        """One frame. False (and `send_dropped` + 1) when there is no connection or the last write has not drained."""
        w = self._writer
        if w is None or w.is_closing() or w.transport.get_write_buffer_size() > 0:
            self.send_dropped += 1
            return False
        w.write(encode_tcp(message))
        return True

    async def _run(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError:
                await asyncio.sleep(RECONNECT_S)
                continue
            sock = writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SEND_BUFFER_BYTES)
            self._writer = writer
            self.connects += 1
            try:
                await self._read_loop(reader, writer)
            except (ConnectionError, OSError):
                pass
            finally:
                self._writer = None
                writer.close()
            await asyncio.sleep(RECONNECT_S)

    async def _read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answers every `sync` request at once; nothing else arrives on this connection."""
        deframer = TcpDeframer()
        while True:
            data = await reader.read(READ_BYTES)
            t2 = self._phone_now()                          # stamp as early as possible
            if not data:
                return
            try:
                bodies = deframer.feed(data)
            except ProtocolError:
                return
            for body in bodies:
                try:
                    req = parse_sync_request(body)
                except ProtocolError:
                    continue
                if req.sid != self.sid:
                    continue
                t3 = self._phone_now()                      # ... and t3 as late as possible
                writer.write(encode_tcp(encode_sync_r(SyncReply(self.cam, self.sid, req.n, req.t1, t2, t3))))
