"""Rolling camera and link statistics shown on the dashboard (PROTOCOL.md §6.2 `cameras[]`)."""

from collections import deque
from dataclasses import dataclass

import numpy as np

from duoware.clock import Clock

NS_PER_S = 1_000_000_000
JITTER_GAIN = 16                 # RFC 3550 §6.4.1: J += (|D| - J) / 16; PROTOCOL.md §6.2 jitter_ms
SEQ_MODULUS = 2 ** 32            # PROTOCOL.md §2: seq is a uint32


def _percentiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    a = np.asarray(values, dtype=np.float64)
    return float(np.percentile(a, 50)), float(np.percentile(a, 95)), float(a.max())


@dataclass(frozen=True)
class PoseAge:
    p50: float | None
    p95: float | None
    max: float | None
    n: int


class CameraStats:
    """Per camera (survives phone sessions): pose ages of accepted frames, frame rate and reject counters."""

    def __init__(self, window_s: float, clock: Clock) -> None:
        self._window_ns = int(window_s * NS_PER_S)
        self._clock = clock
        self._ages: deque[tuple[int, float]] = deque()      # (recv_ns, age_ms)
        self._frames: deque[int] = deque()                  # recv_ns of accepted frames
        self.dropped = 0
        self.rx_bad = 0
        self.rx_bad_marker = 0
        self.rx_version = 0
        self.rx_unauth = 0
        self.rx_late = 0

    def _trim(self, now: int) -> None:
        horizon = now - self._window_ns
        while self._ages and self._ages[0][0] < horizon:
            self._ages.popleft()
        while self._frames and self._frames[0] < horizon:
            self._frames.popleft()

    def add_frame(self, recv_ns: int) -> None:
        self._frames.append(recv_ns)
        self._trim(recv_ns)

    def add_pose_age(self, age_ms: float, recv_ns: int) -> None:
        self._ages.append((recv_ns, age_ms))
        self._trim(recv_ns)

    def pose_age(self) -> PoseAge:
        self._trim(self._clock.mono_ns())
        p50, p95, mx = _percentiles([a for _, a in self._ages])
        return PoseAge(p50, p95, mx, len(self._ages))

    def fps(self) -> float | None:
        """Accepted frames per second over the window; None with fewer than two frames."""
        self._trim(self._clock.mono_ns())
        if len(self._frames) < 2:
            return None
        span = (self._frames[-1] - self._frames[0]) / NS_PER_S
        return (len(self._frames) - 1) / span if span > 0 else None


@dataclass(frozen=True)
class LinkSummary:
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    jitter_ms: float | None
    loss_pct: float | None


class LinkStats:
    """Per session: one-way latency, RFC 3550 jitter and loss over `window_s` (a new path is a new measurement)."""

    def __init__(self, window_s: float, clock: Clock) -> None:
        self._window_ns = int(window_s * NS_PER_S)
        self._clock = clock
        self._transits: deque[tuple[int, float]] = deque()  # (recv_ns, transit_ms), only while synced
        self._seqs: deque[tuple[int, int]] = deque()        # (recv_ns, unwrapped seq)
        self._jitter: float | None = None
        self._last_transit: float | None = None
        self._last_seq: int | None = None
        self._seq_base = 0

    def add(self, seq: int, recv_ns: int, transit_ms: float | None) -> None:
        """One accepted frame. `transit_ms` is None while the camera is not synced."""
        if self._last_seq is not None and seq < self._last_seq - SEQ_MODULUS // 2:
            self._seq_base += SEQ_MODULUS                    # uint32 wrap
        self._last_seq = seq
        self._seqs.append((recv_ns, self._seq_base + seq))
        if transit_ms is not None:
            self._transits.append((recv_ns, transit_ms))
            if self._last_transit is not None:
                d = abs(transit_ms - self._last_transit)
                self._jitter = (self._jitter or 0.0) + (d - (self._jitter or 0.0)) / JITTER_GAIN
            self._last_transit = transit_ms
        else:
            self._last_transit = None                        # do not compare transits across an unsynced gap
        self._trim(recv_ns)

    def _trim(self, now: int) -> None:
        horizon = now - self._window_ns
        while self._transits and self._transits[0][0] < horizon:
            self._transits.popleft()
        while self._seqs and self._seqs[0][0] < horizon:
            self._seqs.popleft()

    def summary(self) -> LinkSummary:
        self._trim(self._clock.mono_ns())
        p50, p95, _ = _percentiles([t for _, t in self._transits])
        loss = None
        if len(self._seqs) >= 2:
            first, last = self._seqs[0][1], self._seqs[-1][1]
            expected = last - first + 1
            loss = max(0.0, (expected - len(self._seqs)) / expected * 100.0)
        return LinkSummary(p50, p95, self._jitter, loss)
