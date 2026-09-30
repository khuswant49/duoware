"""Differential-drive car physics for the simulated cars (config/sim.toml [[car]]).

PWM -> target wheel speed (dead zone, right-wheel gain), first-order spin-up and coast, the TTL of PROTOCOL.md §5.4
(expiry brakes for `BRAKE_MS`, then coasts), and kinematics about the axle midpoint (the rotation centre).
"""

import math
from collections.abc import Callable

from duoware.protocol.car import PWM_MAX
from duoware.sim.scenario import SimCarCfg

BRAKE_MS = 150               # PROTOCOL.md §5.2: stop = brake both motors for 150 ms, then release
BRAKE_TIME_CONSTANT_DIV = 3  # braking decelerates 3x faster than coasting (DECISIONS.md D11, guess)
NS_PER_MS = 1_000_000


def wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


class CarPhysics:
    def __init__(self, cfg: SimCarCfg, on_ttl_expired: Callable[[int], None] | None = None) -> None:
        self.cfg = cfg
        self.x, self.y, self.heading = cfg.start
        self.v_left = self.v_right = 0.0
        self._pwm = (0, 0)
        self._expires_ns = 0
        self._brake_until_ns = 0
        self._moving = False              # a motion command is in effect
        self.ttl_expiries = 0
        self._on_expired = on_ttl_expired

    # ------------------------------------------------------------------------------ commands

    def command(self, left: int, right: int, ttl_ms: int, now_ns: int) -> None:
        """`M l r ttl`: replaces any current motion at once."""
        self._pwm = (left, right)
        self._expires_ns = now_ns + ttl_ms * NS_PER_MS
        self._moving = True

    def stop(self, now_ns: int) -> None:
        """`S`, TTL expiry and every error: brake for BRAKE_MS, then release."""
        self._moving = False
        self._pwm = (0, 0)
        self._brake_until_ns = now_ns + BRAKE_MS * NS_PER_MS

    # ------------------------------------------------------------------------------ dynamics

    def _target(self, pwm: int, gain: float) -> float:
        c = self.cfg
        if abs(pwm) < c.dead_zone_pwm:
            return 0.0
        speed = c.max_speed_mm_s * (abs(pwm) - c.dead_zone_pwm) / (PWM_MAX - c.dead_zone_pwm)
        return math.copysign(speed * gain, pwm)

    def step(self, dt_s: float, now_ns: int) -> None:
        if self._moving and now_ns >= self._expires_ns:
            self.ttl_expiries += 1
            self.stop(self._expires_ns)
            if self._on_expired:
                self._on_expired(self.ttl_expiries)
        tau = self.cfg.time_constant_ms / 1000.0
        if self._moving:
            t_l, t_r = self._target(self._pwm[0], 1.0), self._target(self._pwm[1], self.cfg.right_gain)
        else:
            t_l = t_r = 0.0
            if now_ns < self._brake_until_ns:
                tau /= BRAKE_TIME_CONSTANT_DIV
        k = 1.0 - math.exp(-dt_s / tau)
        self.v_left += (t_l - self.v_left) * k
        self.v_right += (t_r - self.v_right) * k
        v = (self.v_left + self.v_right) / 2.0
        omega_deg = math.degrees((self.v_left - self.v_right) / self.cfg.wheel_base_mm)    # clockwise positive
        mid = math.radians(self.heading + omega_deg * dt_s / 2.0)
        self.x += v * math.cos(mid) * dt_s
        self.y += v * math.sin(mid) * dt_s
        self.heading = wrap_deg(self.heading + omega_deg * dt_s)

    @property
    def speed_mm_s(self) -> float:
        return (self.v_left + self.v_right) / 2.0

    @property
    def is_moving(self) -> bool:
        return abs(self.v_left) > 0.5 or abs(self.v_right) > 0.5

    def pose(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.heading)
