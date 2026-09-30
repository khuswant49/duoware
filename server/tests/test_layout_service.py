"""Step 7: the layout service — measure, blocks, obstacles, moved nodes, registry changes, persistence."""

import dataclasses
import math

import pytest
from scene import MS, Rig

from duoware.errors import DuoError
from duoware.layout.service import LayoutService

TRUE = {10: (0.0, 0.0), 2: (454.0, 58.0), 11: (821.9, 115.5), 3: (-55.7, 396.1), 6: (380.0, 471.0), 4: (766.3, 511.6),
        12: (-122.5, 871.4), 7: (323.1, 934.1), 13: (715.0, 993.0)}


@pytest.fixture
def rig(env):
    r = Rig(env)
    r.converge(70)
    r.layout = LayoutService(env.db, env.registry, r.world, env.settings, env.safety, env.events, env.clock)
    return r


def node(doc, nid):
    return next(n for n in doc["nodes"] if n["id"] == nid)


def test_before_the_first_measure_nodes_are_unmeasured(rig):
    doc = rig.layout.current()
    assert doc["measured_wall_ms"] is None and doc["grid_rotation_deg"] == 0
    assert {n["state"] for n in doc["nodes"]} == {"unmeasured"} and len(doc["nodes"]) == 9
    assert len(doc["edges"]) == 12 and all(e["length_mm"] is None and e["state"] == "ok" for e in doc["edges"])
    assert doc["footprints"] and doc["footprints"][0]["cam"] == 1


def test_measure_stores_positions_rotation_and_edges(rig):
    v0 = rig.layout.version
    doc = rig.layout.measure(v0)
    assert doc["version"] == v0 + 1 and doc["measured_wall_ms"] == rig.env.clock.wall_ms()
    assert doc["grid_rotation_deg"] == pytest.approx(8.0, abs=0.5)
    for n in doc["nodes"]:
        assert n["state"] == "ok" and math.hypot(n["x_mm"] - TRUE[n["id"]][0], n["y_mm"] - TRUE[n["id"]][1]) < 10.0
    assert {frozenset((e["a"], e["b"])) for e in doc["edges"]} == {
        frozenset(p) for p in [(10, 2), (2, 11), (3, 6), (6, 4), (12, 7), (7, 13), (10, 3), (3, 12), (2, 6), (6, 7), (11, 4), (4, 13)]}
    e = next(e for e in doc["edges"] if (e["a"], e["b"]) == (10, 2))
    assert e["length_mm"] == pytest.approx(math.hypot(454, 58), abs=5) and e["bearing_deg"] == pytest.approx(7.3, abs=1.0)
    assert abs(e["angle_error_deg"]) < 1.5
    ev = rig.env.events.query(type="layout")[0]
    assert ev.key == "measure" and ev.facts["grid_rotation_deg"] == pytest.approx(8.0, abs=0.5)
    assert node(doc, 2)["station"] == {"name": "pickup", "kind": "pickup"}


def test_measure_version_conflict_and_safety_rule(rig):
    with pytest.raises(DuoError) as e:
        rig.layout.measure(rig.layout.version + 5)
    assert e.value.code == "version_conflict" and e.value.http == 409
    rig.env.mover.moving = ["DUO-A"]
    with pytest.raises(DuoError) as e:
        rig.layout.measure(rig.layout.version)
    assert e.value.code == "requires_stopped" and e.value.details["moving"] == ["DUO-A"]
    rig.env.safety.stop_all("local")
    rig.layout.measure(rig.layout.version)                       # allowed under E-stop


def test_measure_refuses_nodes_that_are_not_currently_seen(rig):
    rig.scene.hidden.update({6, 13})
    rig.step(2)
    with pytest.raises(DuoError) as e:
        rig.layout.measure(rig.layout.version)
    assert (e.value.code, e.value.http, e.value.details["ids"]) == ("nodes_not_visible", 409, [6, 13])
    rig.scene.hidden.clear()
    rig.step(2)
    rig.layout.measure(rig.layout.version)
    rig.env.clock.advance(2000 * MS)                              # no frames for 2 s: nothing is seen any more
    with pytest.raises(DuoError) as e:
        rig.layout.measure(rig.layout.version)
    assert e.value.code == "nodes_not_visible" and len(e.value.details["ids"]) == 9


def test_measure_without_any_grid_is_a_validation_error(env):
    r = Rig(env, apply_preset=False)
    r.converge(5)
    svc = LayoutService(env.db, env.registry, r.world, env.settings, env.safety, env.events, env.clock)
    with pytest.raises(DuoError) as e:
        svc.measure(svc.version)
    assert e.value.code == "validation"


def test_admin_blocks_nodes_and_edges(rig):
    rig.layout.measure(rig.layout.version)
    v = rig.layout.version
    doc = rig.layout.set_blocked([6], [[13, 4]], v)              # [13, 4] is given in reverse order
    assert doc["version"] == v + 1
    n6 = node(doc, 6)
    assert (n6["state"], n6["blocked_by"]) == ("blocked", "admin")
    blocked = {(e["a"], e["b"]): e["blocked_by"] for e in doc["edges"] if e["state"] == "blocked"}
    assert blocked[(4, 13)] == "admin" and blocked[(2, 6)] == "admin" and blocked[(3, 6)] == "admin" and len(blocked) == 5
    assert rig.layout.set_blocked([], [], v + 1)["edges"][0]["state"] == "ok"
    for args in (([99], []), ([], [[10, 4]]), ([6.5], []), ("x", []), ([], [[1, 2, 3]]), ([], "x")):
        with pytest.raises(DuoError) as e:
            rig.layout.set_blocked(*args, rig.layout.version)
        assert e.value.code == "validation"
    with pytest.raises(DuoError) as e:
        rig.layout.set_blocked([], [], 0)
    assert e.value.code == "version_conflict"


def test_blocking_is_allowed_while_cars_move(rig):
    rig.layout.measure(rig.layout.version)
    rig.env.mover.moving = ["DUO-A", "DUO-B"]
    rig.layout.set_blocked([7], [], rig.layout.version)


def test_blocks_from_map_toml(rig, env):
    rig.layout.measure(rig.layout.version)
    rules = dataclasses.replace(env.settings.map_rules, blocked=dataclasses.replace(
        env.settings.map_rules.blocked, nodes=(11,), edges=((10, 3),)))
    svc = LayoutService(env.db, env.registry, rig.world, dataclasses.replace(env.settings, map_rules=rules),
                        env.safety, env.events, env.clock)
    doc = svc.current()
    assert node(doc, 11)["blocked_by"] == "config"
    by = {(e["a"], e["b"]): e["blocked_by"] for e in doc["edges"] if e["state"] == "blocked"}
    assert by[(10, 3)] == "config" and by[(2, 11)] == "config" and by[(11, 4)] == "config"


def test_obstacle_tags_block_nodes_and_edges_they_are_near(rig, env):
    rig.layout.measure(rig.layout.version)
    env.registry.put(30, {"role": "obstacle", "radius_mm": 100}, 0)
    rig.scene.floor_tags[30] = [30, 380.0 + 40, 471.0 + 30, -90, 60]       # 50 mm from node 6
    rig.step(3)
    doc = rig.layout.current()
    assert (node(doc, 6)["state"], node(doc, 6)["blocked_by"]) == ("blocked", "tag:30")
    assert node(doc, 7)["state"] == "ok"
    edges = {(e["a"], e["b"]): e["blocked_by"] for e in doc["edges"] if e["state"] == "blocked"}
    assert set(edges) == {(3, 6), (6, 4), (2, 6), (6, 7)} and set(edges.values()) == {"tag:30"}
    rig.scene.floor_tags[30] = [30, 380.0 + 40, 471.0 + 30 + 400, -90, 60]   # moved away: nothing is in the way
    rig.step(3)
    assert rig.layout.current()["nodes"][4]["state"] == "ok"


def test_an_obstacle_beside_an_edge_blocks_only_that_edge(rig, env):
    rig.layout.measure(rig.layout.version)
    env.registry.put(30, {"role": "obstacle"}, 0)                        # default radius from map.toml
    mid = ((TRUE[10][0] + TRUE[2][0]) / 2, (TRUE[10][1] + TRUE[2][1]) / 2 + 80)       # 80 mm beside the 10-2 edge
    rig.scene.floor_tags[30] = [30, mid[0], mid[1], -90, 60]
    rig.step(3)
    doc = rig.layout.current()
    assert {(e["a"], e["b"]) for e in doc["edges"] if e["state"] == "blocked"} == {(10, 2)}
    assert all(n["state"] == "ok" for n in doc["nodes"])


def test_a_pushed_node_is_marked_moved_after_the_confirm_time_and_clears(rig, env):
    rig.layout.measure(rig.layout.version)
    tol = env.settings.map_rules.tracking.node_move_tol_mm
    confirm = env.settings.map_rules.tracking.node_move_confirm_s
    rig.scene.floor_tags[6][1] += tol + 25
    rig.step(3)
    rig.layout.tick()
    assert node(rig.layout.current(), 6)["state"] == "ok"               # not yet: it must stay away for confirm_s
    for _ in range(int(confirm * 1000 / 33) + 3):
        rig.step(1)
        rig.layout.tick()
    doc = rig.layout.current()
    assert node(doc, 6)["state"] == "moved" and node(doc, 6)["blocked_by"] is None
    assert all(e["state"] == "blocked" and e["blocked_by"] == "moved:6" for e in doc["edges"] if 6 in (e["a"], e["b"]))
    assert [e.key for e in env.events.query(type="layout")][0] == "node_moved"
    rig.scene.floor_tags[6][1] -= tol + 25                                # pushed back
    rig.step(2)
    rig.layout.tick()
    assert node(rig.layout.current(), 6)["state"] == "ok"
    assert [e.key for e in env.events.query(type="layout")][:2] == ["node_back", "node_moved"]
    n = env.events.last_id
    rig.step(3)
    rig.layout.tick()
    assert env.events.last_id == n                                        # events only on state changes


def test_a_node_under_a_car_is_not_marked_moved(rig, env):
    rig.layout.measure(rig.layout.version)
    rig.scene.hidden.add(6)
    for _ in range(int(3000 / 33)):
        rig.step(1)
        rig.layout.tick()
    assert node(rig.layout.current(), 6)["state"] == "ok"


def test_remeasuring_clears_moved(rig, env):
    rig.layout.measure(rig.layout.version)
    rig.layout._moved.add(6)
    assert node(rig.layout.current(), 6)["state"] == "moved"
    rig.layout.measure(rig.layout.version)
    assert node(rig.layout.current(), 6)["state"] == "ok"


def test_registry_changes_update_the_layout(rig, env):
    rig.layout.measure(rig.layout.version)
    v = rig.layout.version
    body = env.registry.snapshot().get(6).to_body()
    env.registry.put(6, {**body, "grid": [5, 5]}, env.registry.snapshot().get(6).version)
    doc = rig.layout.current()
    assert rig.layout.version == v + 1 and node(doc, 6)["state"] == "unmeasured" and node(doc, 6)["grid"] == [5, 5]
    assert node(doc, 7)["state"] == "ok"
    v = rig.layout.version
    body = env.registry.snapshot().get(7).to_body()
    env.registry.put(7, {**body, "station": {"name": "x", "kind": "custom"}}, env.registry.snapshot().get(7).version)
    assert rig.layout.version == v + 1 and node(rig.layout.current(), 7)["state"] == "ok"      # station: keeps its position
    v = rig.layout.version
    env.registry.put(9, {"role": "ignore"}, 0)
    assert rig.layout.version == v                                        # unrelated tag: no bump
    env.registry.put(30, {"role": "obstacle"}, 0)
    assert rig.layout.version == v + 1
    env.registry.delete(7, env.registry.snapshot().get(7).version)
    assert all(n["id"] != 7 for n in rig.layout.current()["nodes"])


def test_suggest_from_the_world(rig):
    s = rig.layout.suggest()
    assert {x["id"]: tuple(x["grid"]) for x in s["suggestions"]} == {
        10: (0, 0), 2: (0, 1), 11: (0, 2), 3: (1, 0), 6: (1, 1), 4: (1, 2), 12: (2, 0), 7: (2, 1), 13: (2, 2)}
    assert s["conflicts"] == [] and s["grid_rotation_deg"] == pytest.approx(8.0, abs=1.5)
    rig.scene.hidden.add(6)
    rig.step(2)
    assert 6 not in {x["id"] for x in rig.layout.suggest()["suggestions"]}


def test_export_import_and_check_against_live(rig, env):
    assert rig.layout.export() is None
    rig.layout.measure(rig.layout.version)
    rig.layout.set_blocked([7], [], rig.layout.version)
    exported = rig.layout.export()
    assert set(exported["positions"]) == {str(i) for i in TRUE} and exported["blocked_nodes"] == [7]
    assert rig.layout.check_against_live(exported) == {"matched": sorted(TRUE), "moved": [], "missing": []}
    shifted = {**exported, "positions": {**exported["positions"], "6": [400.0, 500.0], "13": [0.0, 0.0]}}
    rig.scene.hidden.add(2)
    rig.step(2)
    check = rig.layout.check_against_live(shifted)
    assert check["missing"] == [2] and sorted(m["id"] for m in check["moved"]) == [6, 13] and 7 in check["matched"]
    assert node(rig.layout.current(), 6)["state"] == "moved"               # moved nodes are marked
    rig.layout.import_(None)
    assert rig.layout.export() is None and {n["state"] for n in rig.layout.current()["nodes"]} == {"unmeasured"}
    rig.layout.import_(exported)
    assert node(rig.layout.current(), 6)["state"] == "ok" and node(rig.layout.current(), 7)["blocked_by"] == "admin"


def test_the_layout_survives_a_restart(rig, env):
    rig.layout.measure(rig.layout.version)
    rig.layout.set_blocked([7], [[10, 2]], rig.layout.version)
    before = rig.layout.current()
    env.reopen()
    rig2 = Rig(env, apply_preset=False)
    rig2.converge(70)
    svc = LayoutService(env.db, env.registry, rig2.world, env.settings, env.safety, env.events, env.clock)
    after = svc.current()
    assert after["version"] == before["version"]
    assert {(n["id"], n["x_mm"], n["y_mm"], n["blocked_by"]) for n in after["nodes"]} == \
        {(n["id"], n["x_mm"], n["y_mm"], n["blocked_by"]) for n in before["nodes"]}
    assert after["edges"] == before["edges"] and after["grid_rotation_deg"] == before["grid_rotation_deg"]
