"""M2 step 3 / B3: the TCP frames listener (PROTOCOL.md §2.1) with real loopback connections."""

import asyncio
import json
import struct
import time

import pytest
from conftest import make_hello
from test_udp import PHONE_OFFSET_NS, Sink, frame

from duoware.clock import SystemClock
from duoware.ingest import tcp as tcp_module
from duoware.ingest.frames import FrameIngest
from duoware.ingest.sessions import Opened, PhoneSessions
from duoware.ingest.tcp import FramesTcpServer
from duoware.protocol.phone import SyncReply, TcpDeframer, encode_sync_r, encode_tcp, parse_sync_request


def framed(obj) -> bytes:
    return encode_tcp(obj if isinstance(obj, bytes) else json.dumps(obj).encode())


class Client:
    """A phone end of one TCP frames connection: sends framed messages, answers `sync` like a phone."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cam: int, sid: str,
                 answer_sync: bool = True) -> None:
        self.reader, self.writer, self.cam, self.sid, self.answer_sync = reader, writer, cam, sid, answer_sync
        self.sync_requests = []
        self.closed_by_server = asyncio.get_running_loop().create_future()
        self._task = asyncio.get_running_loop().create_task(self._read())

    async def _read(self) -> None:
        deframer = TcpDeframer()
        try:
            while True:
                data = await self.reader.read(65536)
                if not data:
                    break
                for body in deframer.feed(data):
                    req = parse_sync_request(body)
                    self.sync_requests.append(req)
                    if self.answer_sync:
                        t2 = time.monotonic_ns() + PHONE_OFFSET_NS
                        self.writer.write(framed(encode_sync_r(SyncReply(self.cam, self.sid, req.n, req.t1, t2,
                                                                         t2 + 10_000))))
        except (ConnectionError, OSError):
            pass
        finally:
            if not self.closed_by_server.done():
                self.closed_by_server.set_result(True)

    def send_raw(self, data: bytes) -> None:
        self.writer.write(data)

    def send(self, obj) -> None:
        self.writer.write(framed(obj))

    async def closed(self, timeout: float = 2.0) -> bool:
        try:
            await asyncio.wait_for(asyncio.shield(self.closed_by_server), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def close(self) -> None:
        self._task.cancel()
        self.writer.close()


class Rig:
    async def start(self, env, peer_ip: str = "127.0.0.1") -> "Rig":
        self.sessions = PhoneSessions(env.db, env.settings, env.events, SystemClock())
        self.sink = Sink()
        self.ingest = FrameIngest(self.sessions, self.sink, SystemClock(), env.settings.tuning.clock_sync)
        self.ingest.start()
        self.server = FramesTcpServer(self.sessions, self.ingest, SystemClock())
        self.port = await self.server.start("127.0.0.1", 0)
        opened = self.sessions.open(make_hello(pair_code=self.sessions.pair_code, mode="wired_adb"), peer_ip)
        assert isinstance(opened, Opened)
        self.s = opened.session
        self.clients: list[Client] = []
        return self

    async def connect(self, answer_sync: bool = True) -> Client:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        c = Client(reader, writer, self.s.cam, self.s.sid, answer_sync)
        self.clients.append(c)
        return c

    async def stop(self) -> None:
        for c in self.clients:
            c.close()
        self.ingest.stop()
        await self.server.stop()


@pytest.fixture
async def rig(env):
    r = await Rig().start(env)
    yield r
    await r.stop()


async def settle(s: float = 0.05) -> None:
    await asyncio.sleep(s)


async def test_frames_reach_the_sink_and_sync_runs_on_the_same_connection(rig):
    c = await rig.connect()
    c.send(frame(rig.s.sid, 0))
    await settle(0.65)
    (sess, f, _), = rig.sink.frames
    assert sess is rig.s and f.seq == 0 and rig.s.transport == "tcp"
    assert len(c.sync_requests) >= 4                            # fast rate, framed on this connection
    assert rig.s.clock_sync.ok(time.monotonic_ns())
    assert abs(rig.s.clock_sync.offset_ns(time.monotonic_ns()) - PHONE_OFFSET_NS) < 2_000_000
    assert not await c.closed(0.05)


async def test_a_message_split_into_single_bytes_still_arrives(rig):
    c = await rig.connect()
    for b in framed(frame(rig.s.sid, 0)):
        c.send_raw(bytes([b]))
        await c.writer.drain()
    await settle()
    assert len(rig.sink.frames) == 1


@pytest.mark.parametrize("bad", [struct.pack(">I", 0), struct.pack(">I", 8193) + b"x" * 10, framed(b"not json")],
                         ids=["length 0", "length 8193", "bad json"])
async def test_framing_errors_count_rx_bad_and_close(rig, bad):
    c = await rig.connect()
    c.send(frame(rig.s.sid, 0))                                 # adopt the connection: the error is the session's
    await settle()
    c.send_raw(bad)
    assert await c.closed()
    assert rig.s.stats.rx_bad == 1
    assert len(rig.sink.frames) == 1


async def test_no_valid_first_message_in_time_closes(rig, monkeypatch):
    monkeypatch.setattr(tcp_module, "FIRST_MESSAGE_TIMEOUT_S", 0.2)
    c = await rig.connect()
    assert await c.closed(1.0)
    assert rig.sink.frames == []


async def test_first_message_from_an_unknown_session_closes(rig):
    c = await rig.connect()
    c.send(frame("f" * 16, 0))
    assert await c.closed()
    assert rig.sessions.unknown_rx == 1 and rig.sink.frames == []


async def test_a_second_connection_for_the_same_sid_replaces_the_first(rig):
    first = await rig.connect()
    first.send(frame(rig.s.sid, 0))
    await settle()
    second = await rig.connect()
    second.send(frame(rig.s.sid, 1))
    assert await first.closed()
    assert not await second.closed(0.1)
    await settle(0.25)
    assert len(second.sync_requests) >= 1                      # sync now goes to the new connection
    assert [f.seq for _, f, _ in rig.sink.frames] == [0, 1]


async def test_ending_the_session_closes_its_connection(rig):
    c = await rig.connect()
    c.send(frame(rig.s.sid, 0))
    await settle()
    rig.sessions.close(rig.s.sid)
    assert await c.closed()


async def test_a_peer_other_than_the_websocket_peer_is_unauthorised_and_closed(env):
    r = await Rig().start(env, peer_ip="10.9.9.9")             # the session's WebSocket came from another address
    try:
        c = await r.connect()
        c.send(frame(r.s.sid, 0))
        assert await c.closed()
        assert r.s.stats.rx_unauth == 1 and r.sink.frames == []
    finally:
        await r.stop()


async def test_late_frames_are_dropped_without_closing(rig):
    c = await rig.connect()
    for seq in (3, 4, 2):
        c.send(frame(rig.s.sid, seq))
    await settle()
    assert [f.seq for _, f, _ in rig.sink.frames] == [3, 4]
    assert rig.s.stats.rx_late == 1
    assert not await c.closed(0.1)
