"""Step 5 / A14: rolling camera and link statistics."""

import pytest

from duoware.clock import FakeClock
from duoware.ingest.stats import CameraStats, LinkStats

S = 1_000_000_000


def test_pose_age_percentiles_on_known_data():
    clock = FakeClock(start_ns=100 * S)
    st = CameraStats(10, clock)
    assert st.pose_age().n == 0 and st.pose_age().p50 is None and st.fps() is None
    for i in range(101):
        st.add_pose_age(float(i), clock.mono_ns())              # ages 0..100 ms
    a = st.pose_age()
    assert (a.n, a.p50, a.max) == (101, 50.0, 100.0) and a.p95 == pytest.approx(95.0)


def test_window_forgets_old_samples():
    clock = FakeClock(start_ns=100 * S)
    st = CameraStats(10, clock)
    st.add_pose_age(500.0, clock.mono_ns())
    clock.advance_s(11)
    st.add_pose_age(10.0, clock.mono_ns())
    a = st.pose_age()
    assert (a.n, a.max) == (1, 10.0)
    clock.advance_s(11)
    assert st.pose_age().n == 0


def test_fps_from_accepted_frames():
    clock = FakeClock(start_ns=100 * S)
    st = CameraStats(10, clock)
    for _ in range(31):
        st.add_frame(clock.mono_ns())
        clock.advance(S // 30)
    assert st.fps() == pytest.approx(30.0, rel=0.01)


def test_jitter_follows_rfc_3550_on_a_known_series():
    clock = FakeClock(start_ns=100 * S)
    ls = LinkStats(10, clock)
    transits = [10.0, 12.0, 11.0, 15.0, 15.0]
    j = 0.0
    for i, t in enumerate(transits):
        ls.add(i, clock.mono_ns(), t)
        clock.advance(S // 30)
        if i:
            j += (abs(t - transits[i - 1]) - j) / 16
    assert ls.summary().jitter_ms == pytest.approx(j)
    assert ls.summary().jitter_ms > 0


def test_latency_percentiles_and_loss():
    clock = FakeClock(start_ns=100 * S)
    ls = LinkStats(10, clock)
    seq = 0
    for i in range(200):
        if i % 10 == 9:
            seq += 1                                            # one seq never arrives every 10 frames
        ls.add(seq, clock.mono_ns(), 2.0 + (i % 5))             # transits 2..6 ms
        seq += 1
        clock.advance(S // 100)
    s = ls.summary()
    assert s.latency_p50_ms == pytest.approx(4.0) and s.latency_p95_ms == pytest.approx(6.0)
    assert s.loss_pct == pytest.approx(100 * 20 / 220, abs=0.1)   # 20 of the 220 seqs from first to last never arrived


def test_transits_are_not_compared_across_an_unsynced_gap():
    clock = FakeClock(start_ns=100 * S)
    ls = LinkStats(10, clock)
    ls.add(0, clock.mono_ns(), 3.0)
    ls.add(1, clock.mono_ns(), None)
    ls.add(2, clock.mono_ns(), 50.0)                            # a huge step, but no neighbour to compare with
    assert ls.summary().jitter_ms is None
    assert ls.summary().latency_p50_ms == pytest.approx(26.5)


def test_seq_wraparound_does_not_count_as_loss():
    clock = FakeClock(start_ns=100 * S)
    ls = LinkStats(10, clock)
    for seq in (2 ** 32 - 2, 2 ** 32 - 1, 0, 1):
        ls.add(seq, clock.mono_ns(), 1.0)
    assert ls.summary().loss_pct == 0.0
