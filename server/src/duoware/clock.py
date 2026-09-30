"""Clocks. Control and ages use the monotonic clock; wall time is for display and the event log only
(DECISIONS.md D21). Every module that needs time takes a `Clock` so tests can drive it by hand."""

import time
from typing import Protocol


class Clock(Protocol):
    def mono_ns(self) -> int: ...

    def wall_ms(self) -> int: ...


class SystemClock:
    def mono_ns(self) -> int:
        return time.monotonic_ns()

    def wall_ms(self) -> int:
        return time.time_ns() // 1_000_000


class FakeClock:
    """Test clock: time only moves when `advance` is called."""

    def __init__(self, start_ns: int = 0, wall_start_ms: int = 1_790_000_000_000) -> None:
        self._mono_ns = start_ns
        self._wall_start_ms = wall_start_ms
        self._start_ns = start_ns

    def mono_ns(self) -> int:
        return self._mono_ns

    def wall_ms(self) -> int:
        return self._wall_start_ms + (self._mono_ns - self._start_ns) // 1_000_000

    def advance(self, ns: int) -> None:
        self._mono_ns += ns

    def advance_s(self, seconds: float) -> None:
        self._mono_ns += int(seconds * 1e9)
