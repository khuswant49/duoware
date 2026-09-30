"""The simulated physical world: cars stepped at 200 Hz, their pose history (ground truth at any capture instant),
and which floor tags a car currently covers."""

import asyncio
import bisect
import math

from duoware.clock import Clock
from duoware.sim.car_physics import CarPhysics
from duoware.sim.scenario import Scenario

STEP_HZ = 200                       # physics rate (plan step 9)
HISTORY_KEEP = 4000                 # pose samples kept per car (20 s at 200 Hz) ...
HISTORY_TRIM = 2000                 # ... trimmed by this many when full
DEFAULT_FOOTPRINT_MM = (200.0, 160.0)


class SimWorld:
    def __init__(self, scenario: Scenario, clock: Clock, footprints: dict[str, tuple[float, float]] | None = None) -> None:
        self.scenario, self.clock = scenario, clock
        self.physics = {c.name: CarPhysics(c) for c in scenario.car}
        self.footprints = footprints or {}
        self._times: dict[str, list[int]] = {n: [] for n in self.physics}
        self._poses: dict[str, list[tuple[float, float, float]]] = {n: [] for n in self.physics}
        self.floor_tags = [tuple(t) for t in scenario.world.floor_tags]      # (id, x, y, yaw, size)
        self._task: asyncio.Task | None = None
        self._record(clock.mono_ns())

    # ------------------------------------------------------------------------------ stepping

    def _record(self, now_ns: int) -> None:
        for name, ph in self.physics.items():
            self._times[name].append(now_ns)
            self._poses[name].append(ph.pose())
            if len(self._times[name]) > HISTORY_KEEP:
                del self._times[name][:HISTORY_TRIM]
                del self._poses[name][:HISTORY_TRIM]

    def step(self, dt_s: float, now_ns: int) -> None:
        sub = max(1, math.ceil(dt_s * STEP_HZ))                 # never integrate a long gap in one go
        for i in range(1, sub + 1):
            t = now_ns - int((dt_s * (sub - i) / sub) * 1e9)
            for ph in self.physics.values():
                ph.step(dt_s / sub, t)
        self._record(now_ns)

    async def run(self) -> None:
        last = self.clock.mono_ns()
        try:
            while True:
                await asyncio.sleep(1.0 / STEP_HZ)
                now = self.clock.mono_ns()
                self.step((now - last) / 1e9, now)
                last = now
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    # ------------------------------------------------------------------------------ ground truth

    def true_pose(self, car: str, t_ns: int) -> tuple[float, float, float]:
        """Rotation-centre pose (x, y, heading) of `car` at server-clock time `t_ns`, interpolated from the history
        (the current pose for a time after the last sample, the oldest for one before the first)."""
        ts, ps = self._times[car], self._poses[car]
        i = bisect.bisect_left(ts, t_ns)
        if i <= 0:
            return ps[0]
        if i >= len(ts):
            return ps[-1]
        f = (t_ns - ts[i - 1]) / (ts[i] - ts[i - 1])
        (x0, y0, h0), (x1, y1, h1) = ps[i - 1], ps[i]
        dh = (h1 - h0 + 180.0) % 360.0 - 180.0
        return (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, (h0 + dh * f + 180.0) % 360.0 - 180.0)

    def covered_floor_tags(self, t_ns: int) -> set[int]:
        """Floor tags whose centre lies inside a car's footprint rectangle (centred on its rotation centre)."""
        hidden: set[int] = set()
        for name in self.physics:
            x, y, h = self.true_pose(name, t_ns)
            length, width = self.footprints.get(name, DEFAULT_FOOTPRINT_MM)
            c, s = math.cos(math.radians(h)), math.sin(math.radians(h))
            for tid, tx, ty, _yaw, _size in self.floor_tags:
                dx, dy = tx - x, ty - y
                along, across = dx * c + dy * s, -dx * s + dy * c
                if abs(along) <= length / 2 and abs(across) <= width / 2:
                    hidden.add(int(tid))
        return hidden
