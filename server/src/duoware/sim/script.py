"""M1 only: a scripted driver so the map and pose-age statistics have something moving before the server can drive
(M3+). It is part of the SIMULATED WORLD, not a controller: it uses the simulator's true poses and commands the
physics directly (no TCP), refreshing like a real controller (TTL 300 ms every 100 ms). Removed once the server
drives the sim cars."""

import asyncio
import math

from duoware.clock import Clock
from duoware.protocol.car import PWM_MAX
from duoware.sim.car_physics import wrap_deg
from duoware.sim.scenario import Scenario, SimCarCfg
from duoware.sim.world import SimWorld

REFRESH_S = 0.1              # plan step 9: refreshed like a real controller, every 100 ms ...
TTL_MS = 300                 # ... with TTL 300
ARRIVE_MM = 5.0              # plan step 9: stop within 5 mm of the tag
TURN_DONE_DEG = 3.0          # turning in place ends within this heading error (the straight run steers out the rest)
TURN_SLOW_DEG = 20.0         # turn rate falls linearly below this heading error
TURN_MIN_FRACTION = 0.25     # ... down to this fraction of turn_deg_s
DRIVE_KP = 3.0               # straight run: speed = DRIVE_KP x distance (1/s), capped at speed_mm_s
DRIVE_MIN_MM_S = 12.0        # ... but never slower than this, so a run always finishes
STEER_KP = 0.02              # straight run: differential speed fraction per degree of heading error
DWELL_S = 0.3                # pause at every node


def _pwm_for(speed: float, cfg: SimCarCfg, gain: float) -> int:
    """PWM that gives a wheel speed (inverse of CarPhysics._target)."""
    if abs(speed) < 1e-6:
        return 0
    p = cfg.dead_zone_pwm + min(1.0, abs(speed) / (cfg.max_speed_mm_s * gain)) * (PWM_MAX - cfg.dead_zone_pwm)
    return int(math.copysign(min(PWM_MAX, round(p)), speed))


class ScriptDriver:
    def __init__(self, scenario: Scenario, world: SimWorld, clock: Clock) -> None:
        self.cfg, self.world, self.clock = scenario.script, world, clock
        self.tags = {int(t[0]): (t[1], t[2]) for t in scenario.world.floor_tags}
        self._route_index = {n: 0 for n in scenario.script.routes}
        self._dwell_until = {n: 0 for n in scenario.script.routes}
        self._phase = {n: "turn" for n in scenario.script.routes}
        self._cars = {c.name: c for c in scenario.car}
        self._task: asyncio.Task | None = None

    def _target(self, name: str) -> tuple[float, float]:
        route = self.cfg.routes[name]
        return self.tags[route[self._route_index[name] % len(route)]]

    def tick(self) -> None:
        now = self.clock.mono_ns()
        for name in self.cfg.routes:
            ph, car = self.world.physics[name], self._cars[name]
            if now < self._dwell_until[name]:
                continue
            x, y, h = ph.pose()
            tx, ty = self._target(name)
            dist = math.hypot(tx - x, ty - y)
            if dist <= ARRIVE_MM:
                ph.command(0, 0, TTL_MS, now)
                self._route_index[name] += 1
                self._phase[name] = "turn"
                self._dwell_until[name] = now + int(DWELL_S * 1e9)
                continue
            err = wrap_deg(math.degrees(math.atan2(ty - y, tx - x)) - h)
            if self._phase[name] == "turn":
                if abs(err) <= TURN_DONE_DEG:
                    self._phase[name] = "drive"
                else:
                    rate = self.cfg.turn_deg_s * max(TURN_MIN_FRACTION, min(1.0, abs(err) / TURN_SLOW_DEG))
                    v = math.radians(rate) * car.wheel_base_mm / 2.0               # wheel speed of a spin in place
                    sign = 1 if err > 0 else -1                                    # err > 0: turn clockwise = left wheel forward
                    ph.command(_pwm_for(sign * v, car, 1.0), _pwm_for(-sign * v, car, car.right_gain), TTL_MS, now)
                    continue
            speed = max(DRIVE_MIN_MM_S, min(self.cfg.speed_mm_s, DRIVE_KP * dist))
            steer = max(-0.5, min(0.5, STEER_KP * err))                            # > 0: too far left of target: speed up left
            ph.command(_pwm_for(speed * (1 + steer), car, 1.0), _pwm_for(speed * (1 - steer), car, car.right_gain), TTL_MS, now)
            if abs(err) > TURN_SLOW_DEG * 2:
                self._phase[name] = "turn"

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(REFRESH_S)
                self.tick()
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        if self.cfg.enabled:
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
