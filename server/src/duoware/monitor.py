"""Event-loop lag monitor (M1 review F12). A probe task sleeps `lag_probe_ms` and records how late it wakes: anything
that blocks the loop (calibration refits, later the car links' command refresh) shows up as lag. Reported by
`GET /api/health` as `loop_lag_ms` and, for a stall, as a `system` event (at most once a minute)."""

import asyncio
from collections import deque

import numpy as np

from duoware.clock import Clock
from duoware.settings import MonitorCfg
from duoware.store.events import EventLog

NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000
STALL_EVENT_EVERY_S = 60       # at most one loop_stall event per minute


class LoopLagMonitor:
    def __init__(self, cfg: MonitorCfg, clock: Clock, events: EventLog | None = None) -> None:
        self.cfg, self.clock, self.events = cfg, clock, events
        self._samples: deque[tuple[int, float]] = deque()      # (time ns, overshoot ms)
        self._last_event_ns = -STALL_EVENT_EVERY_S * NS_PER_S
        self._task: asyncio.Task | None = None

    def record(self, overshoot_ms: float) -> None:
        now = self.clock.mono_ns()
        self._samples.append((now, overshoot_ms))
        horizon = now - int(self.cfg.window_s * NS_PER_S)
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()
        if (overshoot_ms > self.cfg.stall_ms and self.events is not None
                and now - self._last_event_ns >= STALL_EVENT_EVERY_S * NS_PER_S):
            self._last_event_ns = now
            self.events.log("system", key="loop_stall", value=round(overshoot_ms, 1),
                            facts={"overshoot_ms": round(overshoot_ms, 1), "stall_ms": self.cfg.stall_ms},
                            reason=f"the event loop ran a timed probe {overshoot_ms:.0f} ms late: something blocked it")

    def stats(self) -> dict[str, float | None]:
        """{"p95", "max"} over the window; None values until the first sample."""
        self.record_expire()
        if not self._samples:
            return {"p95": None, "max": None}
        v = np.array([s[1] for s in self._samples])
        return {"p95": round(float(np.percentile(v, 95)), 2), "max": round(float(v.max()), 2)}

    def record_expire(self) -> None:
        horizon = self.clock.mono_ns() - int(self.cfg.window_s * NS_PER_S)
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()

    async def _run(self) -> None:
        period_ns = int(self.cfg.lag_probe_ms * NS_PER_MS)
        try:
            while True:
                t0 = self.clock.mono_ns()
                await asyncio.sleep(period_ns / NS_PER_S)
                self.record(max(0.0, (self.clock.mono_ns() - t0 - period_ns) / NS_PER_MS))
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
