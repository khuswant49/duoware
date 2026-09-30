"""M2 step 3: `FrameIngest`, the §2 server rules and the §3.2 sync loop shared by UDP and TCP, without any socket."""

import asyncio
import dataclasses
import json

import pytest
from conftest import make_hello

from duoware.clock import SystemClock
from duoware.ingest.frames import FrameIngest
from duoware.ingest.sessions import Opened, PhoneSessions
from duoware.protocol.phone import SyncReply, encode_sync_r, parse_sync_request


class Sink:
    def __init__(self):
        self.frames = []

    def on_frame(self, session, frame, recv_ns):
        self.frames.append((session, frame, recv_ns))


def frame(sid, seq, cam=1, **over):
    d = {"v": 1, "t": "frame", "cam": cam, "sid": sid, "seq": seq, "cap_ns": 1, "exp_ns": 3_000_000, "skew_ns": 0,
         "avail_ns": 2, "sent_ns": 3, "w": 1280, "h": 720, "scan": "full", "searched": [],
         "m": [[1, 10, 10, 50, 10, 50, 50, 10, 50]]}
    d.update(over)
    return json.dumps(d).encode()


@pytest.fixture
def rig(env):
    class Rig:
        pass

    r = Rig()
    r.clock = SystemClock()
    r.sessions = PhoneSessions(env.db, env.settings, env.events, r.clock)
    r.sink = Sink()
    r.ingest = FrameIngest(r.sessions, r.sink, r.clock, env.settings.tuning.clock_sync)
    opened = r.sessions.open(make_hello(pair_code=r.sessions.pair_code), "127.0.0.1")
    assert isinstance(opened, Opened)
    r.s = opened.session
    r.replies = []
    r.reply = r.replies.append
    r.env = env
    return r


def handle(r, data, ip="127.0.0.1", transport="tcp"):
    return r.ingest.handle(data, ip, r.reply, transport, r.clock.mono_ns())


def test_an_accepted_frame_reaches_the_sink_and_sets_the_reply_and_transport(rig):
    h = handle(rig, frame(rig.s.sid, 0))
    assert h.session is rig.s and h.rejected is None and h.frame_accepted
    assert len(rig.sink.frames) == 1 and rig.sink.frames[0][0] is rig.s
    assert rig.s.reply == rig.reply and rig.s.transport == "tcp"
    handle(rig, frame(rig.s.sid, 1), transport="udp")
    assert rig.s.transport == "udp"                               # the latest accepted frame decides


def test_a_late_frame_is_counted_and_does_not_change_the_reply(rig):
    handle(rig, frame(rig.s.sid, 5))
    other = []
    h = rig.ingest.handle(frame(rig.s.sid, 5), "127.0.0.1", other.append, "udp", rig.clock.mono_ns())
    assert h.rejected == "rx_late" and h.session is rig.s and not h.frame_accepted
    assert rig.s.stats.rx_late == 1 and rig.s.reply == rig.reply and rig.s.transport == "tcp"


def test_each_rejection_names_its_counter_and_camera(rig):
    sid = rig.s.sid
    assert handle(rig, b"not json").rejected == "rx_bad"
    assert rig.sessions.unknown_rx == 1 and rig.s.stats.rx_bad == 0          # no sid to attribute it to
    assert handle(rig, frame(sid, 0, w="x")).rejected == "rx_bad"            # sid readable: counted on the camera
    assert rig.s.stats.rx_bad == 1
    assert handle(rig, frame(sid, 0, v=2)).rejected == "rx_version" and rig.s.stats.rx_version == 1
    assert handle(rig, b" " * 9000).rejected == "rx_bad" and rig.sessions.unknown_rx == 2       # too big, no sid
    assert rig.sink.frames == []


def test_unknown_sid_wrong_ip_and_wrong_cam_are_unauthorised(rig):
    a = handle(rig, frame("f" * 16, 0))
    assert a.rejected == "rx_unauth" and a.session is None and rig.sessions.unknown_rx == 1
    b = handle(rig, frame(rig.s.sid, 0), ip="10.9.9.9")
    assert b.rejected == "rx_unauth" and b.session is None and rig.s.stats.rx_unauth == 1
    c = handle(rig, frame(rig.s.sid, 0, cam=2))
    assert c.rejected == "rx_unauth" and rig.sessions.unknown_rx == 2          # wrong cam: not attributed to cam 1
    assert rig.sink.frames == []


def test_a_sync_reply_with_the_right_n_adds_a_sample_and_others_are_ignored(rig):
    t1 = rig.clock.mono_ns()
    rig.s.sync_sent[7] = t1
    t2 = t1 + 12_345_000_000
    body = encode_sync_r(SyncReply(rig.s.cam, rig.s.sid, 7, t1, t2, t2 + 10_000))
    h = handle(rig, body)
    assert h.session is rig.s and h.rejected is None and not h.frame_accepted
    assert rig.s.clock_sync.samples == 1
    handle(rig, encode_sync_r(SyncReply(rig.s.cam, rig.s.sid, 8, t1, t2, t2)))          # never requested
    assert rig.s.clock_sync.samples == 1


async def test_the_sync_loop_sends_through_the_sessions_reply(rig):
    cfg = dataclasses.replace(rig.env.settings.tuning.clock_sync, fast_period_s=0.3, fast_interval_ms=20, interval_ms=200)
    ingest = FrameIngest(rig.sessions, rig.sink, rig.clock, cfg)
    ingest.start()
    try:
        await asyncio.sleep(0.1)
        assert rig.replies == []                                     # no frame yet: nowhere to send
        rig.s.reply = rig.replies.append
        await asyncio.sleep(0.3)
        reqs = [parse_sync_request(m) for m in rig.replies]
        assert len(reqs) >= 5 and all(r.sid == rig.s.sid for r in reqs)
        assert [r.n for r in reqs] == sorted(r.n for r in reqs)
        rig.sessions.close(rig.s.sid)
        n = len(rig.replies)
        await asyncio.sleep(0.15)
        assert len(rig.replies) in (n, n + 1)                        # the task ends with the session
    finally:
        ingest.stop()


async def test_a_reply_that_raises_does_not_end_the_sync_loop(rig):
    cfg = dataclasses.replace(rig.env.settings.tuning.clock_sync, fast_period_s=0.3, fast_interval_ms=20, interval_ms=200)
    ingest = FrameIngest(rig.sessions, rig.sink, rig.clock, cfg)
    ingest.start()
    calls = []

    def flaky(data):
        calls.append(data)
        if len(calls) < 3:
            raise ConnectionResetError("closing")

    try:
        rig.s.reply = flaky
        await asyncio.sleep(0.25)
        assert len(calls) >= 5
    finally:
        ingest.stop()
