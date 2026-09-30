"""
Scripted DUO-WARE fleet scenarios on the demo warehouse. Used by tools/fleet_demo.py and the tests.

Each builder returns (manager, simulator, info) after running the scenario, so callers can read
the decision log, alerts and final state.
"""

from typing import Dict, Tuple

from .layout import demo_warehouse
from .manager import FleetConfig, FleetManager
from .models import CareLevel, Parcel, PoolRole, RobotState, TaskPriority, TaskStatus
from .sim import FleetSimulator


def _fleet(config: FleetConfig = None, robots=(), dt: float = 0.5):
    fm = FleetManager(demo_warehouse(), config)
    for spec in robots:
        rid, node, battery = spec[:3]
        pool = spec[3] if len(spec) > 3 else PoolRole.NORMAL
        fm.register_robot(rid, node, battery=battery, pool=pool, home=node if node.startswith("DOCK") else None)
    return fm, FleetSimulator(fm, dt=dt)


def robot_selection() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """Spec example: A is near but at 21 %, B is farther at 81 %, C is nearest but busy."""
    fm, sim = _fleet(robots=[("A", "C02", 21.0), ("B", "C06", 81.0), ("C", "C01", 65.0)])
    busy = fm.submit_task("RACK_A12", "PACKING_P01", now=0.0)
    sim.step()
    task = fm.submit_task("RACK_A14", "PACKING_P03", TaskPriority.HIGH)
    sim.step()
    return fm, sim, {"task": task, "busy_task": busy}


def all_robots_busy() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """Three robots busy; Task D arrives, is reserved for the robot predicted to finish first."""
    fm, sim = _fleet(robots=[("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R03", 90.0), ("R03", "DOCK_R04", 90.0)])
    fm.submit_task("RACK_A12", "PACKING_P03", now=0.0)       # long job
    fm.submit_task("RACK_B07", "PACKING_P03")                 # short job
    fm.submit_task("RACK_B03", "PACKING_P01")
    sim.run(2.0)
    task_d = fm.submit_task("RACK_A14", "PACKING_P01", TaskPriority.HIGH, parcel_id=None)
    sim.step()
    reserved_for = fm.tasks[task_d].reserved_for
    sim.run(240.0, until=lambda m: m.tasks[task_d].status == TaskStatus.DONE)
    return fm, sim, {"task": task_d, "reserved_for": reserved_for}


def failure_recovery() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """R03 suffers a motor fault in the middle of the grid while another robot needs that aisle."""
    fm, sim = _fleet(robots=[("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R02", 90.0),
                             ("R03", "DOCK_R03", 90.0), ("R04", "DOCK_R04", 90.0)])
    t_fail = fm.submit_task("RACK_A14", "PACKING_P01", now=0.0)
    sim.step()
    victim = fm.tasks[t_fail].assigned_robot
    # Let it get into the grid, then stop its motor on the next aisle segment.
    sim.run(60.0, until=lambda m: m.robots[victim].moving_to in ("C05", "C02", "C04") and
            m.robots[victim].node in ("C08", "C05", "C07"))
    sim.inject(victim, "motor")
    zone = (fm.robots[victim].node, fm.robots[victim].moving_to)
    other = fm.submit_task("RACK_A12", "PACKING_P03")
    sim.run(150.0, until=lambda m: m.tasks[t_fail].status == TaskStatus.DONE and
            m.tasks[other].status == TaskStatus.DONE)
    # Technician: first attempt while the motor fault is still reported, then after the repair.
    alert = next(iter(fm.alerts))
    fm.acknowledge_alert(alert, "Tech02")
    worker_view = fm.stopped_robot_report(victim)
    refused = fm.approve_restart(victim, "Supervisor01", confirmed_node=zone[0], aisle_clear=True)
    sim.clear(victim)
    sim.step()
    restarted = fm.approve_restart(victim, "Supervisor01", confirmed_node=zone[0], aisle_clear=True,
                                   notes="Motor connector reseated.")
    sim.run(40.0)
    return fm, sim, {"victim": victim, "task": t_fail, "other": other, "zone": zone,
                     "refused": refused, "restarted": restarted, "worker_view": worker_view}


def silent_stall() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """A robot stops with no reported cause: POSSIBLE_FAULT first, FAULT only after confirmation."""
    fm, sim = _fleet(robots=[("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R03", 90.0)])
    tid = fm.submit_task("RACK_A12", "PACKING_P03", now=0.0)
    sim.step()
    rid = fm.tasks[tid].assigned_robot
    sim.run(30.0, until=lambda m: m.robots[rid].moving_to is not None and
            m.map.checkpoints[m.robots[rid].node].kind == "INTERSECTION")
    sim.inject(rid, "stall")
    stalled_at = sim.t
    sim.run(60.0, until=lambda m: m.robots[rid].state == RobotState.FAULT)
    sim.run(120.0, until=lambda m: m.tasks[tid].status == TaskStatus.DONE)
    return fm, sim, {"robot": rid, "task": tid, "stalled_at": stalled_at}


def intersection_contention() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """Two robots request C05 in the same cycle; the one carrying a fragile parcel goes first."""
    fm, sim = _fleet(robots=[("R01", "C04", 90.0), ("R02", "C06", 90.0)])
    fm.register_parcel(Parcel("P237", "glassware", 2.0, CareLevel.FRAGILE))
    fm.robots["R01"].home = "DOCK_R01"
    fm.robots["R02"].home = "DOCK_R04"
    t1 = fm.submit_task("C04", "PACKING_P01", parcel_id="P237", now=0.0)
    t2 = fm.submit_task("C06", "RACK_B03")
    # Pin the assignments so both robots head through C05 at the same moment.
    fm.reassign_task(t1, "R01", "demo", "scenario setup")
    fm.reassign_task(t2, "R02", "demo", "scenario setup")
    sim.run(20.0, until=lambda m: any(e.kind == "TRAFFIC" and "first" in e.decision for e in m.log.events))
    sim.run(120.0, until=lambda m: all(m.tasks[t].status == TaskStatus.DONE for t in (t1, t2)))
    return fm, sim, {"fragile_task": t1, "normal_task": t2}


def head_on_deadlock() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """R01 at C01 needs C02, R02 at C02 needs C01: a circular wait the manager must break."""
    fm, sim = _fleet(robots=[("R01", "C01", 90.0), ("R02", "C02", 90.0)])
    fm.robots["R01"].home = "DOCK_R01"
    fm.robots["R02"].home = "DOCK_R03"
    t1 = fm.submit_task("RACK_A14", "PACKING_P03", now=0.0)
    t2 = fm.submit_task("RACK_A12", "PACKING_P01")
    fm.reassign_task(t1, "R01", "demo", "scenario setup")
    fm.reassign_task(t2, "R02", "demo", "scenario setup")
    sim.run(180.0, until=lambda m: all(m.tasks[t].status == TaskStatus.DONE for t in (t1, t2)))
    return fm, sim, {"tasks": (t1, t2)}


def overload() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """20 jobs for 3 robots: queue, OVERLOAD, reserve activation, then human assistance."""
    cfg = FleetConfig(overload_threshold=10, human_assist_after_s=45.0, reserve_activate_interval_s=20.0)
    fm, sim = _fleet(cfg, robots=[("R01", "DOCK_R01", 95.0), ("R02", "DOCK_R02", 95.0), ("R03", "DOCK_R03", 95.0),
                                  ("R04", "DOCK_R04", 95.0, PoolRole.PEAK_RESERVE),
                                  ("R05", "DOCK_R05", 95.0, PoolRole.EMERGENCY_RESERVE)])
    racks = ["RACK_A12", "RACK_A14", "RACK_B03", "RACK_B07"]
    drops = ["PACKING_P01", "PACKING_P03"]
    low = fm.submit_task("RACK_A12", "PACKING_P01", TaskPriority.LOW, now=0.0)
    for i in range(19):
        pr = TaskPriority.HIGH if i % 5 == 0 else TaskPriority.NORMAL
        fm.submit_task(racks[i % 4], drops[i % 2], pr)
    sim.run(90.0)
    snapshot = fm.capacity_report()
    sim.run(900.0, until=lambda m: all(t.status == TaskStatus.DONE for t in m.tasks.values()))
    return fm, sim, {"low_task": low, "snapshot": snapshot}


def care_mode() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """Highly fragile parcel: 40 % speed, rough joint avoided, worker confirms hand-over."""
    fm, sim = _fleet(robots=[("R01", "DOCK_R01", 90.0)])
    fm.register_parcel(Parcel("P900", "lab glass", 1.2, CareLevel.HIGHLY_FRAGILE,
                              instructions="upright, no shocks"))
    tid = fm.submit_task("RACK_B03", "PACKING_P01", TaskPriority.HIGH, parcel_id="P900", now=0.0)
    sim.run(60.0, until=lambda m: m.robots["R01"].care == CareLevel.HIGHLY_FRAGILE and m.robots["R01"].moving_to)
    speed = fm.motion_command("R01")
    sim.run(120.0, until=lambda m: m.tasks[tid].status == TaskStatus.AWAITING_CONFIRMATION)
    fm.confirm_delivery(tid, "Worker07")
    sim.run(30.0, until=lambda m: m.tasks[tid].status == TaskStatus.DONE)
    return fm, sim, {"task": tid, "command_at_destination": speed}


def staggered_charging() -> Tuple[FleetManager, FleetSimulator, Dict]:
    """Spec example: R01 32 %, R02 29 %, R03 31 %, R04 80 % -> charge one at a time, lowest first."""
    cfg = FleetConfig(charge_threshold=35.0, charge_stagger_s=20.0)
    # Parked near the chargers, so none of them is forced to charge by distance alone.
    fm, sim = _fleet(cfg, robots=[("R01", "C01", 32.0), ("R02", "C02", 29.0),
                                  ("R03", "C06", 31.0), ("R04", "C09", 80.0)])
    sim.run(1.0)
    first = [r.robot_id for r in fm.robots.values() if r.goal_node and r.goal_node.startswith("CHARGER")]
    sim.run(24.0)
    after_25s = [e.robot_id for e in fm.log.find(kind="CHARGE") if "->" in e.decision]
    return fm, sim, {"first": first, "charge_order": after_25s}


ALL = {
    "selection": robot_selection,
    "busy": all_robots_busy,
    "failure": failure_recovery,
    "stall": silent_stall,
    "intersection": intersection_contention,
    "deadlock": head_on_deadlock,
    "overload": overload,
    "care": care_mode,
    "charging": staggered_charging,
}
