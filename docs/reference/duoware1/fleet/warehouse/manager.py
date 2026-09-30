"""
DUO-WARE Fleet Manager: central coordination, recovery and explainability for a robot fleet.

The manager owns fleet-level decisions; each robot only drives the checkpoint-to-checkpoint moves
it is granted. Inputs are NFC/RFID checkpoint reads and telemetry; outputs are motion grants
(`motion_command`), alerts for workers, and a decision log that explains every important choice.

Time is passed in by the caller (`tick(now)`, `on_checkpoint(..., now=)`), so the same code runs
against the simulator, recorded logs or real robots.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .decision_log import DecisionLog
from .layout import WarehouseMap, edge_key
from .models import (CARE_PROFILES, STOPPED_STATES, Alert, CareLevel, CheckpointRead, FleetMode, Goal,
                     Parcel, PoolRole, Robot, RobotState, Task, TaskPriority, TaskStatus)

logger = logging.getLogger("duo_ware.fleet.manager")

BASE_PRIORITY = {TaskPriority.LOW: 10.0, TaskPriority.NORMAL: 30.0,
                 TaskPriority.HIGH: 60.0, TaskPriority.CRITICAL: 90.0}
CARE_RANK = {CareLevel.NORMAL: 0, CareLevel.FRAGILE: 1, CareLevel.HIGHLY_FRAGILE: 2}
OPEN_TASK = {TaskStatus.PENDING, TaskStatus.RESERVED}
ACTIVE_TASK = {TaskStatus.ASSIGNED, TaskStatus.CARRYING, TaskStatus.AWAITING_CONFIRMATION}


@dataclass
class FleetConfig:
    drain_pct_per_m: float = 0.4          # planning estimate; the real drain comes from telemetry
    energy_margin: float = 1.15
    min_operational_battery: float = 20.0  # never plan a task that ends below this
    recovery_reserve: float = 10.0         # must still reach a charger after the task
    charge_threshold: float = 30.0
    charge_target: float = 90.0
    early_release_battery: float = 60.0    # OVERLOAD may pull a robot off the charger at this level
    charge_stagger_s: float = 20.0         # at most one voluntary charge start per window
    dwell_pick_s: float = 3.0
    dwell_drop_s: float = 3.0
    stall_min_s: float = 15.0
    stall_factor: float = 3.0
    confirm_window_s: float = 10.0
    obstacle_timeout_s: float = 10.0
    comm_timeout_s: float = 5.0
    max_wait_before_reroute_s: float = 20.0
    congestion_penalty_m: float = 4.0
    overload_threshold: int = 15
    human_assist_after_s: float = 60.0
    reserve_activate_interval_s: float = 30.0
    reserve_release_after_s: float = 60.0
    aging_points_per_min: float = 6.0      # anti-starvation: waiting work gains priority
    reservation_hysteresis_s: float = 10.0


class FleetManager:
    def __init__(self, warehouse: WarehouseMap, config: Optional[FleetConfig] = None):
        self.map = warehouse
        self.cfg = config or FleetConfig()
        self.now = 0.0
        self.robots: Dict[str, Robot] = {}
        self.tasks: Dict[str, Task] = {}
        self.parcels: Dict[str, Parcel] = {}
        self.holder: Dict[str, str] = {}                     # checkpoint -> robot holding it
        self.blocked_nodes: Dict[str, str] = {}              # checkpoint -> reason
        self.blocked_edges: Dict[Tuple[str, str], str] = {}
        self.alerts: Dict[str, Alert] = {}
        self.log = DecisionLog()
        self.checkpoint_log: List[CheckpointRead] = []
        self.estop_all = False
        self.mode = FleetMode.NORMAL
        self.reserve_active: Set[str] = set()
        self.needs_charge: Set[str] = set()
        self._task_seq = 0
        self._alert_seq = 0
        self._overload_since: Optional[float] = None
        self._queue_empty_since: Optional[float] = None
        self._last_reserve_change = -math.inf
        self._last_voluntary_charge = -math.inf
        self._no_robot_logged: Set[str] = set()
        self._human_requested: Optional[str] = None
        self._yielding: Dict[str, Tuple[Set[str], float]] = {}   # robot -> (robots it gives way to, until)

    # ------------------------------------------------------------------ registry

    def register_robot(self, robot_id: str, node: str, battery: float = 100.0,
                       pool: PoolRole = PoolRole.NORMAL, capabilities=(), home: Optional[str] = None,
                       speed_mps: float = 1.0, now: Optional[float] = None) -> Robot:
        self._clock(now)
        if node not in self.map.checkpoints:
            raise ValueError(f"unknown checkpoint {node}")
        if node in self.holder:
            raise ValueError(f"{node} already occupied by {self.holder[node]}")
        r = Robot(robot_id=robot_id, node=node, battery=battery, pool=pool,
                  capabilities=set(capabilities), home=home or node, speed_mps=speed_mps,
                  last_comm=self.now, last_checkpoint_at=self.now)
        if pool != PoolRole.NORMAL:
            r.state = RobotState.RESERVED
        self.robots[robot_id] = r
        self.holder[node] = robot_id
        self.log.record(self.now, "REGISTER", f"{robot_id} joined at {node}",
                        f"pool {pool.value}, battery {battery:.0f}%", robot_id=robot_id, location=node,
                        battery=battery, new_state=r.state.value)
        return r

    def register_parcel(self, parcel: Parcel):
        self.parcels[parcel.parcel_id] = parcel

    def submit_task(self, pickup: str, destination: str, priority: TaskPriority = TaskPriority.NORMAL,
                    parcel_id: Optional[str] = None, deadline: Optional[float] = None,
                    required_capability: Optional[str] = None, importance: float = 0.0,
                    task_id: Optional[str] = None, now: Optional[float] = None) -> str:
        self._clock(now)
        for cp in (pickup, destination):
            if cp not in self.map.checkpoints:
                raise ValueError(f"unknown checkpoint {cp}")
        self._task_seq += 1
        tid = task_id or f"TASK_{self._task_seq:03d}"
        care, source = self._lookup_care(parcel_id)
        t = Task(task_id=tid, pickup=pickup, destination=destination, priority=priority, parcel_id=parcel_id,
                 deadline=deadline, required_capability=required_capability, importance=importance,
                 created_at=self.now, care=care)
        self.tasks[tid] = t
        free = [r for r in self.robots.values() if self._availability(r)[0]]
        note = "" if free else " All robots are busy: task waits in the pending queue (not rejected)."
        self.log.record(self.now, "QUEUE", f"{tid} queued {pickup} -> {destination}",
                        f"priority {priority.value}, care {care.value} ({source}).{note}", task_id=tid,
                        location=pickup)
        return tid

    def _lookup_care(self, parcel_id: Optional[str]) -> Tuple[CareLevel, str]:
        if not parcel_id:
            return CareLevel.NORMAL, "no parcel record attached"
        p = self.parcels.get(parcel_id)
        if p is None:
            return CareLevel.FRAGILE, f"{parcel_id} has no handling record: treated as FRAGILE until confirmed"
        return p.care, f"{parcel_id} handling record: {p.care.value}"

    # ------------------------------------------------------------------ robot inputs

    def on_telemetry(self, robot_id: str, battery: Optional[float] = None, obstacle: Optional[bool] = None,
                     motor_fault: Optional[bool] = None, estop: Optional[bool] = None,
                     now: Optional[float] = None):
        self._clock(now)
        r = self.robots[robot_id]
        r.last_comm = self.now
        if battery is not None:
            r.battery = battery
        if obstacle is not None:
            if obstacle and r.obstacle_since is None:
                r.obstacle_since = self.now
            elif not obstacle:
                r.obstacle_since = None
        if motor_fault is not None:
            r.motor_fault = motor_fault
        if estop is not None:
            r.estop = estop

    def on_checkpoint(self, robot_id: str, checkpoint: str, battery: Optional[float] = None,
                      now: Optional[float] = None):
        """A robot read an NFC/RFID tag: this is a trusted position fix."""
        self._clock(now)
        r = self.robots[robot_id]
        r.last_comm = self.now
        if battery is not None:
            r.battery = battery
        if checkpoint == r.node and r.moving_to is None:
            return
        prev = r.node
        expected = r.moving_to
        if checkpoint != expected:
            self.log.record(self.now, "POSITION", f"{robot_id} read {checkpoint}, expected {expected}",
                            "Checkpoint tag is trusted over dead reckoning: position corrected, route re-planned.",
                            robot_id=robot_id, location=checkpoint, battery=r.battery)
            if expected and self.holder.get(expected) == robot_id:
                del self.holder[expected]
            other = self.holder.get(checkpoint)
            if other and other != robot_id:
                self._raise_alert(r, f"reads {checkpoint}, which {other} also holds: check both robots",
                                  "Position conflict", location=checkpoint)
            r.route = []
        if self.holder.get(prev) == robot_id and prev != checkpoint:
            del self.holder[prev]
        self.holder[checkpoint] = robot_id
        r.prev_node, r.node, r.moving_to = prev, checkpoint, None
        r.last_checkpoint_at = self.now
        if r.state == RobotState.POSSIBLE_FAULT:
            r.state = self._travel_state(r)
            r.suspect_since = None
            self.log.record(self.now, "HEALTH", f"{robot_id} cleared", f"Reached {checkpoint}: progress resumed.",
                            robot_id=robot_id, location=checkpoint, new_state=r.state.value)
        task = self.tasks.get(r.task_id) if r.task_id else None
        self.checkpoint_log.append(CheckpointRead(
            robot_id=robot_id, checkpoint=checkpoint, time=self.now, task_id=r.task_id, battery=r.battery,
            state=r.state.value, direction=f"{prev}->{checkpoint}", previous=prev,
            next_expected=r.route[0] if r.route else None,
            destination=r.goal_node or (task.destination if task else None), expected=checkpoint == expected))

    def motion_command(self, robot_id: str) -> Optional[Tuple[str, float]]:
        """(next checkpoint, speed m/s) the robot may drive to now, or None to hold still."""
        r = self.robots[robot_id]
        if self.estop_all or r.estop or r.moving_to is None:
            return None
        if r.state in STOPPED_STATES or r.state == RobotState.PAUSED:
            return None
        return r.moving_to, r.speed_mps * r.profile.speed_factor

    # ------------------------------------------------------------------ main loop

    def tick(self, now: float):
        self._clock(now)
        for r in self.robots.values():
            if r.in_service and r.state not in STOPPED_STATES and self.now - r.last_comm > self.cfg.comm_timeout_s:
                self._fault(r, RobotState.OFFLINE,
                            f"no telemetry for {self.now - r.last_comm:.1f} s; position not confirmed")
        if self.estop_all:
            return          # E-stop outranks every mission and scheduling rule
        self._check_health()
        self._finish_dwells()
        self._manage_reserve_pool()
        self._dispatch()
        self._manage_charging()
        self._park_idle()
        self._traffic()
        self._update_mode()

    # ------------------------------------------------------------------ priority and selection

    def priority_score(self, task: Task) -> Tuple[float, Dict[str, float]]:
        c = {"base": BASE_PRIORITY[task.priority],
             "waiting": self.cfg.aging_points_per_min * (self.now - task.created_at) / 60.0}
        if task.deadline is not None:
            slack = task.deadline - self.now
            c["deadline"] = 40.0 * min(1.0, max(0.0, 1.0 - slack / 600.0))
        if task.care != CareLevel.NORMAL:
            c["safety"] = 5.0 * CARE_RANK[task.care]
        if task.importance:
            c["importance"] = task.importance
        return round(sum(c.values()), 1), {k: round(v, 1) for k, v in c.items()}

    def _energy(self, metres: float) -> float:
        return metres * self.cfg.drain_pct_per_m * self.cfg.energy_margin

    def _open_distance(self, a: str, b: str) -> float:
        def cost(u, v, length):
            blocked = v in self.blocked_nodes or edge_key(u, v) in self.blocked_edges
            return math.inf if blocked else length
        path = self.map.shortest(a, b, edge_cost=cost)
        return math.inf if path is None else self.map.path_length(path)

    def _nearest_charger_distance(self, node: str) -> float:
        return min((self.map.distance(node, c) for c in self.map.of_kind("CHARGER")), default=0.0)

    def _availability(self, r: Robot) -> Tuple[bool, str]:
        if not r.in_service or r.state in STOPPED_STATES:
            return False, f"out of service ({r.state.value})"
        if r.estop:
            return False, "emergency stop active"
        if r.state == RobotState.PAUSED:
            return False, "paused by operator"
        if r.state == RobotState.RESERVED or (r.pool != PoolRole.NORMAL and r.robot_id not in self.reserve_active):
            return False, "standby reserve (not activated)"
        if r.state == RobotState.POSSIBLE_FAULT:
            return False, "under fault check"
        if r.task_id:
            return False, f"busy with {r.task_id}"
        if r.state == RobotState.CHARGING:
            if self.mode in (FleetMode.OVERLOAD, FleetMode.HUMAN_ASSISTANCE) and \
                    r.battery >= self.cfg.early_release_battery:
                return True, "on charger, released early for overload"
            return False, f"charging ({r.battery:.0f}%)"
        if r.goal == Goal.CHARGE:
            return False, f"heading to charger ({r.battery:.0f}%)"
        return True, "available"

    def evaluate(self, r: Robot, task: Task, start: Optional[str] = None, battery: Optional[float] = None,
                 lead_s: float = 0.0) -> Dict:
        """Score one robot for one task. Feasibility first (battery reserve), then a 0-100 score."""
        start = start or r.moving_to or r.node
        battery = r.battery if battery is None else battery
        d_pick = self._open_distance(start, task.pickup)
        d_task = self._open_distance(task.pickup, task.destination)
        d_charge = self._nearest_charger_distance(task.destination)
        after = battery - self._energy(d_pick + d_task)
        row = {"robot": r.robot_id, "state": r.state.value, "distance_m": round(d_pick, 1),
               "battery": round(battery, 1), "battery_after": round(after, 1)}
        if task.required_capability and task.required_capability not in r.capabilities:
            return {**row, "feasible": False, "score": None, "reason": f"lacks capability {task.required_capability}"}
        if math.isinf(d_pick) or math.isinf(d_task):
            return {**row, "feasible": False, "score": None, "reason": "no open route"}
        if after < self.cfg.min_operational_battery:
            return {**row, "feasible": False, "score": None,
                    "reason": f"battery {battery:.0f}% would end at {after:.0f}%, below the "
                              f"{self.cfg.min_operational_battery:.0f}% operating reserve"}
        if after - self._energy(d_charge) < self.cfg.recovery_reserve:
            return {**row, "feasible": False, "score": None,
                    "reason": f"could not reach a charger with {self.cfg.recovery_reserve:.0f}% left after the task"}
        path = self.map.shortest(start, task.pickup) or [start]
        congestion = sum(1 for n in path[1:] if n in self.holder and self.holder[n] != r.robot_id)
        eta = lead_s + d_pick / r.speed_mps
        parts = {
            "distance": 35.0 * max(0.0, 1.0 - d_pick / 40.0),
            "battery": 35.0 * min(1.0, max(0.0, (after - self.cfg.min_operational_battery) /
                                          (100.0 - self.cfg.min_operational_battery))),
            "congestion": 20.0 * (1.0 - min(congestion, 4) / 4.0),
            "time": 10.0 * max(0.0, 1.0 - eta / 120.0),
        }
        score = round(sum(parts.values()), 1)
        return {**row, "feasible": True, "score": score, "eta_s": round(eta, 1),
                "parts": {k: round(v, 1) for k, v in parts.items()}, "reason": "feasible"}

    def _dispatch(self):
        open_tasks = [t for t in self.tasks.values() if t.status in OPEN_TASK]
        if not open_tasks:
            return
        open_tasks.sort(key=lambda t: -self.priority_score(t)[0])
        taken: Set[str] = set()
        reserved_robots: Set[str] = set()
        for task in open_tasks:
            rows, feasible = [], []
            for r in self.robots.values():
                ok, why = self._availability(r)
                if not ok or r.robot_id in taken:
                    rows.append({"robot": r.robot_id, "state": r.state.value, "feasible": False, "score": None,
                                 "battery": round(r.battery, 1),
                                 "reason": "just assigned another task" if ok else why})
                    continue
                ev = self.evaluate(r, task)
                rows.append(ev)
                if ev["feasible"]:
                    feasible.append(ev)
                elif ev["reason"].startswith("battery") and r.state != RobotState.CHARGING:
                    self.needs_charge.add(r.robot_id)
            if feasible:
                pick = max(feasible, key=lambda e: e["score"])
                if task.reserved_for and task.reserved_for in {e["robot"] for e in feasible}:
                    pick = next(e for e in feasible if e["robot"] == task.reserved_for)
                self._assign(task, self.robots[pick["robot"]], rows, pick)
                taken.add(pick["robot"])
            else:
                self._reserve_future(task, rows, reserved_robots)

    def _explain_choice(self, pick: Dict, rows: List[Dict]) -> str:
        parts = [f"{pick['robot']} selected (score {pick['score']}): {pick['distance_m']:.0f} m to pickup, "
                 f"battery {pick['battery']:.0f}% -> {pick['battery_after']:.0f}% after the task."]
        for row in rows:
            if row["robot"] == pick["robot"]:
                continue
            if row["feasible"]:
                parts.append(f"{row['robot']} scored lower ({row['score']}, {row['distance_m']:.0f} m).")
            else:
                parts.append(f"{row['robot']} not selected: {row['reason']}.")
        return " ".join(parts)

    def _assign(self, task: Task, r: Robot, rows: List[Dict], pick: Dict, override_by: Optional[str] = None,
                ai_rec: Optional[str] = None, note: str = ""):
        was_reserved = task.reserved_for
        task.status, task.assigned_robot, task.reserved_for = TaskStatus.ASSIGNED, r.robot_id, None
        task.attempts += 1
        task.started_at = task.started_at if task.started_at is not None else self.now
        self._no_robot_logged.discard(task.task_id)
        prev = r.state
        r.task_id = task.task_id
        self._set_goal(r, Goal.PICKUP, task.pickup)
        if r.moving_to is None and r.state != RobotState.WAITING:
            r.state = RobotState.MOVING
        reason = note or self._explain_choice(pick, rows)
        if was_reserved and was_reserved != r.robot_id and not override_by:
            reason += f" Reservation for {was_reserved} released: {r.robot_id} is free now."
        score, parts = self.priority_score(task)
        self.log.record(self.now, "OVERRIDE" if override_by else "ASSIGN",
                        f"{task.task_id} -> {r.robot_id}", reason, robot_id=r.robot_id, task_id=task.task_id,
                        location=r.node, battery=r.battery, prev_state=prev.value, new_state=r.state.value,
                        alternatives=rows, human_override=bool(override_by), operator_id=override_by,
                        ai_recommendation=ai_rec, sensors={"task_priority": score, "priority_parts": parts})

    def _remaining_work(self, r: Robot) -> Tuple[float, float, str]:
        """(seconds until free, battery used until then, node where it ends) for a busy robot."""
        task = self.tasks.get(r.task_id)
        pos = r.moving_to or r.node
        if task is None:
            return 0.0, 0.0, pos
        v_empty = r.speed_mps
        v_task = r.speed_mps * CARE_PROFILES[task.care].speed_factor
        dwell = max(0.0, (r.dwell_until or self.now) - self.now)
        if task.status == TaskStatus.ASSIGNED:
            d1 = self.map.distance(pos, task.pickup)
            d2 = self.map.distance(task.pickup, task.destination)
            secs = dwell + d1 / v_empty + self.cfg.dwell_pick_s + d2 / v_task + self.cfg.dwell_drop_s
            metres = d1 + d2
        else:
            d2 = self.map.distance(pos, task.destination)
            secs = dwell + d2 / v_task + self.cfg.dwell_drop_s
            metres = d2
        return secs, self._energy(metres), task.destination

    def _reserve_future(self, task: Task, rows: List[Dict], reserved_robots: Set[str]):
        options, behind = [], 0
        for r in self.robots.values():
            if not r.task_id or r.state in STOPPED_STATES \
                    or r.state in (RobotState.PAUSED, RobotState.POSSIBLE_FAULT) or r.estop:
                continue
            if r.robot_id in reserved_robots:
                behind += 1
                continue
            secs, used, end = self._remaining_work(r)
            ev = self.evaluate(r, task, start=end, battery=r.battery - used, lead_s=secs)
            ev["free_in_s"] = round(secs, 1)
            if ev["feasible"]:
                options.append(ev)
        if not options:
            if task.reserved_for or task.task_id not in self._no_robot_logged:
                task.reserved_for, task.status = None, TaskStatus.PENDING
                self._no_robot_logged.add(task.task_id)
                why = (f"Every busy robot's next slot is already reserved for higher-priority work ({behind}); "
                       f"it keeps its place and gains priority while waiting." if behind else
                       "No robot can take it now or after its current job (battery, capability or route); "
                       "it stays queued and is re-checked every cycle.")
                self.log.record(self.now, "QUEUE", f"{task.task_id} waiting", why,
                                task_id=task.task_id, alternatives=rows)
            return
        best = min(options, key=lambda e: e["eta_s"])
        current = next((e for e in options if e["robot"] == task.reserved_for), None)
        if current and current["eta_s"] <= best["eta_s"] + self.cfg.reservation_hysteresis_s:
            reserved_robots.add(current["robot"])
            return
        task.reserved_for, task.status = best["robot"], TaskStatus.RESERVED
        reserved_robots.add(best["robot"])
        busy = ", ".join(f"{e['robot']} free in {e['free_in_s']:.0f} s" for e in sorted(options, key=lambda e: e['free_in_s']))
        self.log.record(self.now, "RESERVE", f"{task.task_id} reserved for {best['robot']}",
                        f"No robot is free. Predicted availability: {busy}. {best['robot']} can start soonest "
                        f"and keeps {best['battery_after']:.0f}% battery after this task; it is assigned "
                        f"automatically when it finishes.",
                        robot_id=best["robot"], task_id=task.task_id, alternatives=options + rows)

    # ------------------------------------------------------------------ goals, dwell, completion

    def _set_goal(self, r: Robot, goal: Optional[Goal], node: Optional[str]):
        r.goal, r.goal_node, r.route = goal, node, []

    def _travel_state(self, r: Robot) -> RobotState:
        if r.goal == Goal.DROP:
            return RobotState.CARE_MODE if r.care != CareLevel.NORMAL else RobotState.DELIVERING
        return RobotState.MOVING if r.goal else RobotState.IDLE

    def _arrive_goal(self, r: Robot):
        task = self.tasks.get(r.task_id) if r.task_id else None
        if r.goal == Goal.PICKUP and task:
            r.state, r.dwell_until = RobotState.PICKING, self.now + self.cfg.dwell_pick_s
        elif r.goal == Goal.DROP and task:
            if r.profile.confirm_at_destination and task.status != TaskStatus.AWAITING_CONFIRMATION \
                    and r.dwell_until is None:
                task.status = TaskStatus.AWAITING_CONFIRMATION
                r.state, r.waiting_for = RobotState.WAITING, "worker confirmation"
                self.log.record(self.now, "CARE", f"{r.robot_id} holding at {r.node}",
                                f"{task.parcel_id} is HIGHLY_FRAGILE: a worker must confirm hand-over.",
                                robot_id=r.robot_id, task_id=task.task_id, location=r.node)
            elif task.status != TaskStatus.AWAITING_CONFIRMATION and r.dwell_until is None:
                r.dwell_until = self.now + self.cfg.dwell_drop_s
        elif r.goal == Goal.CHARGE:
            r.state, r.goal, r.goal_node = RobotState.CHARGING, None, None
            self.needs_charge.discard(r.robot_id)
            self.log.record(self.now, "CHARGE", f"{r.robot_id} charging at {r.node}", f"battery {r.battery:.0f}%",
                            robot_id=r.robot_id, location=r.node, battery=r.battery, new_state="CHARGING")
        else:
            standby = r.pool != PoolRole.NORMAL and r.robot_id not in self.reserve_active
            r.state = RobotState.RESERVED if standby else RobotState.IDLE
            r.goal, r.goal_node = None, None

    def _finish_dwells(self):
        for r in self.robots.values():
            if r.dwell_until is None or self.now < r.dwell_until or r.state in STOPPED_STATES \
                    or r.state == RobotState.PAUSED or r.estop:
                continue
            r.dwell_until = None
            task = self.tasks.get(r.task_id)
            if task is None:
                continue
            if r.goal == Goal.PICKUP:
                care, source = self._lookup_care(task.parcel_id)
                task.care, task.status = care, TaskStatus.CARRYING
                r.care = care
                self._set_goal(r, Goal.DROP, task.destination)
                r.state = self._travel_state(r)
                self.log.record(self.now, "PICKUP", f"{r.robot_id} loaded {task.parcel_id or 'parcel'}",
                                f"Scanned at {r.node}: {source}.", robot_id=r.robot_id, task_id=task.task_id,
                                location=r.node, battery=r.battery, new_state=r.state.value)
                if care != CareLevel.NORMAL:
                    p = r.profile
                    self.log.record(self.now, "CARE", f"{r.robot_id} entered Care Mode ({care.value})",
                                    f"{task.parcel_id} is marked {care.value} in the parcel record: speed "
                                    f"{p.speed_factor:.0%}, acceleration {p.max_accel_mps2} m/s^2, braking {p.braking}, "
                                    f"turns {p.turning}, safety distance {p.safety_distance_m} m"
                                    + (", rough segments avoided" if p.avoid_rough_route else "") + ".",
                                    robot_id=r.robot_id, task_id=task.task_id, location=r.node)
            elif r.goal == Goal.DROP:
                task.status, task.completed_at = TaskStatus.DONE, self.now
                r.task_id, r.care = None, CareLevel.NORMAL
                self._set_goal(r, None, None)
                r.state = RobotState.IDLE
                self.log.record(self.now, "COMPLETE", f"{task.task_id} delivered by {r.robot_id}",
                                f"{task.pickup} -> {task.destination} in {self.now - task.created_at:.0f} s "
                                f"from request.", robot_id=r.robot_id, task_id=task.task_id, location=r.node,
                                battery=r.battery, new_state="IDLE", result="DONE")

    def _park_idle(self):
        for r in self.robots.values():
            if r.state != RobotState.IDLE or r.task_id or r.goal or r.estop:
                continue
            if r.node == r.home or self.map.checkpoints[r.node].kind == "DOCK":
                continue
            home = r.home if r.home and self.holder.get(r.home) in (None, r.robot_id) else None
            if home is None:
                docks = [d for d in self.map.of_kind("DOCK") if d not in self.holder and not self._targeted(d)]
                home = min(docks, key=lambda d: self.map.distance(r.node, d), default=None)
            if home:
                self._set_goal(r, Goal.PARK, home)
                r.state = RobotState.MOVING

    def _targeted(self, node: str) -> bool:
        return any(o.goal_node == node for o in self.robots.values())

    # ------------------------------------------------------------------ traffic

    def _edge_cost(self, r: Robot):
        planned: Dict[str, int] = {}
        for o in self.robots.values():
            if o.robot_id != r.robot_id:
                for n in o.route:
                    planned[n] = planned.get(n, 0) + 1

        def cost(u: str, v: str, length: float) -> float:
            if v in self.blocked_nodes or edge_key(u, v) in self.blocked_edges:
                return math.inf
            c = length
            if edge_key(u, v) in self.map.rough:
                if r.care == CareLevel.HIGHLY_FRAGILE:
                    c += 50.0
                elif r.care == CareLevel.FRAGILE:
                    c += 0.5 * length
            h = self.holder.get(v)
            if h and h != r.robot_id:
                c += self.cfg.congestion_penalty_m
            return c + 0.5 * self.cfg.congestion_penalty_m * planned.get(v, 0)
        return cost

    def _plan(self, r: Robot, avoid: Set[str] = frozenset()) -> Optional[List[str]]:
        path = self.map.shortest(r.node, r.goal_node, edge_cost=self._edge_cost(r), avoid_nodes=avoid)
        if path is None:
            return None
        plain = self.map.shortest(r.node, r.goal_node)
        extra = self.map.path_length(path) - (self.map.path_length(plain) if plain else 0.0)
        if plain and path != plain and extra > 0.5:
            why = []
            for n in plain[1:-1]:
                if n in self.blocked_nodes:
                    why.append(f"{n} blocked ({self.blocked_nodes[n]})")
                elif self.holder.get(n) not in (None, r.robot_id):
                    why.append(f"{n} occupied by {self.holder[n]}")
            if any(edge_key(a, b) in self.map.rough for a, b in zip(plain, plain[1:])) and r.care != CareLevel.NORMAL:
                why.append("rough floor segment avoided for Care Mode")
            self.log.record(self.now, "ROUTE", f"{r.robot_id} via {'-'.join(path)} (+{extra:.0f} m)",
                            "Slightly longer route chosen for the fleet: " + ("; ".join(why) or "less congestion") + ".",
                            robot_id=r.robot_id, task_id=r.task_id, location=r.node,
                            alternatives=[{"route": "-".join(plain), "length_m": round(self.map.path_length(plain), 1)}])
        return path[1:]

    def _route_blocked(self, r: Robot) -> bool:
        hops = [r.moving_to or r.node] + r.route
        return any(n in self.blocked_nodes for n in r.route) or \
            any(edge_key(a, b) in self.blocked_edges for a, b in zip(hops, hops[1:]))

    def _route_invalid(self, r: Robot) -> bool:
        """For a robot standing at a checkpoint."""
        return not r.route or not self.map.adjacent(r.node, r.route[0]) or self._route_blocked(r)

    def _request_key(self, r: Robot):
        task = self.tasks.get(r.task_id) if r.task_id else None
        score = self.priority_score(task)[0] if task else -1.0
        waited = self.now - r.waiting_since if r.waiting_since is not None else 0.0
        return (CARE_RANK[r.care], score, waited, r.robot_id)

    def _traffic(self):
        requests: Dict[str, List[Robot]] = {}
        for r in self.robots.values():
            if (not r.in_service or r.estop or r.moving_to or r.goal_node is None or r.dwell_until is not None
                    or r.state in STOPPED_STATES or r.state in (RobotState.PAUSED, RobotState.POSSIBLE_FAULT,
                                                               RobotState.PICKING, RobotState.CHARGING)):
                continue
            if r.waiting_for == "worker confirmation":
                continue
            if r.node == r.goal_node:
                self._arrive_goal(r)
                continue
            if self._route_invalid(r):
                path = self._plan(r)
                if path is None:
                    self._wait(r, None, f"no open route to {r.goal_node} (blocked zones: "
                                        f"{', '.join(self.blocked_nodes) or 'none'})")
                    continue
                r.route = path
            if self._must_yield(r, r.route[0]):
                self._wait(r, r.route[0], f"giving way: {r.route[0]} is still needed by "
                                          f"{', '.join(sorted(self._yielding[r.robot_id][0]))}")
                continue
            requests.setdefault(r.route[0], []).append(r)

        for node, reqs in requests.items():
            holder = self.holder.get(node)
            if node in self.blocked_nodes:
                for r in reqs:
                    r.route = []
                continue
            if holder is not None and holder not in {r.robot_id for r in reqs}:
                for r in reqs:
                    self._wait(r, node, f"{node} occupied by {holder}")
                continue
            reqs.sort(key=lambda r: (r.robot_id == holder, self._request_key(r)), reverse=True)
            winner = reqs[0]
            if len(reqs) > 1:
                self.log.record(self.now, "TRAFFIC", f"{winner.robot_id} enters {node} first",
                                self._contention_reason(winner, reqs[1:], node), robot_id=winner.robot_id,
                                task_id=winner.task_id, location=node,
                                alternatives=[{"robot": o.robot_id, "key": list(self._request_key(o)[:3])}
                                              for o in reqs])
            self._grant(winner, node)
            for r in reqs[1:]:
                self._wait(r, node, f"{node} granted to {winner.robot_id}", log=False)
        self._resolve_deadlocks()
        self._reroute_long_waits()

    def _must_yield(self, r: Robot, node: str) -> bool:
        entry = self._yielding.get(r.robot_id)
        if entry is None:
            return False
        partners, until = entry
        needed = set()
        for pid in partners:
            p = self.robots[pid]
            if p.state not in STOPPED_STATES:
                needed.update(p.route)
                needed.update(n for n in (p.moving_to, p.goal_node, p.waiting_for) if n)
        if self.now >= until or not needed:
            del self._yielding[r.robot_id]
            return False
        return node in needed

    def _contention_reason(self, w: Robot, losers: List[Robot], node: str) -> str:
        kw = self._request_key(w)
        bits = []
        for o in losers:
            ko = self._request_key(o)
            if kw[0] != ko[0]:
                why = f"{w.robot_id} carries a {w.care.value} parcel"
            elif kw[1] != ko[1]:
                why = f"{w.robot_id} has the higher task priority ({kw[1]:.1f} vs {ko[1]:.1f})"
            elif kw[2] != ko[2]:
                why = f"{w.robot_id} has waited longer ({kw[2]:.0f} s vs {ko[2]:.0f} s)"
            else:
                why = "tie broken by robot ID"
            bits.append(f"{o.robot_id} waits outside {node}: {why}")
        return f"Intersection {node} is granted to one robot at a time. " + "; ".join(bits) + "."

    def _grant(self, r: Robot, node: str):
        was_waiting = r.state == RobotState.WAITING
        self.holder[node] = r.robot_id
        r.moving_to, r.move_started = node, self.now
        r.route.pop(0)
        r.waiting_for, r.waiting_since = None, None
        r.state = self._travel_state(r)
        if was_waiting:
            self.log.record(self.now, "TRAFFIC", f"{r.robot_id} proceeds to {node}", "Checkpoint is free again.",
                            robot_id=r.robot_id, task_id=r.task_id, location=r.node, new_state=r.state.value)

    def _wait(self, r: Robot, node: Optional[str], reason: str, log: bool = True):
        if r.state != RobotState.WAITING or r.waiting_for != node:
            if log or r.state != RobotState.WAITING:
                self.log.record(self.now, "TRAFFIC", f"{r.robot_id} WAIT at {r.node}", reason + " (collision avoidance).",
                                robot_id=r.robot_id, task_id=r.task_id, location=r.node,
                                prev_state=r.state.value, new_state="WAITING")
            r.waiting_since = self.now if r.state != RobotState.WAITING else r.waiting_since
            r.state, r.waiting_for = RobotState.WAITING, node

    def _resolve_deadlocks(self):
        waits = {r.robot_id: self.holder.get(r.waiting_for) for r in self.robots.values()
                 if r.state == RobotState.WAITING and r.waiting_for in self.holder}
        seen: Set[str] = set()
        for start in list(waits):
            if start in seen:
                continue
            chain, cur = [], start
            while cur in waits and cur not in chain:
                chain.append(cur)
                cur = waits[cur]
            seen.update(chain)
            if cur not in chain:
                continue
            cycle = chain[chain.index(cur):]
            members = sorted((self.robots[c] for c in cycle), key=self._request_key)
            held = {n for n, h in self.holder.items() if h in cycle}
            stuck: List[str] = []
            for v in members:
                if self._break_deadlock(v, held, cycle, stuck):
                    break
                stuck.append(v.robot_id)
            else:
                self._raise_alert(members[0], f"deadlock between {', '.join(cycle)} with no free way out",
                                  "Deadlock", location=members[0].node)

    def _break_deadlock(self, v: Robot, held: Set[str], cycle: List[str], stuck: List[str]) -> bool:
        path = self._plan(v, avoid=held - {v.node})
        if path and path[0] not in held:
            v.route = path
            action = f"re-routed via {'-'.join(path)}"
        else:
            spot = self._side_step(v, cycle)
            if spot is None:
                return False
            v.route = [spot]
            action = f"steps aside to {spot} to open the way"
        v.state, v.waiting_for, v.waiting_since = self._travel_state(v), None, None
        self._yielding[v.robot_id] = (set(cycle) - {v.robot_id}, self.now + 30.0)
        who = (f"{', '.join(stuck)} cannot move aside, so {v.robot_id} yields" if stuck else
               f"{v.robot_id} has the lowest right of way, so it yields")
        self.log.record(self.now, "DEADLOCK", f"{v.robot_id} {action}",
                        f"Circular wait detected ({' -> '.join(cycle + [cycle[0]])}); {who} and keeps clear until "
                        f"the others have passed.", robot_id=v.robot_id, task_id=v.task_id, location=v.node)
        return True

    def _side_step(self, v: Robot, cycle: List[str]) -> Optional[str]:
        """A free neighbouring checkpoint that the other robots in the cycle do not need."""
        needed = set()
        for rid in cycle:
            o = self.robots[rid]
            if o is not v:
                needed.update(o.route)
                needed.update(n for n in (o.moving_to, o.goal_node) if n)
        free = [n for n in self.map.adj[v.node]
                if n not in self.holder and n not in self.blocked_nodes and not self._targeted(n)]
        clear = [n for n in free if n not in needed]
        # Prefer an intersection off their path, then a side station (dock, rack, packing) off their path.
        clear.sort(key=lambda n: self.map.checkpoints[n].kind != "INTERSECTION")
        return (clear or free or [None])[0]

    def _reroute_long_waits(self):
        for r in self.robots.values():
            if r.state != RobotState.WAITING or r.waiting_since is None or r.waiting_for not in self.map.checkpoints:
                continue
            if self.now - r.waiting_since < self.cfg.max_wait_before_reroute_s:
                continue
            h = self.holder.get(r.waiting_for)
            if h and self.robots[h].moving_to is not None:
                continue       # holder is moving: the wait will clear by itself
            path = self._plan(r, avoid={r.waiting_for})
            if path and path[0] != r.waiting_for:
                self.log.record(self.now, "REROUTE", f"{r.robot_id} via {'-'.join(path)}",
                                f"Waited {self.now - r.waiting_since:.0f} s for {r.waiting_for} "
                                f"({'held by stationary ' + h if h else 'kept clear for robots it is giving way to'}); "
                                f"alternative route found.", robot_id=r.robot_id, task_id=r.task_id,
                                location=r.node)
                r.route, r.state, r.waiting_for, r.waiting_since = path, self._travel_state(r), None, None

    # ------------------------------------------------------------------ fault detection and recovery

    def _check_health(self):
        for r in self.robots.values():
            if not r.in_service or r.state in STOPPED_STATES or r.estop or r.state == RobotState.PAUSED:
                continue
            if r.motor_fault:
                self._fault(r, RobotState.FAULT, "motor telemetry fault")
                continue
            if r.battery <= 0.5:
                self._fault(r, RobotState.FAULT, "battery depleted")
                continue
            if r.moving_to is None:
                continue
            if r.obstacle_since is not None:
                blocked_for = self.now - r.obstacle_since
                if blocked_for >= self.cfg.obstacle_timeout_s:
                    self._fault(r, RobotState.FAULT, f"obstacle sensor has continuously detected an obstruction "
                                                     f"for {blocked_for:.0f} s")
                continue
            length = self.map.adj[r.node][r.moving_to]
            expected = length / (r.speed_mps * r.profile.speed_factor)
            limit = max(self.cfg.stall_min_s, self.cfg.stall_factor * expected)
            elapsed = self.now - r.move_started
            if r.state != RobotState.POSSIBLE_FAULT and elapsed > limit:
                checks = {"state": r.state.value, "reservation": f"granted {r.moving_to}", "obstacle": "clear",
                          "motor": "OK", "comm": f"{self.now - r.last_comm:.1f} s ago", "battery": f"{r.battery:.0f}%",
                          "estop": "off", "waiting_permission": "no"}
                prev = r.state
                r.state, r.suspect_since = RobotState.POSSIBLE_FAULT, self.now
                self.log.record(self.now, "HEALTH", f"{r.robot_id} POSSIBLE_FAULT",
                                f"No checkpoint between {r.node} and {r.moving_to} for {elapsed:.0f} s (expected "
                                f"{expected:.0f} s). It is not waiting for permission and telemetry shows no cause; "
                                f"confirming for {self.cfg.confirm_window_s:.0f} s before recovery.",
                                robot_id=r.robot_id, task_id=r.task_id, location=r.node, battery=r.battery,
                                prev_state=prev.value, new_state="POSSIBLE_FAULT", sensors=checks)
            elif r.state == RobotState.POSSIBLE_FAULT and self.now - r.suspect_since >= self.cfg.confirm_window_s:
                self._fault(r, RobotState.FAULT, f"stalled between {r.node} and {r.moving_to} for "
                                                 f"{elapsed:.0f} s with no progress (cause not reported)")

    def _fault(self, r: Robot, state: RobotState, reason: str):
        prev = r.state
        stopped_at = r.move_started if r.moving_to else self.now
        r.state, r.fault_reason, r.stopped_at = state, reason, stopped_at
        r.suspect_since, r.dwell_until = None, None
        zone_nodes = [n for n in (r.node, r.moving_to) if n]
        zone = "-".join(zone_nodes)
        for n in zone_nodes:
            self.blocked_nodes[n] = f"{r.robot_id} {state.value}"
        if r.moving_to:
            self.blocked_edges[edge_key(r.node, r.moving_to)] = f"{r.robot_id} {state.value}"
        self.log.record(self.now, "FAULT", f"{r.robot_id} {state.value}", reason[0].upper() + reason[1:] + ".",
                        robot_id=r.robot_id, task_id=r.task_id, location=zone, battery=r.battery,
                        prev_state=prev.value, new_state=state.value,
                        sensors={"motor_fault": r.motor_fault, "obstacle": r.obstacle_since is not None,
                                 "comm_age_s": round(self.now - r.last_comm, 1), "battery": round(r.battery, 1)})
        self.log.record(self.now, "RECOVERY", f"BLOCKED_ZONE = {zone}",
                        f"Other robots may not enter {zone} until a worker confirms it is clear.",
                        robot_id=r.robot_id, location=zone)

        affected = []
        for o in self.robots.values():
            if o is r or not o.route or not self._route_blocked(o):
                continue
            affected.append(o.robot_id)
            if o.moving_to:
                o.route = []        # re-plans at its next checkpoint
                self.log.record(self.now, "REROUTE", f"{o.robot_id} re-plans at {o.moving_to}",
                                f"Remaining route crossed blocked zone {zone}.", robot_id=o.robot_id,
                                task_id=o.task_id, location=o.moving_to)
            else:
                o.route = self._plan(o) or []
                if o.route:
                    self.log.record(self.now, "REROUTE", f"{o.robot_id} via {'-'.join([o.node] + o.route)}",
                                    f"Previous route crossed blocked zone {zone}.", robot_id=o.robot_id,
                                    task_id=o.task_id, location=o.node)
                else:
                    self._wait(o, None, f"no route around blocked zone {zone}")

        task = self.tasks.get(r.task_id) if r.task_id else None
        task_note, reassigned = "no task on board", None
        if task:
            if task.status == TaskStatus.ASSIGNED:
                task.status, task.assigned_robot = TaskStatus.PENDING, None
                task_note = f"{task.task_id} returned to the queue (parcel not yet picked)"
            else:
                task.status = TaskStatus.NEEDS_HUMAN
                task_note = (f"{task.task_id}: parcel {task.parcel_id or ''} is on board {r.robot_id}; a worker must "
                             f"retrieve it, then re-queue the task")
        r.task_id, r.route = None, []
        r.goal, r.goal_node = None, None

        if task and task.status == TaskStatus.PENDING:
            self._activate_emergency_reserve(f"{r.robot_id} {state.value}")
            self._dispatch()
            reassigned = task.assigned_robot or (f"reserved for {task.reserved_for}" if task.reserved_for else None)

        alert = self._raise_alert(r, reason, "Robot stopped", location=zone, task=task)
        alert.blocked_route = zone
        alert.system_action = "route blocked; affected robots re-routed; " + task_note
        alert.task_reassigned_to = reassigned
        alert.affected_robots = affected
        self.log.record(self.now, "RECOVERY", f"{r.robot_id} recovery actions complete",
                        f"{task_note}; reassigned to {reassigned or 'nobody yet'}; re-routed: "
                        f"{', '.join(affected) or 'none'}; technician notified ({alert.alert_id}). "
                        f"Restart needs inspection and operator approval.",
                        robot_id=r.robot_id, task_id=task.task_id if task else None, location=zone,
                        result="AWAITING_INSPECTION")

    def _raise_alert(self, r: Robot, reason: str, title: str, location: str = "", task: Optional[Task] = None) -> Alert:
        self._alert_seq += 1
        a = Alert(alert_id=f"ALERT_{self._alert_seq:03d}", robot_id=r.robot_id, location=location or r.node,
                  reason=reason, created_at=self.now, task_id=task.task_id if task else r.task_id, battery=r.battery)
        self.alerts[a.alert_id] = a
        self.log.record(self.now, "ALERT", f"{title}: {r.robot_id}", reason, robot_id=r.robot_id,
                        location=a.location)
        return a

    def stopped_robot_report(self, robot_id: str) -> Optional[Dict]:
        """Worker view for a stopped robot."""
        r = self.robots[robot_id]
        alert = next((a for a in reversed(list(self.alerts.values()))
                      if a.robot_id == robot_id and a.technician_status != "RESOLVED"), None)
        if alert is None:
            return None
        task = self.tasks.get(alert.task_id) if alert.task_id else None
        return {
            "robot": robot_id, "state": r.state.value, "location": alert.location,
            "stopped_for_s": round(self.now - (r.stopped_at or alert.created_at), 1),
            "reason": alert.reason,
            "current_task": f"{task.pickup} -> {task.destination} ({task.task_id})" if task else None,
            "battery": round(r.battery, 1), "blocked_route": alert.blocked_route,
            "system_action": alert.system_action, "task_reassigned_to": alert.task_reassigned_to,
            "affected_robots": alert.affected_robots, "technician_status": alert.technician_status,
            "worker_options": alert.worker_options, "alert": alert.alert_id,
        }

    # ------------------------------------------------------------------ battery and charging

    def _free_chargers(self) -> List[str]:
        return [c for c in self.map.of_kind("CHARGER")
                if c not in self.holder and c not in self.blocked_nodes and not self._targeted(c)]

    def _send_to_charge(self, r: Robot, reason: str, operator: Optional[str] = None) -> bool:
        chargers = self._free_chargers()
        if not chargers:
            return False
        c = min(chargers, key=lambda x: self.map.distance(r.moving_to or r.node, x))
        self._set_goal(r, Goal.CHARGE, c)
        if r.moving_to is None:
            r.state = RobotState.MOVING
        self.log.record(self.now, "CHARGE", f"{r.robot_id} -> {c}", reason, robot_id=r.robot_id,
                        location=r.node, battery=r.battery, human_override=bool(operator), operator_id=operator)
        return True

    def _manage_charging(self):
        cfg = self.cfg
        for r in self.robots.values():
            if r.state != RobotState.CHARGING:
                continue
            if r.battery >= cfg.charge_target:
                self._set_goal(r, Goal.PARK, r.home)
                r.state = RobotState.MOVING
                self.log.record(self.now, "CHARGE", f"{r.robot_id} leaves charger",
                                f"Reached {r.battery:.0f}% (target {cfg.charge_target:.0f}%).",
                                robot_id=r.robot_id, location=r.node, battery=r.battery, new_state="MOVING")
        idle = [r for r in self.robots.values() if self._availability(r)[0] and r.state != RobotState.CHARGING
                and r.goal in (None, Goal.PARK)]
        overload = self.mode in (FleetMode.OVERLOAD, FleetMode.HUMAN_ASSISTANCE)
        def margin(r: Robot) -> float:
            return r.battery - self._energy(self._nearest_charger_distance(r.moving_to or r.node))
        must = sorted((r for r in idle if margin(r) < cfg.min_operational_battery), key=lambda r: r.battery)
        want = sorted((r for r in idle if r not in must and not overload and
                       (r.battery < cfg.charge_threshold or r.robot_id in self.needs_charge)), key=lambda r: r.battery)
        for r in must:
            if not self._send_to_charge(r, f"Battery {r.battery:.0f}% leaves ~{margin(r):.0f}% after the trip to the "
                                           f"nearest charger (floor {cfg.min_operational_battery:.0f}%): charging "
                                           f"is mandatory."):
                break
        if want and self.now - self._last_voluntary_charge >= cfg.charge_stagger_s:
            r = want[0]
            others = ", ".join(f"{o.robot_id} {o.battery:.0f}%" for o in want[1:])
            why = (f"Lowest battery among robots due for charge ({r.battery:.0f}%)."
                   if r.robot_id not in self.needs_charge else
                   f"Battery {r.battery:.0f}% is too low for the queued work (task would breach the reserve).")
            if others:
                why += f" Staggered: {others} keep working on short tasks and charge next."
            if self._send_to_charge(r, why):
                self._last_voluntary_charge = self.now
        self.needs_charge -= {r.robot_id for r in self.robots.values() if r.state == RobotState.CHARGING}

    # ------------------------------------------------------------------ reserve pool and fleet modes

    def pending_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.status in OPEN_TASK)

    def _activate(self, r: Robot, reason: str, operator: Optional[str] = None):
        self.reserve_active.add(r.robot_id)
        r.state = RobotState.IDLE
        self._last_reserve_change = self.now
        self.log.record(self.now, "RESERVE_POOL", f"activated {r.robot_id} ({r.pool.value})", reason,
                        robot_id=r.robot_id, location=r.node, human_override=bool(operator), operator_id=operator,
                        new_state="IDLE")

    def _activate_emergency_reserve(self, cause: str):
        for r in self.robots.values():
            if r.pool == PoolRole.EMERGENCY_RESERVE and r.robot_id not in self.reserve_active and r.in_service \
                    and r.state == RobotState.RESERVED:
                self._activate(r, f"Emergency reserve activated to replace capacity lost ({cause}).")
                return

    def _manage_reserve_pool(self):
        pending = self.pending_count()
        if pending > self.cfg.overload_threshold and \
                self.now - self._last_reserve_change >= self.cfg.reserve_activate_interval_s:
            for r in self.robots.values():
                if r.pool == PoolRole.PEAK_RESERVE and r.robot_id not in self.reserve_active and \
                        r.state == RobotState.RESERVED and r.in_service:
                    self._activate(r, f"Pending tasks {pending} > overload threshold {self.cfg.overload_threshold}.")
                    break
        if pending == 0:
            self._queue_empty_since = self._queue_empty_since if self._queue_empty_since is not None else self.now
        else:
            self._queue_empty_since = None
        if self._queue_empty_since is not None and self.now - self._queue_empty_since >= self.cfg.reserve_release_after_s:
            for rid in list(self.reserve_active):
                r = self.robots[rid]
                if r.task_id or r.state not in (RobotState.IDLE, RobotState.CHARGING):
                    continue
                self.reserve_active.discard(rid)
                self._last_reserve_change = self.now
                if r.state == RobotState.IDLE:
                    r.state = RobotState.RESERVED if r.node == r.home else RobotState.MOVING
                    if r.node != r.home:
                        self._set_goal(r, Goal.PARK, r.home)
                self.log.record(self.now, "RESERVE_POOL", f"{rid} back to standby",
                                f"Queue empty for {self.now - self._queue_empty_since:.0f} s: extra capacity released.",
                                robot_id=rid, location=r.node)

    def capacity_report(self) -> Dict:
        active = [r for r in self.robots.values() if r.in_service and r.state not in STOPPED_STATES
                  and r.state != RobotState.RESERVED]
        busy = [r for r in active if r.task_id]
        done = [t for t in self.tasks.values() if t.status == TaskStatus.DONE and t.started_at is not None]
        done.sort(key=lambda t: t.completed_at)
        if done:
            avg = sum(t.completed_at - t.started_at for t in done[-20:]) / len(done[-20:])
        else:
            avg = 60.0
        pending = self.pending_count()
        high = sum(1 for t in self.tasks.values() if t.status in OPEN_TASK and
                   t.priority in (TaskPriority.HIGH, TaskPriority.CRITICAL))
        n = max(len(active), 1)
        recent = [t for t in self.tasks.values() if t.created_at >= self.now - 600.0]
        window = max(min(600.0, self.now), 1.0)
        arrival_per_h = len(recent) * 3600.0 / window
        throughput_per_h = len(active) * 3600.0 / max(avg, 1.0)
        rec = "No action needed."
        if self.mode == FleetMode.HUMAN_ASSISTANCE:
            rec = "Activate standby robot or human-assisted transport."
        elif self.mode == FleetMode.OVERLOAD:
            rec = "Urgent work first; non-critical tasks delayed; reserve robots activating."
        note = ""
        if arrival_per_h > throughput_per_h and len(recent) >= 5:
            note = (f"Demand ~{arrival_per_h:.0f} tasks/h exceeds fleet throughput ~{throughput_per_h:.0f} tasks/h. "
                    f"Software can order and queue work but cannot create carrying capacity: add robots or people.")
        return {"mode": self.mode.value, "fleet_capacity_pct": round(100.0 * len(busy) / n),
                "active_robots": f"{len(busy)}/{len(active)}", "pending_tasks": pending,
                "high_priority_pending": high, "avg_task_s": round(avg, 1),
                "estimated_queue_clearance_min": round(pending * avg / n / 60.0, 1),
                "throughput_per_h": round(throughput_per_h), "arrival_per_h": round(arrival_per_h),
                "recommendation": rec, "capacity_note": note}

    def _update_mode(self):
        pending = self.pending_count()
        recovering = any(r.state in (RobotState.FAULT, RobotState.OFFLINE) and r.in_service
                         for r in self.robots.values())
        active = [r for r in self.robots.values() if self._availability(r)[0] or r.task_id]
        overload = pending > self.cfg.overload_threshold
        self._overload_since = (self._overload_since if self._overload_since is not None else self.now) if overload else None
        human = self._human_requested is not None or \
            (overload and self.now - self._overload_since >= self.cfg.human_assist_after_s) or (pending and not active)
        if human:
            mode = FleetMode.HUMAN_ASSISTANCE
        elif recovering:
            mode = FleetMode.RECOVERY
        elif overload:
            mode = FleetMode.OVERLOAD
        elif pending or (active and all(r.task_id for r in active)):
            mode = FleetMode.BUSY
        else:
            mode = FleetMode.NORMAL
        if mode != self.mode:
            cap = self.capacity_report()
            reason = {
                FleetMode.NORMAL: "Robots available; jobs assigned normally.",
                FleetMode.BUSY: (f"All robots have work; {pending} task(s) queued and reserved for the robots "
                                 f"predicted to free up first." if pending else "All robots have work."),
                FleetMode.OVERLOAD: f"{pending} pending tasks > threshold {self.cfg.overload_threshold}: urgent work "
                                    f"first, non-critical tasks delayed, queue clears in "
                                    f"~{cap['estimated_queue_clearance_min']} min.",
                FleetMode.RECOVERY: "A robot has stopped: zone blocked, fleet re-routed, work reassigned.",
                FleetMode.HUMAN_ASSISTANCE: self._human_requested or
                    f"Fleet saturated ({cap['active_robots']} busy, {pending} pending, "
                    f"{cap['high_priority_pending']} high priority). {cap['recommendation']}",
            }[mode]
            self.log.record(self.now, "MODE", f"{self.mode.value} -> {mode.value}", reason,
                            sensors=cap)
            self.mode = mode

    # ------------------------------------------------------------------ worker controls (all logged)

    def _op(self, kind: str, operator: str, decision: str, reason: str, **kw):
        self.log.record(self.now, kind, decision, reason, human_override=kind == "OVERRIDE", operator_id=operator, **kw)

    def emergency_stop(self, operator: str, robot_id: Optional[str] = None, reason: str = ""):
        if robot_id:
            self.robots[robot_id].estop = True
        else:
            self.estop_all = True
        self._op("ESTOP", operator, f"EMERGENCY STOP {robot_id or 'ALL ROBOTS'}", reason or "operator command",
                 robot_id=robot_id)

    def release_emergency_stop(self, operator: str, robot_id: Optional[str] = None):
        targets = [self.robots[robot_id]] if robot_id else list(self.robots.values())
        if robot_id:
            self.robots[robot_id].estop = False
        else:
            self.estop_all = False
        for r in targets:
            if r.moving_to:
                r.move_started = self.now          # stall timers restart from the release
        self._op("ESTOP", operator, f"E-stop released {robot_id or 'ALL ROBOTS'}", "operator confirmed area safe",
                 robot_id=robot_id)

    def pause_robot(self, robot_id: str, operator: str, reason: str = ""):
        r = self.robots[robot_id]
        if r.state in STOPPED_STATES or r.state == RobotState.PAUSED:
            return False
        r.paused_state, r.state = r.state, RobotState.PAUSED
        self._op("OPERATOR", operator, f"pause {robot_id}", reason or "operator command", robot_id=robot_id,
                 new_state="PAUSED")
        return True

    def resume_robot(self, robot_id: str, operator: str):
        r = self.robots[robot_id]
        if r.state != RobotState.PAUSED:
            return False
        r.state, r.paused_state = r.paused_state or RobotState.IDLE, None
        if r.moving_to:
            r.move_started = self.now
        self._op("OPERATOR", operator, f"resume {robot_id}", "operator command", robot_id=robot_id,
                 new_state=r.state.value)
        return True

    def _unassign(self, task: Task):
        r = self.robots.get(task.assigned_robot) if task.assigned_robot else None
        if r and r.task_id == task.task_id:
            r.task_id = None
            self._set_goal(r, None, None)
            if r.state not in STOPPED_STATES and r.state != RobotState.PAUSED:
                r.state = RobotState.IDLE
        task.assigned_robot = None

    def pause_task(self, task_id: str, operator: str, reason: str = "") -> Tuple[bool, str]:
        t = self.tasks[task_id]
        if t.status in (TaskStatus.CARRYING, TaskStatus.AWAITING_CONFIRMATION):
            return False, "parcel is on board: pause the robot instead"
        if t.status not in OPEN_TASK | {TaskStatus.ASSIGNED}:
            return False, f"task is {t.status.value}"
        self._unassign(t)
        t.paused_from, t.status, t.reserved_for = t.status, TaskStatus.PAUSED, None
        self._op("OPERATOR", operator, f"pause {task_id}", reason or "operator command", task_id=task_id)
        return True, "paused"

    def resume_task(self, task_id: str, operator: str) -> bool:
        t = self.tasks[task_id]
        if t.status != TaskStatus.PAUSED:
            return False
        t.status = TaskStatus.PENDING
        self._op("OPERATOR", operator, f"resume {task_id}", "back in the queue", task_id=task_id)
        return True

    def change_priority(self, task_id: str, priority: TaskPriority, operator: str, reason: str = ""):
        t = self.tasks[task_id]
        old, t.priority = t.priority, priority
        self._op("OVERRIDE", operator, f"{task_id} priority {old.value} -> {priority.value}",
                 reason or "operator command", task_id=task_id)

    def reassign_task(self, task_id: str, robot_id: str, operator: str, reason: str) -> Tuple[bool, str]:
        t = self.tasks[task_id]
        r = self.robots[robot_id]
        if t.status not in OPEN_TASK | {TaskStatus.ASSIGNED, TaskStatus.PAUSED}:
            return False, f"task is {t.status.value}; parcel already picked or finished"
        ok, why = self._availability(r)
        if not ok and not (r.task_id == task_id):
            return False, f"{robot_id} cannot take work: {why}"
        ev = self.evaluate(r, t)
        if ev["reason"] == "no open route" or ev["battery_after"] < self.cfg.recovery_reserve:
            return False, f"refused, safety limit: {ev['reason']}"
        rows = [self.evaluate(o, t) if self._availability(o)[0] else
                {"robot": o.robot_id, "feasible": False, "score": None, "reason": self._availability(o)[1]}
                for o in self.robots.values()]
        best = max((x for x in rows if x["feasible"]), key=lambda x: x["score"], default=None)
        ai_rec = best["robot"] if best else (t.assigned_robot or t.reserved_for)
        if t.assigned_robot and t.assigned_robot != robot_id:
            self._unassign(t)
        warn = "" if ev["feasible"] else f" AI warning overridden: {ev['reason']}."
        self._assign(t, r, rows, ev, override_by=operator, ai_rec=ai_rec,
                     note=f"AI recommended {ai_rec}; supervisor selected {robot_id}. Reason: {reason}.{warn}")
        return True, "reassigned"

    def send_robot_to_charge(self, robot_id: str, operator: str) -> Tuple[bool, str]:
        r = self.robots[robot_id]
        t = self.tasks.get(r.task_id) if r.task_id else None
        if t and t.status != TaskStatus.ASSIGNED:
            return False, "parcel on board: deliver or pause first"
        if t:
            self._unassign(t)
            t.status = TaskStatus.PENDING
        return (True, "sent") if self._send_to_charge(r, "Operator request.", operator) else (False, "no free charger")

    def remove_robot(self, robot_id: str, operator: str, reason: str = "maintenance"):
        """Take a robot out of service (maintenance). Its checkpoint stays blocked until restart approval."""
        r = self.robots[robot_id]
        t = self.tasks.get(r.task_id) if r.task_id else None
        if t:
            if t.status == TaskStatus.ASSIGNED:
                self._unassign(t)
                t.status = TaskStatus.PENDING
            else:
                t.status = TaskStatus.NEEDS_HUMAN
                r.task_id = None
        for n in (r.node, r.moving_to):
            if n and self.map.checkpoints[n].kind == "INTERSECTION":
                self.blocked_nodes[n] = f"{robot_id} MAINTENANCE"
        r.state, r.in_service, r.route, r.goal, r.goal_node = RobotState.MAINTENANCE, False, [], None, None
        self._op("OPERATOR", operator, f"{robot_id} removed from fleet", reason, robot_id=robot_id,
                 new_state="MAINTENANCE", location=r.node)

    def approve_restart(self, robot_id: str, operator: str, confirmed_node: str, aisle_clear: bool,
                        notes: str = "") -> Tuple[bool, str]:
        """Restart only after inspection: real position, clear aisle, stale reservations, task ownership."""
        r = self.robots[robot_id]
        problems = []
        if r.state not in STOPPED_STATES | {RobotState.POSSIBLE_FAULT}:
            problems.append(f"robot is {r.state.value}, not stopped")
        if self.now - r.last_comm > self.cfg.comm_timeout_s:
            problems.append("robot is not reporting telemetry")
        if r.motor_fault:
            problems.append("motor fault still reported")
        if r.obstacle_since is not None:
            problems.append("obstacle still detected")
        if confirmed_node not in self.map.checkpoints:
            problems.append(f"unknown checkpoint {confirmed_node}")
        elif self.holder.get(confirmed_node) not in (None, robot_id):
            problems.append(f"{confirmed_node} is occupied by {self.holder[confirmed_node]}")
        if not aisle_clear:
            problems.append("aisle not confirmed clear")
        duplicate = [t.task_id for t in self.tasks.values() if t.assigned_robot == robot_id and t.status in ACTIVE_TASK]
        if duplicate:
            problems.append(f"still owns {', '.join(duplicate)}")
        checks = {"position": confirmed_node, "aisle_clear": aisle_clear, "telemetry_age_s": round(self.now - r.last_comm, 1),
                  "motor_fault": r.motor_fault, "battery": round(r.battery, 1)}
        if problems:
            self._op("RESTART", operator, f"restart of {robot_id} refused", "; ".join(problems) + ".",
                     robot_id=robot_id, sensors=checks)
            return False, "; ".join(problems)
        for n, h in list(self.holder.items()):
            if h == robot_id:
                del self.holder[n]
        for n, why in list(self.blocked_nodes.items()):
            if why.startswith(f"{robot_id} "):
                del self.blocked_nodes[n]
        for e, why in list(self.blocked_edges.items()):
            if why.startswith(f"{robot_id} "):
                del self.blocked_edges[e]
        self.holder[confirmed_node] = robot_id
        r.node, r.moving_to, r.route, r.goal, r.goal_node = confirmed_node, None, [], None, None
        r.state, r.fault_reason, r.in_service, r.stopped_at = RobotState.IDLE, "", True, None
        r.last_checkpoint_at, r.care = self.now, CareLevel.NORMAL
        for a in self.alerts.values():
            if a.robot_id == robot_id and a.technician_status != "RESOLVED":
                a.technician_status = "RESOLVED"
        self._op("RESTART", operator, f"{robot_id} restarted at {confirmed_node}",
                 "Inspection complete: position confirmed, aisle clear, old reservations and blocked zone cleared, "
                 "task already handed to another robot. " + notes, robot_id=robot_id, sensors=checks,
                 new_state="IDLE", location=confirmed_node)
        return True, "restarted"

    def block_route(self, a: str, operator: str, b: Optional[str] = None, reason: str = ""):
        if b:
            self.blocked_edges[edge_key(a, b)] = f"operator {operator}: {reason}"
        else:
            self.blocked_nodes[a] = f"operator {operator}: {reason}"
        for o in self.robots.values():
            if o.route and self._route_blocked(o):
                o.route = []
        self._op("OPERATOR", operator, f"block {a}{'-' + b if b else ''}", reason or "operator command")

    def open_route(self, a: str, operator: str, b: Optional[str] = None):
        if b:
            self.blocked_edges.pop(edge_key(a, b), None)
        else:
            self.blocked_nodes.pop(a, None)
        self._op("OPERATOR", operator, f"open {a}{'-' + b if b else ''}", "operator command")

    def acknowledge_alert(self, alert_id: str, operator: str):
        a = self.alerts[alert_id]
        a.technician_status, a.acknowledged_by = "ACKNOWLEDGED", operator
        self._op("OPERATOR", operator, f"acknowledged {alert_id}", a.reason, robot_id=a.robot_id)

    def confirm_delivery(self, task_id: str, operator: str) -> bool:
        t = self.tasks[task_id]
        if t.status != TaskStatus.AWAITING_CONFIRMATION:
            return False
        r = self.robots[t.assigned_robot]
        t.status, r.waiting_for = TaskStatus.CARRYING, None
        r.state = self._travel_state(r)
        r.dwell_until = self.now + self.cfg.dwell_drop_s
        self._op("OPERATOR", operator, f"hand-over confirmed for {task_id}", "worker at destination",
                 task_id=task_id, robot_id=r.robot_id)
        return True

    def requeue_task(self, task_id: str, operator: str, pickup: Optional[str] = None) -> bool:
        t = self.tasks[task_id]
        if t.status != TaskStatus.NEEDS_HUMAN:
            return False
        t.pickup = pickup or t.pickup
        t.status, t.assigned_robot = TaskStatus.PENDING, None
        self._op("OPERATOR", operator, f"{task_id} re-queued from {t.pickup}", "parcel retrieved by worker",
                 task_id=task_id)
        return True

    def activate_reserve(self, robot_id: str, operator: str):
        self._activate(self.robots[robot_id], "Operator activated standby robot.", operator)

    def request_human_assistance(self, operator: str, reason: str):
        self._human_requested = reason
        self._op("OPERATOR", operator, "human assistance requested", reason)

    def clear_human_assistance(self, operator: str):
        self._human_requested = None
        self._op("OPERATOR", operator, "human assistance cleared", "operator command")

    # ------------------------------------------------------------------ views

    def summary(self) -> Dict:
        return {
            "time": self.now, "mode": self.mode.value, "estop": self.estop_all,
            "robots": {rid: {"state": r.state.value, "node": r.node, "moving_to": r.moving_to,
                             "battery": round(r.battery, 1), "task": r.task_id, "goal": r.goal_node,
                             "care": r.care.value, "pool": r.pool.value, "fault": r.fault_reason or None,
                             "last_checkpoint_age_s": round(self.now - r.last_checkpoint_at, 1)}
                       for rid, r in self.robots.items()},
            "queue": [{"task": t.task_id, "status": t.status.value, "score": self.priority_score(t)[0],
                       "reserved_for": t.reserved_for, "care": t.care.value,
                       "waiting_s": round(self.now - t.created_at, 1)}
                      for t in sorted((t for t in self.tasks.values() if t.status in OPEN_TASK),
                                      key=lambda t: -self.priority_score(t)[0])],
            "blocked": {**self.blocked_nodes, **{f"{a}-{b}": v for (a, b), v in self.blocked_edges.items()}},
            "alerts": [a.alert_id for a in self.alerts.values() if a.technician_status != "RESOLVED"],
            "capacity": self.capacity_report(),
        }

    def _clock(self, now: Optional[float]):
        if now is not None and now > self.now:
            self.now = now
