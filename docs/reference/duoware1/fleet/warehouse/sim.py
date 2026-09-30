"""
DUO-WARE fleet simulator: stands in for the physical robots.

It drives each robot along the checkpoint-to-checkpoint moves the FleetManager grants, drains
and charges batteries, reports NFC/RFID reads on arrival and sends telemetry every step. Faults
can be injected to exercise detection and recovery:

    motor     motor stops, telemetry reports motor_fault
    obstacle  robot halts, obstacle sensor stays on
    stall     robot halts with no reported cause
    comm      telemetry stops; the robot's local safety logic halts it
"""

from typing import Dict, Optional, Set

from .manager import FleetManager
from .models import RobotState

FAULT_KINDS = {"motor", "obstacle", "stall", "comm"}


class FleetSimulator:
    def __init__(self, manager: FleetManager, dt: float = 0.5, start: float = 0.0,
                 drain_pct_per_m: float = 0.4, idle_drain_pct_per_s: float = 0.002,
                 charge_pct_per_s: float = 2.0):
        self.fm = manager
        self.dt = dt
        self.t = start
        self.drain = drain_pct_per_m
        self.idle_drain = idle_drain_pct_per_s
        self.charge_rate = charge_pct_per_s
        self.progress: Dict[str, float] = {}
        self.battery: Dict[str, float] = {rid: r.battery for rid, r in manager.robots.items()}
        self.faults: Dict[str, Set[str]] = {}

    def inject(self, robot_id: str, kind: str):
        assert kind in FAULT_KINDS, kind
        self.faults.setdefault(robot_id, set()).add(kind)

    def clear(self, robot_id: str, kind: Optional[str] = None):
        if kind:
            self.faults.get(robot_id, set()).discard(kind)
        else:
            self.faults.pop(robot_id, None)

    def step(self):
        self.t += self.dt
        for rid, r in self.fm.robots.items():
            self.battery.setdefault(rid, r.battery)
            faults = self.faults.get(rid, set())
            cmd = self.fm.motion_command(rid)
            halted = faults & FAULT_KINDS
            if cmd and not halted:
                target, speed = cmd
                length = self.fm.map.adj[r.node][target]
                step = speed * self.dt
                self.progress[rid] = self.progress.get(rid, 0.0) + step
                self.battery[rid] -= step * self.drain
                if self.progress[rid] >= length:
                    self.progress[rid] = 0.0
                    if "comm" not in faults:
                        self.fm.on_checkpoint(rid, target, battery=self.battery[rid], now=self.t)
            elif r.state == RobotState.CHARGING and self.fm.map.checkpoints[r.node].kind == "CHARGER":
                self.battery[rid] = min(100.0, self.battery[rid] + self.charge_rate * self.dt)
            else:
                self.battery[rid] -= self.idle_drain * self.dt
            self.battery[rid] = max(0.0, self.battery[rid])
            if "comm" not in faults:
                self.fm.on_telemetry(rid, battery=self.battery[rid], obstacle="obstacle" in faults,
                                     motor_fault="motor" in faults, now=self.t)
        self.fm.tick(self.t)

    def run(self, seconds: float, until=None) -> bool:
        """Advance up to `seconds`; stop early (returning True) once until(manager) is true."""
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.step()
            if until is not None and until(self.fm):
                return True
        return False
