"""Step 4 / A4: every validation and conflict code of PROTOCOL.md §7.4, versions, batches, persistence."""

import pytest

from duoware.errors import DuoError
from duoware.registry.model import RegistryError
from duoware.registry.service import Change

NODE = {"role": "node", "size_mm": 90}
CAR = {"role": "car", "size_mm": 80, "car": "DUO-A"}


def code(fn, *args, **kw) -> DuoError:
    with pytest.raises(DuoError) as e:
        fn(*args, **kw)
    return e.value


def test_put_get_delete_and_versions(env):
    r = env.registry
    assert r.snapshot().version == 0
    d = r.put(5, {**CAR, "car": "DUO-B"}, 0)
    assert (d.id, d.role, d.version, d.updated_by) == (5, "car", 1, "local")
    assert r.snapshot().version == 1 and r.snapshot().car_tag("DUO-B").id == 5
    d2 = r.put(5, {**CAR, "car": "DUO-B", "label": "blue"}, 1)
    assert d2.version == 2 and d2.label == "blue"
    r.delete(5, 2)
    assert r.snapshot().get(5) is None and r.snapshot().version == 3


def test_no_op_put_keeps_version_and_logs_nothing(env):
    r = env.registry
    r.put(2, NODE, 0)
    before = env.events.last_id
    r.put(2, NODE, 1)
    assert r.snapshot().get(2).version == 1 and env.events.last_id == before


def test_bad_tag_id(env):
    for bad in (-1, 50, 999, "5", None):
        assert code(env.registry.put, bad, NODE, 0).code == "bad_tag_id"
        assert code(env.registry.put, bad, NODE, 0).http == 400


@pytest.mark.parametrize("body", [
    "x", {}, {"role": "wizard"}, {"role": "node"},                                  # missing/invalid role, size
    {"role": "node", "size_mm": 9}, {"role": "node", "size_mm": 501}, {"role": "node", "size_mm": "big"},
    {"role": "node", "size_mm": 90, "label": "x" * 41}, {"role": "node", "size_mm": 90, "label": 5},
    {"role": "node", "size_mm": 90, "grid": [-1, 0]}, {"role": "node", "size_mm": 90, "grid": [0]},
    {"role": "node", "size_mm": 90, "grid": [0.5, 1]}, {"role": "node", "size_mm": 90, "origin": "yes"},
    {"role": "node", "size_mm": 90, "pose": {"x_mm": 1}}, {"role": "node", "size_mm": 90, "origin": True,
                                                            "pose": {"x_mm": 0, "y_mm": 0, "yaw_deg": 0}},
    {"role": "node", "size_mm": 90, "station": {"name": "", "kind": "home"}},
    {"role": "node", "size_mm": 90, "station": {"name": "x" * 25, "kind": "home"}},
    {"role": "node", "size_mm": 90, "station": {"name": "a", "kind": "garage"}},
    {"role": "car", "size_mm": 80}, {"role": "car", "size_mm": 80, "car": "DUO-A", "offset_mm": [1]},
    {"role": "car", "size_mm": 80, "car": "DUO-A", "heading_offset_deg": "x"},
    {"role": "obstacle", "radius_mm": -1}, {"role": "obstacle", "radius_mm": 1001},
    {"role": "node", "size_mm": 90, "id": 7},                                        # id differs from the path
])
def test_validation_errors(env, body):
    e = code(env.registry.put, 4, body, 0)
    assert (e.code, e.http) == ("validation", 400)


def test_role_fields_on_the_wrong_role_are_rejected(env):
    cases = [{"role": "anchor", "size_mm": 90, "grid": [0, 0]}, {"role": "car", "size_mm": 80, "car": "DUO-A", "grid": [0, 0]},
             {"role": "obstacle", "origin": True}, {"role": "ignore", "car": "DUO-A"},
             {"role": "node", "size_mm": 90, "radius_mm": 5}, {"role": "anchor", "size_mm": 90, "station": None}]
    for body in cases:
        e = code(env.registry.put, 4, body, 0)
        assert e.code == "validation" and e.details["fields"], body


def test_read_only_fields_are_ignored_on_input(env):
    env.registry.put(4, {**NODE, "placed": True, "version": 9, "updated_by": "x", "updated_wall_ms": 1}, 0)
    assert env.registry.snapshot().get(4).version == 1


def test_defaults_for_roles_without_size(env):
    d = env.registry.put(9, {"role": "obstacle"}, 0)
    assert d.size_mm == env.settings.tuning.markers.default_size_mm and d.fields.radius_mm is None
    assert env.registry.put(8, {"role": "ignore"}, 0).role == "ignore"


def test_version_conflict_returns_the_current_document(env):
    env.registry.put(3, NODE, 0)
    e = code(env.registry.put, 3, {**NODE, "label": "new"}, 0)
    assert (e.code, e.http) == ("version_conflict", 409)
    assert e.details["current"]["version"] == 1 and e.details["current"]["id"] == 3
    e = code(env.registry.put, 12, NODE, 4)                       # unassigned tag: current is null, version 0
    assert e.code == "version_conflict" and e.details["current"] is None
    assert code(env.registry.delete, 3, 7).code == "version_conflict"


def test_unknown_car(env):
    e = code(env.registry.put, 1, {**CAR, "car": "DUO-Z"}, 0)
    assert (e.code, e.http) == ("unknown_car", 409) and "DUO-A" in e.details["cars"]


def test_car_bind_uniqueness_both_ways(env):
    env.registry.put(1, CAR, 0)
    e = code(env.registry.put, 5, CAR, 0)                         # a second tag for DUO-A
    assert (e.code, e.details["id"], e.details["other"]) == ("car_already_bound", 5, 1)
    env.registry.put(5, {**CAR, "car": "DUO-B"}, 0)
    e = code(env.registry.put, 1, {**CAR, "car": "DUO-B"}, 1)     # re-binding tag 1 to the car tag 5 has
    assert e.code == "car_already_bound" and e.details["id"] == 1 and e.details["other"] == 5
    env.registry.delete(1, 1)                                     # freeing the car makes it bindable again
    env.registry.put(6, CAR, 0)


def test_origin_uniqueness_across_node_and_anchor(env):
    env.registry.put(10, {**NODE, "origin": True}, 0)
    e = code(env.registry.put, 11, {"role": "anchor", "size_mm": 90, "origin": True}, 0)
    assert (e.code, e.http) == ("origin_exists", 409) and e.details["other"] == 10
    env.registry.put(11, {"role": "anchor", "size_mm": 90}, 0)
    env.registry.put(10, NODE, 1)                                 # clear it first ...
    env.registry.put(11, {"role": "anchor", "size_mm": 90, "origin": True}, 1)   # ... then it can move


def test_grid_uniqueness(env):
    env.registry.put(2, {**NODE, "grid": [0, 1]}, 0)
    e = code(env.registry.put, 3, {**NODE, "grid": [0, 1]}, 0)
    assert (e.code, e.http) == ("grid_position_taken", 409) and e.details["other"] == 2
    env.registry.put(3, {**NODE, "grid": [1, 1]}, 0)


def test_station_name_uniqueness(env):
    st = {"name": "pickup", "kind": "pickup"}
    env.registry.put(2, {**NODE, "station": st}, 0)
    e = code(env.registry.put, 3, {**NODE, "station": {"name": "pickup", "kind": "custom"}}, 0)
    assert (e.code, e.http) == ("station_name_taken", 409)
    env.registry.put(3, {**NODE, "station": {"name": "other", "kind": "custom"}}, 0)


def test_requires_stopped_with_a_moving_car(env):
    env.registry.put(2, NODE, 0)
    env.mover.moving = ["DUO-A"]
    for fn in (lambda: env.registry.put(2, {**NODE, "size_mm": 91}, 1), lambda: env.registry.put(3, NODE, 0),
               lambda: env.registry.delete(2, 1), lambda: env.registry.put(1, CAR, 0)):
        e = code(fn)
        assert (e.code, e.http) == ("requires_stopped", 409) and e.details == {"moving": ["DUO-A"]}
    assert env.registry.snapshot().get(2).size_mm == 90


def test_requires_stopped_is_lifted_by_estop(env):
    env.mover.moving = ["DUO-A"]
    env.safety.stop_all("local")
    assert env.registry.put(2, NODE, 0).version == 1
    env.safety.resume("local")
    assert code(env.registry.put, 3, NODE, 0).code == "requires_stopped"


def test_non_motion_change_allowed_while_moving(env):
    env.registry.put(9, {"role": "obstacle", "radius_mm": 100}, 0)
    env.mover.moving = ["DUO-A", "DUO-B"]
    assert env.registry.put(9, {"role": "obstacle", "radius_mm": 200}, 1).fields.radius_mm == 200
    assert env.registry.put(8, {"role": "ignore"}, 0).role == "ignore"
    assert code(env.registry.put, 9, {"role": "node", "size_mm": 90}, 2).code == "requires_stopped"  # obstacle -> node


def test_batch_is_all_or_nothing_and_logs_one_event(env):
    r = env.registry
    before = env.events.last_id
    out = r.batch([Change(2, {**NODE, "grid": [0, 0]}, 0), Change(3, {**NODE, "grid": [0, 1]}, 0)])
    assert [d.id for d in out] == [2, 3] and env.events.last_id == before + 1
    assert r.snapshot().version == 1
    before = env.events.last_id
    with pytest.raises(RegistryError) as e:
        r.batch([Change(2, {**NODE, "grid": [5, 5]}, 1), Change(3, {**NODE, "grid": [9, 9]}, 99)])
    assert e.value.code == "version_conflict"
    with pytest.raises(RegistryError) as e:
        r.batch([Change(4, {**NODE, "grid": [7, 7]}, 0), Change(5, {**NODE, "grid": [7, 7]}, 0)])
    assert e.value.code == "grid_position_taken"
    with pytest.raises(RegistryError):
        r.batch([Change(4, NODE, 0), Change(5, {"role": "nope"}, 0)])
    assert r.snapshot().get(4) is None and r.snapshot().get(2).fields.grid == (0, 0)
    assert r.snapshot().version == 1 and env.events.last_id == before


def test_batch_can_swap_grid_positions(env):
    r = env.registry
    r.batch([Change(2, {**NODE, "grid": [0, 0]}, 0), Change(3, {**NODE, "grid": [0, 1]}, 0)])
    r.batch([Change(2, {**NODE, "grid": [0, 1]}, 1), Change(3, {**NODE, "grid": [0, 0]}, 1)])
    assert r.snapshot().get(2).fields.grid == (0, 1) and r.snapshot().get(3).fields.grid == (0, 0)


def test_batch_delete_with_null_doc(env):
    env.registry.put(2, NODE, 0)
    env.registry.batch([Change(2, None, 1)])
    assert env.registry.snapshot().get(2) is None


def test_persistence_across_reopen(env):
    env.registry.put(10, {**NODE, "origin": True, "grid": [0, 0], "station": {"name": "s", "kind": "home"}}, 0)
    env.registry.put(1, {**CAR, "offset_mm": [30, 0], "heading_offset_deg": 1.5}, 0)
    before = {i: d.to_json() for i, d in env.registry.snapshot().tags.items()}
    v = env.registry.snapshot().version
    env.reopen()
    assert env.registry.snapshot().version == v
    assert {i: d.to_json() for i, d in env.registry.snapshot().tags.items()} == before


def test_events_record_old_and_new(env):
    env.registry.put(5, {**CAR, "car": "DUO-B"}, 0)
    ev = env.events.query(type="registry")[0]
    assert (ev.key, ev.value, ev.prev, ev.operator) == ("tag:5", "car", "unassigned", "local")
    assert ev.facts["old"] is None and ev.facts["new"]["car"] == "DUO-B"


def test_observers_get_old_and_new_snapshots(env):
    seen = []
    env.registry.on_change(lambda old, new: seen.append((old.version, new.version)))
    env.registry.put(2, NODE, 0)
    env.registry.delete(2, 1)
    assert seen == [(0, 1), (1, 2)]


def test_registry_error_is_a_duo_error(env):
    assert isinstance(code(env.registry.put, 99, NODE, 0), DuoError)
