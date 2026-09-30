"""Step 5: the UDP frames endpoint with real loopback sockets (PROTOCOL.md §2 rules, §3.2 sync)."""

import asyncio
import json
import time

import pytest
from conftest import make_hello

from duoware.clock import SystemClock
from duoware.ingest.sessions import Opened, PhoneSessions
from duoware.ingest.udp import FramesEndpoint
from duoware.protocol.phone import SyncReply, encode_sync_r, parse_sync_request

PHONE_OFFSET_NS = 12345_678_000_000


class Sink:
    def __init__(self):
        self.frames = []

    def on_frame(self, session, frame, recv_ns):
        self.frames.append((session, frame, recv_ns))


class Phone(asyncio.DatagramProtocol):
    """A loopback UDP client that can send JSON and answers sync requests like a phone."""

    def __init__(self, cam=1, sid="", answer_sync=True):
        self.cam, self.sid, self.answer_sync = cam, sid, answer_sync
        self.transport = None
        self.sync_requests = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        req = parse_sync_request(data)
        self.sync_requests.append(req)
        if self.answer_sync:
            t2 = time.monotonic_ns() + PHONE_OFFSET_NS
            self.transport.sendto(encode_sync_r(SyncReply(self.cam, self.sid, req.n, req.t1, t2, t2 + 10_000)), addr)

    def send(self, obj, addr):
        self.transport.sendto(obj if isinstance(obj, bytes) else json.dumps(obj).encode(), addr)


def frame(sid, seq, cam=1, markers=None, **over):
    d = {"v": 1, "t": "frame", "cam": cam, "sid": sid, "seq": seq, "cap_ns": 1, "exp_ns": 3_000_000, "skew_ns": 0,
         "avail_ns": 2, "sent_ns": 3, "w": 1280, "h": 720, "scan": "full", "searched": [],
         "m": markers if markers is not None else [[1, 10, 10, 50, 10, 50, 50, 10, 50]]}
    d.update(over)
    return d


class Rig:
    async def start(self, env, peer_ip="127.0.0.1", answer_sync=True):
        self.sessions = PhoneSessions(env.db, env.settings, env.events, SystemClock())
        self.sink = Sink()
        self.endpoint = FramesEndpoint(self.sessions, self.sink, SystemClock(), env.settings.tuning.clock_sync)
        loop = asyncio.get_running_loop()
        self.server_tr, _ = await loop.create_datagram_endpoint(lambda: self.endpoint, local_addr=("127.0.0.1", 0))
        self.server_addr = self.server_tr.get_extra_info("sockname")
        opened = self.sessions.open(make_hello(pair_code=self.sessions.pair_code), peer_ip)
        assert isinstance(opened, Opened)
        self.s = opened.session
        self.phone = Phone(self.s.cam, self.s.sid, answer_sync)
        self.phone_tr, _ = await loop.create_datagram_endpoint(lambda: self.phone, local_addr=("127.0.0.1", 0))
        return self

    def send(self, obj):
        self.phone.send(obj, self.server_addr)

    async def settle(self, s=0.05):
        await asyncio.sleep(s)

    def stop(self):
        self.server_tr.close()
        self.phone_tr.close()


@pytest.fixture
async def rig(env):
    r = await Rig().start(env)
    yield r
    r.stop()


async def test_good_frame_reaches_the_sink(rig):
    rig.send(frame(rig.s.sid, 0))
    await rig.settle()
    (sess, f, recv_ns), = rig.sink.frames
    assert sess is rig.s and f.seq == 0 and len(f.markers) == 1 and recv_ns > 0
    assert rig.s.frame_addr == rig.phone_tr.get_extra_info("sockname")
    assert rig.s.stats.dropped == 0


async def test_seq_gap_counts_dropped_and_late_is_rejected(rig):
    for seq in (5, 6, 9, 9, 7, 10):
        rig.send(frame(rig.s.sid, seq))
        await rig.settle(0.01)
    await rig.settle()
    assert [f.seq for _, f, _ in rig.sink.frames] == [5, 6, 9, 10]
    assert rig.s.stats.dropped == 2 + 0     # 6 -> 9 lost two; 9 -> 10 none (the first frame's seq is not a gap)
    assert rig.s.stats.rx_late == 2          # the duplicate 9 and the late 7


async def test_bad_datagrams_are_counted_per_cause(rig):
    sid = rig.s.sid
    rig.send(b"not json")
    rig.send({**frame(sid, 0), "w": "x"})                                  # wrong type, sid known
    rig.send(b" " * 9000)                                                  # too big
    rig.send({**frame(sid, 0), "v": 2})
    rig.send({**frame(sid, 0), "v": 2})
    await rig.settle()
    assert rig.s.stats.rx_bad == 1 and rig.s.stats.rx_version == 2
    assert rig.sessions.unknown_rx == 2                                    # "not json" and the oversized one
    assert rig.sink.frames == []


async def test_unknown_sid_and_wrong_source_ip_are_unauthorised(env):
    r = await Rig().start(env, peer_ip="10.9.9.9")                         # the WebSocket peer is another address
    try:
        r.send(frame(r.s.sid, 0))
        r.send(frame("f" * 16, 0))
        await r.settle()
        assert r.s.stats.rx_unauth == 1 and r.sessions.unknown_rx == 1 and r.sink.frames == []
    finally:
        r.stop()


async def test_bad_markers_are_dropped_and_counted_but_the_frame_is_kept(rig):
    good = [1, 10, 10, 50, 10, 50, 50, 10, 50]
    rig.send(frame(rig.s.sid, 0, markers=[good, [77] + good[1:], "x"]))
    await rig.settle()
    assert len(rig.sink.frames) == 1 and len(rig.sink.frames[0][1].markers) == 1
    assert rig.s.stats.rx_bad_marker == 2


async def test_empty_frames_are_accepted(rig):
    rig.send(frame(rig.s.sid, 0, markers=[]))
    await rig.settle()
    assert rig.sink.frames[0][1].markers == ()


async def test_sync_requests_start_with_the_first_frame_and_are_answered(rig):
    await rig.settle(0.2)
    assert rig.phone.sync_requests == []                # the source address is not known yet
    rig.send(frame(rig.s.sid, 0))
    await rig.settle(0.65)
    assert len(rig.phone.sync_requests) >= 4            # fast rate: every 100 ms
    assert rig.s.clock_sync.samples >= 4 and rig.s.clock_sync.ok(time.monotonic_ns())
    theta = rig.s.clock_sync.offset_ns(time.monotonic_ns())
    assert abs(theta - PHONE_OFFSET_NS) < 2_000_000     # loopback: within a couple of ms of the true offset
    assert [r.n for r in rig.phone.sync_requests] == sorted(r.n for r in rig.phone.sync_requests)


async def test_replies_with_unknown_or_stale_n_are_discarded(rig):
    rig.send(frame(rig.s.sid, 0))
    await rig.settle(0.05)
    n0 = rig.s.clock_sync.samples
    rig.send(encode_sync_r(SyncReply(rig.s.cam, rig.s.sid, 9999, 1, 2, 3)))         # never requested
    t = time.monotonic_ns()
    rig.send(encode_sync_r(SyncReply(rig.s.cam, rig.s.sid, rig.s.sync_n, t, t, t)))  # right n, wrong t1
    await rig.settle(0.02)
    assert rig.s.clock_sync.samples <= n0 + 1           # at most the normal sample that arrived meanwhile


async def test_closing_a_session_stops_its_sync_task(rig):
    rig.send(frame(rig.s.sid, 0))
    await rig.settle(0.15)
    rig.sessions.close(rig.s.sid)
    n = len(rig.phone.sync_requests)
    await rig.settle(0.3)
    assert len(rig.phone.sync_requests) in (n, n + 1)
    rig.send(frame(rig.s.sid, 1))
    await rig.settle()
    assert rig.sessions.unknown_rx == 1                 # a frame for a dead sid is unauthorised


async def test_slow_sync_rate_after_the_fast_period(env):
    import dataclasses
    cfg = dataclasses.replace(env.settings.tuning.clock_sync, fast_period_s=0.2, fast_interval_ms=20, interval_ms=200)
    r = Rig()
    r.sessions = PhoneSessions(env.db, env.settings, env.events, SystemClock())
    r.endpoint = FramesEndpoint(r.sessions, Sink(), SystemClock(), cfg)
    loop = asyncio.get_running_loop()
    r.server_tr, _ = await loop.create_datagram_endpoint(lambda: r.endpoint, local_addr=("127.0.0.1", 0))
    r.server_addr = r.server_tr.get_extra_info("sockname")
    r.s = r.sessions.open(make_hello(pair_code=r.sessions.pair_code), "127.0.0.1").session
    r.phone = Phone(r.s.cam, r.s.sid)
    r.phone_tr, _ = await loop.create_datagram_endpoint(lambda: r.phone, local_addr=("127.0.0.1", 0))
    try:
        r.send(frame(r.s.sid, 0))
        await r.settle(0.2)
        fast_count = len(r.phone.sync_requests)
        await r.settle(0.6)
        slow_count = len(r.phone.sync_requests) - fast_count
        assert fast_count >= 5 and slow_count <= 5
    finally:
        r.stop()
