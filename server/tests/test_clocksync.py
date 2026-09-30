"""Step 5: the clock-sync estimator (PROTOCOL.md §3.3)."""

import random

from duoware.clock import FakeClock
from duoware.ingest.clocksync import ClockSync
from duoware.settings import load_settings

CFG = load_settings().tuning.clock_sync
OFFSET_NS = 12345_678_000_000            # 12345.678 s: phone clock minus server clock at server time 0
DRIFT = 30e-6                            # the phone clock runs 30 ppm fast
MS = 1_000_000


def phone_clock(server_ns: float) -> int:
    return int(server_ns * (1 + DRIFT) + OFFSET_NS)


def true_theta(server_ns: float) -> float:
    return phone_clock(server_ns) - server_ns


def exchange(cs: ClockSync, t1: int, rng: random.Random, jitter_ms: float = 3.0, base_ms: float = 0.5) -> bool:
    d1 = (base_ms + rng.uniform(0, jitter_ms)) * MS            # server -> phone one-way
    d2 = (base_ms + rng.uniform(0, jitter_ms)) * MS            # phone -> server one-way
    t2 = phone_clock(t1 + d1)
    t3 = phone_clock(t1 + d1 + 0.05 * MS)
    t4 = int(t1 + d1 + 0.05 * MS + d2)
    return cs.add(t1, t2, t3, t4)


def test_recovers_offset_and_drift_after_20_samples():
    for seed in range(20):
        rng = random.Random(seed)
        cs = ClockSync(CFG)
        t0 = 7_000_000_000
        for i in range(20):
            exchange(cs, t0 + i * 100 * MS, rng)
        assert cs.samples == 20
        now = t0 + 20 * 100 * MS
        err_ms = abs(cs.offset_ns(now) - true_theta(now)) / MS
        assert err_ms < 0.5, (seed, err_ms)


def test_error_stays_small_over_a_long_run():
    rng = random.Random(1)
    cs = ClockSync(CFG)
    t0 = 7_000_000_000
    worst = 0.0
    for i in range(400):                                        # 3 s fast + then 500 ms spacing ~ 3 minutes
        t = t0 + (i * 100 * MS if i < 30 else 3_000 * MS + (i - 30) * 500 * MS)
        exchange(cs, t, rng)
        if i >= 20:
            now = t + 5 * MS
            worst = max(worst, abs(cs.offset_ns(now) - true_theta(now)) / MS)
    assert worst < 1.0


def test_to_server_ns_inverts_the_phone_clock():
    rng = random.Random(3)
    cs = ClockSync(CFG)
    t0 = 7_000_000_000
    for i in range(30):
        exchange(cs, t0 + i * 100 * MS, rng)
    now = t0 + 3_100 * MS
    captured_server = now - 40 * MS
    assert abs(cs.to_server_ns(phone_clock(captured_server), now) - captured_server) < MS // 2


def test_discards_negative_and_slow_round_trips():
    cs = ClockSync(CFG)
    assert cs.add(1000, 5000, 5100, 1050) is False            # rtt = 50 - 100 < 0
    assert cs.add(0, 10, 20, int((CFG.max_rtt_ms + 1) * MS)) is False
    assert cs.samples == 0 and cs.offset_ns(0) is None and cs.to_server_ns(1, 0) is None
    assert cs.add(0, 10, 20, 2 * MS) is True and cs.samples == 1


def test_with_fewer_than_four_samples_uses_the_lowest_rtt_and_no_slope():
    cs = ClockSync(CFG)
    cs.add(0, OFFSET_NS + 1 * MS, OFFSET_NS + 1 * MS, 10 * MS)              # rtt 10 ms
    cs.add(100 * MS, OFFSET_NS + 101 * MS, OFFSET_NS + 101 * MS, 102 * MS)  # rtt 2 ms: theta = OFFSET
    cs.add(200 * MS, OFFSET_NS + 204 * MS, OFFSET_NS + 204 * MS, 208 * MS)
    assert cs.rtt_ms_min == 2.0
    assert cs.offset_ns(0) == cs.offset_ns(10**12) == OFFSET_NS


def test_ok_expires_after_unsynced_after_s():
    clock = FakeClock(start_ns=10_000_000_000)
    cs = ClockSync(CFG)
    assert not cs.ok(clock.mono_ns())
    t = clock.mono_ns()
    cs.add(t, t + OFFSET_NS, t + OFFSET_NS, t + 2 * MS)
    assert cs.ok(clock.mono_ns() + 2 * MS)
    clock.advance_s(CFG.unsynced_after_s - 0.5)
    assert cs.ok(clock.mono_ns())
    clock.advance_s(1.0)
    assert not cs.ok(clock.mono_ns())
    assert cs.samples == 1                                      # stale, but the estimator keeps its window


def test_window_drops_old_samples():
    cs = ClockSync(CFG)
    cs.add(0, OFFSET_NS, OFFSET_NS, 2 * MS)
    cs.add(int((CFG.window_s + 1) * 1e9), OFFSET_NS, OFFSET_NS, int((CFG.window_s + 1) * 1e9) + 2 * MS)
    assert cs.samples == 1
