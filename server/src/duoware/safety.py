"""The latched E-stop and the "may this change happen while cars move?" rule (PROTOCOL.md §7.4).

M1 has no car links, so its motion provider reports no moving cars; M3 replaces it. The E-stop is latched in
the server only and is never cleared by anything but `resume`.
"""

from typing import Protocol

from duoware.clock import Clock
from duoware.errors import DuoError
from duoware.store.events import EventLog


class MotionStateProvider(Protocol):
    def moving_cars(self) -> list[str]:
        """Names of cars with a motion command in effect or an autonomous task."""
        ...


class NoCarsMoving:
    def moving_cars(self) -> list[str]:
        return []


class SafetyState:
    def __init__(self, clock: Clock, events: EventLog, provider: MotionStateProvider | None = None) -> None:
        self._clock = clock
        self._events = events
        self.provider: MotionStateProvider = provider or NoCarsMoving()
        self.estop = False
        self.estop_since_wall_ms: int | None = None
        self.estop_by: str | None = None

    def stop_all(self, operator: str) -> bool:
        """Latches the E-stop. Idempotent: returns True only when this call changed the state."""
        if self.estop:
            return False
        self.estop, self.estop_since_wall_ms, self.estop_by = True, self._clock.wall_ms(), operator
        self._events.log("operator", operator=operator, key="estop", value="latched", prev="released",
                         facts={"moving": self.provider.moving_cars()}, reason="operator pressed STOP ALL")
        return True

    def resume(self, operator: str) -> bool:
        if not self.estop:
            return False
        self.estop, self.estop_since_wall_ms, self.estop_by = False, None, None
        self._events.log("operator", operator=operator, key="estop", value="released", prev="latched",
                         reason="operator resumed after STOP ALL")
        return True

    def motion_change_allowed(self) -> tuple[bool, list[str]]:
        """Allowed while the E-stop is latched or every car is stopped. Also returns the moving cars."""
        moving = self.provider.moving_cars()
        return (self.estop or not moving), moving

    def require_motion_allowed(self) -> None:
        allowed, moving = self.motion_change_allowed()
        if not allowed:
            raise DuoError("requires_stopped", 409, "Stop the cars (or press STOP ALL) before changing this.",
                           {"moving": moving})
