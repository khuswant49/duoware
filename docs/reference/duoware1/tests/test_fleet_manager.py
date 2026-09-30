"""
DUO-WARE Fleet Manager Tests (simulation, no hardware needed)

Covers robot selection, workload continuity, traffic control, deadlocks, fault detection and
recovery, battery and charging, reserve robots, Care Mode, worker controls and the decision log.

    python -m unittest tests.test_fleet_manager -v
"""

import unittest

from fleet.warehouse import (CareLevel, FleetManager, FleetMode, FleetSimulator, Parcel, PoolRole,
                             RobotState, TaskPriority, TaskStatus, demo_warehouse)
from fleet.warehouse import scenarios


def fleet(robots, config=None):
    fm = FleetManager(demo_warehouse(), config)
    for rid, node, battery in robots:
        fm.register_robot(rid, node, battery=battery)
    return fm, FleetSimulator(fm)


def assert_exclusive(tc, fm):
    """Safety invariant: a checkpoint is never granted to two robots."""
    seen = {}
    for r in fm.robots.values():
        for n in {r.node, r.moving_to} - {None}:
            if r.state in (RobotState.FAULT, RobotState.OFFLINE, RobotState.MAINTENANCE) and n in fm.blocked_nodes:
                continue
            tc.assertNotIn(n, seen, f"{n} held by {seen.get(n)} and {r.robot_id} at t={fm.now}")
            seen[n] = r.robot_id
            tc.assertEqual(fm.holder.get(n), r.robot_id, f"{n} occupied by {r.robot_id} but not reserved")


class TestSelectionAndQueue(unittest.TestCase):
    def test_battery_and_availability_beat_distance(self):
        fm, _, info = scenarios.robot_selection()
        why = fm.log.why(info["task"])
        self.assertEqual(why["selected"], "B")
        first = fm.log.find(kind="ASSIGN", task_id=info["busy_task"])[0]
        self.assertIn("A not selected: battery 21% would end at", first.reason)
        self.assertIn("operating reserve", first.reason)
        rows = {row["robot"]: row for row in why["candidates"]}
        self.assertIn("busy", rows["C"]["reason"])

    def test_all_busy_task_is_queued_reserved_and_auto_assigned(self):
        fm, _, info = scenarios.all_robots_busy()
        tid = info["task"]
        reserve = fm.log.find(kind="RESERVE", task_id=tid)
        self.assertTrue(reserve, "task should have been pre-reserved while every robot was busy")
        options = [a for a in reserve[0].alternatives if "free_in_s" in a]
        earliest = min(options, key=lambda a: a["eta_s"])["robot"]
        self.assertEqual(info["reserved_for"], earliest)
        assign = fm.log.find(kind="ASSIGN", task_id=tid)[-1]
        self.assertEqual(assign.robot_id, info["reserved_for"])
        self.assertEqual(fm.tasks[tid].status, TaskStatus.DONE)

    def test_priority_aging_prevents_starvation(self):
        fm, _ = fleet([("R01", "DOCK_R01", 90.0)])
        old_low = fm.submit_task("RACK_A12", "PACKING_P01", TaskPriority.LOW, now=0.0)
        fm.now = 600.0
        fresh_high = fm.submit_task("RACK_A14", "PACKING_P01", TaskPriority.HIGH)
        self.assertGreater(fm.priority_score(fm.tasks[old_low])[0], fm.priority_score(fm.tasks[fresh_high])[0])

    def test_deadline_raises_priority(self):
        fm, _ = fleet([("R01", "DOCK_R01", 90.0)])
        a = fm.submit_task("RACK_A12", "PACKING_P01", now=0.0)
        b = fm.submit_task("RACK_A14", "PACKING_P01", deadline=60.0)
        score, parts = fm.priority_score(fm.tasks[b])
        self.assertGreater(score, fm.priority_score(fm.tasks[a])[0])
        self.assertIn("deadline", parts)

    def test_every_assignment_is_explained(self):
        fm, _, _ = scenarios.overload()
        for e in fm.log.find(kind="ASSIGN"):
            self.assertTrue(e.reason and e.alternatives, e)


class TestTraffic(unittest.TestCase):
    def test_checkpoints_are_never_double_granted(self):
        fm, sim = fleet([("R01", "DOCK_R01", 95.0), ("R02", "DOCK_R02", 95.0),
                         ("R03", "DOCK_R03", 95.0), ("R04", "DOCK_R04", 95.0)])
        racks = ["RACK_A12", "RACK_A14", "RACK_B03", "RACK_B07"]
        for i in range(12):
            fm.submit_task(racks[i % 4], ["PACKING_P01", "PACKING_P03"][i % 2], now=0.0)
        for _ in range(1600):
            sim.step()
            assert_exclusive(self, fm)
            if all(t.status == TaskStatus.DONE for t in fm.tasks.values()):
                break
        self.assertTrue(all(t.status == TaskStatus.DONE for t in fm.tasks.values()))
        self.assertFalse(fm.log.find(kind="FAULT"), "no robot should be faulted in normal traffic")

    def test_fragile_parcel_gets_intersection_first(self):
        fm, _, info = scenarios.intersection_contention()
        first = [e for e in fm.log.find(kind="TRAFFIC") if "first" in e.decision][0]
        self.assertEqual(first.robot_id, "R01")
        self.assertIn("FRAGILE", first.reason)
        self.assertTrue(all(fm.tasks[t].status == TaskStatus.DONE for t in info.values()))

    def test_head_on_deadlock_is_broken(self):
        fm, _, info = scenarios.head_on_deadlock()
        self.assertTrue(fm.log.find(kind="DEADLOCK"))
        self.assertTrue(all(fm.tasks[t].status == TaskStatus.DONE for t in info["tasks"]))

    def test_intentional_wait_is_not_a_fault(self):
        fm, sim = fleet([("R01", "DOCK_R01", 90.0)])
        tid = fm.submit_task("RACK_A12", "PACKING_P01", now=0.0)
        sim.run(30.0, until=lambda m: m.robots["R01"].node == "C04")
        fm.block_route("C01", "Supervisor01", reason="spill")      # the only way to RACK_A12
        sim.run(90.0)
        r = fm.robots["R01"]
        self.assertEqual(r.state, RobotState.WAITING)
        self.assertFalse(fm.log.find(kind="FAULT"))
        fm.open_route("C01", "Supervisor01")
        sim.run(120.0, until=lambda m: m.tasks[tid].status == TaskStatus.DONE)
        self.assertEqual(fm.tasks[tid].status, TaskStatus.DONE)


class TestFaultsAndRecovery(unittest.TestCase):
    def test_motor_fault_blocks_reassigns_and_needs_approved_restart(self):
        fm, _, info = scenarios.failure_recovery()
        victim, tid = info["victim"], info["task"]
        fault = fm.log.find(kind="FAULT", robot_id=victim)
        self.assertTrue(fault)
        self.assertTrue(any(e.decision.startswith("BLOCKED_ZONE") for e in fm.log.find(kind="RECOVERY")))
        self.assertEqual(fm.tasks[tid].status, TaskStatus.DONE)
        self.assertNotEqual(fm.tasks[tid].assigned_robot, victim)
        self.assertEqual(info["worker_view"]["reason"], "motor telemetry fault")
        self.assertEqual(info["worker_view"]["technician_status"], "ACKNOWLEDGED")
        self.assertFalse(info["refused"][0])
        self.assertIn("motor fault", info["refused"][1])
        self.assertTrue(info["restarted"][0])
        self.assertFalse(any(v.startswith(victim) for v in fm.blocked_nodes.values()))
        self.assertNotEqual(fm.robots[victim].state, RobotState.FAULT)

    def test_silent_stall_is_confirmed_before_recovery(self):
        fm, _, info = scenarios.silent_stall()
        rid = info["robot"]
        suspect = [e for e in fm.log.find(kind="HEALTH", robot_id=rid) if "POSSIBLE_FAULT" in e.decision]
        fault = fm.log.find(kind="FAULT", robot_id=rid)
        self.assertTrue(suspect and fault)
        self.assertGreaterEqual(fault[0].time - suspect[0].time, fm.cfg.confirm_window_s - 1e-6)
        self.assertEqual(fm.tasks[info["task"]].status, TaskStatus.DONE)

    def test_lost_telemetry_marks_robot_offline(self):
        fm, sim = fleet([("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R03", 90.0)])
        tid = fm.submit_task("RACK_A12", "PACKING_P03", now=0.0)
        sim.step()
        rid = fm.tasks[tid].assigned_robot
        sim.run(4.0)
        sim.inject(rid, "comm")
        sim.run(10.0)
        self.assertEqual(fm.robots[rid].state, RobotState.OFFLINE)
        self.assertNotEqual(fm.tasks[tid].assigned_robot, rid)

    def test_parcel_on_failed_robot_needs_a_worker(self):
        fm, sim = fleet([("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R03", 90.0)])
        tid = fm.submit_task("RACK_B03", "PACKING_P03", now=0.0)
        sim.step()
        rid = fm.tasks[tid].assigned_robot
        sim.run(60.0, until=lambda m: m.tasks[tid].status == TaskStatus.CARRYING and m.robots[rid].moving_to)
        sim.inject(rid, "motor")
        sim.step()
        self.assertEqual(fm.tasks[tid].status, TaskStatus.NEEDS_HUMAN)
        self.assertTrue(fm.requeue_task(tid, "Worker07", pickup="RACK_B07"))
        sim.run(180.0, until=lambda m: m.tasks[tid].status == TaskStatus.DONE)
        self.assertEqual(fm.tasks[tid].status, TaskStatus.DONE)


class TestEnergyAndCapacity(unittest.TestCase):
    def test_staggered_charging_lowest_first(self):
        _, _, info = scenarios.staggered_charging()
        self.assertEqual(info["first"], ["R02"])
        self.assertEqual(info["charge_order"][:2], ["R02", "R03"])

    def test_overload_activates_reserve_and_asks_for_people(self):
        fm, _, info = scenarios.overload()
        modes = [e.decision.split(" -> ")[1] for e in fm.log.find(kind="MODE")]
        self.assertIn(FleetMode.OVERLOAD.value, modes)
        self.assertIn(FleetMode.HUMAN_ASSISTANCE.value, modes)
        self.assertTrue(any("R04" in e.decision and "activated" in e.decision for e in fm.log.find(kind="RESERVE_POOL")))
        self.assertEqual(info["snapshot"]["recommendation"], "Activate standby robot or human-assisted transport.")
        self.assertTrue(all(t.status == TaskStatus.DONE for t in fm.tasks.values()))

    def test_reserve_robot_stays_out_until_activated(self):
        fm = FleetManager(demo_warehouse())
        fm.register_robot("R01", "DOCK_R01", battery=90.0)
        fm.register_robot("R09", "DOCK_R02", battery=90.0, pool=PoolRole.PEAK_RESERVE)
        tid = fm.submit_task("RACK_A12", "PACKING_P01", now=0.0)
        fm.tick(0.5)
        self.assertEqual(fm.tasks[tid].assigned_robot, "R01")
        self.assertEqual(fm.robots["R09"].state, RobotState.RESERVED)


class TestCareModeAndWorkers(unittest.TestCase):
    def test_highly_fragile_parcel_profile_route_and_confirmation(self):
        fm, _, info = scenarios.care_mode()
        tid = info["task"]
        _, speed = info["command_at_destination"]
        self.assertAlmostEqual(speed, 0.4 * fm.robots["R01"].speed_mps)
        hops = {read.direction for read in fm.checkpoint_log if read.task_id == tid}
        self.assertNotIn("C05->C06", hops)
        self.assertNotIn("C06->C05", hops)
        self.assertTrue(fm.log.find(kind="CARE"))
        self.assertTrue(any(e.operator_id == "Worker07" for e in fm.log.find(task_id=tid)))
        self.assertEqual(fm.tasks[tid].status, TaskStatus.DONE)

    def test_unknown_parcel_is_handled_as_fragile(self):
        fm, _ = fleet([("R01", "DOCK_R01", 90.0)])
        tid = fm.submit_task("RACK_A12", "PACKING_P01", parcel_id="P404", now=0.0)
        self.assertEqual(fm.tasks[tid].care, CareLevel.FRAGILE)

    def test_emergency_stop_holds_every_robot_mid_mission(self):
        fm, sim = fleet([("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R03", 90.0)])
        fm.register_parcel(Parcel("P1", care=CareLevel.FRAGILE))
        t1 = fm.submit_task("RACK_A12", "PACKING_P03", parcel_id="P1", now=0.0)
        t2 = fm.submit_task("RACK_B07", "PACKING_P01")
        sim.run(12.0)
        fm.emergency_stop("Supervisor01", reason="person in aisle")
        before = {rid: (r.node, r.moving_to, sim.progress.get(rid, 0.0)) for rid, r in fm.robots.items()}
        sim.run(60.0)
        after = {rid: (r.node, r.moving_to, sim.progress.get(rid, 0.0)) for rid, r in fm.robots.items()}
        self.assertEqual(before, after)
        self.assertTrue(all(fm.motion_command(rid) is None for rid in fm.robots))
        fm.release_emergency_stop("Supervisor01")
        sim.run(240.0, until=lambda m: all(m.tasks[t].status == TaskStatus.DONE for t in (t1, t2)))
        self.assertFalse(fm.log.find(kind="FAULT"), "E-stop must not be mistaken for a stall")
        self.assertTrue(all(fm.tasks[t].status == TaskStatus.DONE for t in (t1, t2)))

    def test_override_is_logged_with_ai_recommendation(self):
        fm, _ = fleet([("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R05", 90.0)])
        tid = fm.submit_task("RACK_B03", "PACKING_P01", now=0.0)
        ok, _ = fm.reassign_task(tid, "R02", "Supervisor01", "R01 scheduled for inspection")
        self.assertTrue(ok)
        e = fm.log.find(kind="OVERRIDE", task_id=tid)[-1]
        self.assertTrue(e.human_override)
        self.assertEqual(e.operator_id, "Supervisor01")
        self.assertEqual(e.ai_recommendation, "R01")
        self.assertEqual(fm.log.why(tid)["selected"], "R02")

    def test_override_cannot_breach_battery_safety(self):
        fm, _ = fleet([("R01", "DOCK_R01", 90.0), ("R02", "DOCK_R05", 12.0)])
        tid = fm.submit_task("RACK_A12", "PACKING_P01", now=0.0)
        ok, why = fm.reassign_task(tid, "R02", "Supervisor01", "test")
        self.assertFalse(ok)
        self.assertIn("safety", why)

    def test_checkpoint_reads_are_recorded(self):
        fm, _, _ = scenarios.all_robots_busy()
        read = fm.checkpoint_log[0]
        for field in ("robot_id", "checkpoint", "time", "task_id", "battery", "state", "direction", "previous",
                      "next_expected", "destination"):
            self.assertTrue(hasattr(read, field))
        self.assertTrue(all(r.expected for r in fm.checkpoint_log))

    def test_unexpected_tag_corrects_position(self):
        fm, _ = fleet([("R01", "C05", 90.0)])
        fm.on_checkpoint("R01", "C09", now=1.0)
        self.assertEqual(fm.robots["R01"].node, "C09")
        self.assertEqual(fm.holder.get("C09"), "R01")
        self.assertNotIn("C05", fm.holder)
        self.assertTrue(fm.log.find(kind="POSITION"))


if __name__ == "__main__":
    unittest.main()
