"""Step 8: REST endpoints — happy paths and error codes (PROTOCOL.md §7)."""

import pytest
from conftest import make_hello
from scene import Scene, feed_frames, status

from duoware.protocol.dashboard import Layout, TagList


def error(r, code, http):
    assert r.status_code == http, r.text
    body = r.json()["error"]
    assert body["code"] == code and isinstance(body["message"], str) and isinstance(body["details"], dict)
    return body


def apply_sim(api):
    r = api.post("/api/venue-presets/sim_3x3/apply", json={})
    assert r.status_code == 200, r.text
    return r.json()


def live_camera(api, frames=70):
    """Applies sim_3x3 and feeds a paired, synced, tracking camera with synthetic frames."""
    sv = api.sv
    apply_sim(api)
    opened = sv.sessions.open(make_hello(pair_code=sv.sessions.pair_code), "127.0.0.1")
    sv.sessions.on_status(opened.session, status())
    scene = Scene(noise_px=0.07)
    feed_frames(sv, opened.session, scene, frames)
    return opened.session, scene


# ---- core ------------------------------------------------------------------------------------------------------


def test_health(api):
    body = api.get("/api/health").json()
    assert body["status"] == "ok" and body["proto"] == 1 and body["mode"] == "sim" and body["uptime_s"] >= 0


def test_config_summary(api):
    c = api.get("/api/config").json()
    assert [x["name"] for x in c["cars"]] == ["DUO-A", "DUO-B"] and c["cars"][0]["footprint_mm"] == [200.0, 160.0]
    assert c["pose"]["stale_ms"] == 300 and c["markers"] == {"dictionary": "DICT_4X4_50", "default_size_mm": 90.0}
    assert c["roles"] == ["car", "node", "anchor", "obstacle", "ignore"]
    assert "pickup" in c["station_kinds"] and c["layout_rules"]["validation"]["max_edge_angle_deg"] == 15
    assert c["layout_rules"]["tracking"]["node_move_tol_mm"] == 30


def test_error_shapes(api):
    error(api.get("/api/nope"), "not_found", 404)
    error(api.get("/api/tags/5"), "method_not_allowed", 405)
    error(api.put("/api/tags/abc", json={}), "validation", 400)
    error(api.put("/api/tags/5", content="not json"), "validation", 400)
    error(api.put("/api/tags/5", json={"role": "node", "size_mm": 90}), "validation", 400)      # no expected_version


def test_stop_all_and_resume(api):
    assert api.post("/api/control/stop_all", json={}).json() == {"estop": True}
    assert api.post("/api/control/stop_all").json() == {"estop": True}                          # idempotent, empty body
    assert api.sv.safety.estop
    error(api.post("/api/control/resume", json={}), "confirm_required", 400)
    error(api.post("/api/control/resume", json={"confirm": "yes"}), "confirm_required", 400)
    assert api.post("/api/control/resume", json={"confirm": True}).json() == {"estop": False}
    assert not api.sv.safety.estop


# ---- tags ------------------------------------------------------------------------------------------------------


def test_tags_crud(api):
    t = api.get("/api/tags").json()
    assert t == {"registry_version": 0, "tags": []}
    body = {"role": "car", "size_mm": 80, "car": "DUO-A", "expected_version": 0}
    r = api.put("/api/tags/1", json=body)
    assert r.status_code == 200
    e = r.json()
    assert e["id"] == 1 and e["role"] == "car" and e["version"] == 1 and e["car"] == "DUO-A" and e["seen"] is False
    assert e["offset_mm"] is None and "grid" not in e and e["placed"] is None
    lst = api.get("/api/tags").json()
    TagList.model_validate(lst)
    assert lst["registry_version"] == 1 and [x["id"] for x in lst["tags"]] == [1]
    error(api.put("/api/tags/1", json=body), "version_conflict", 409)
    conflict = api.put("/api/tags/1", json=body).json()["error"]["details"]["current"]
    assert conflict["version"] == 1
    e = api.put("/api/tags/1", json={**body, "expected_version": 1, "offset_mm": [30, 0], "label": "A"}).json()
    assert e["version"] == 2 and e["offset_mm"] == [30.0, 0.0] and e["label"] == "A"
    error(api.put("/api/tags/2", json={"role": "car", "size_mm": 80, "car": "DUO-A", "expected_version": 0}), "car_already_bound", 409)
    error(api.put("/api/tags/2", json={"role": "car", "size_mm": 80, "car": "DUO-Z", "expected_version": 0}), "unknown_car", 409)
    error(api.put("/api/tags/77", json={"role": "ignore", "expected_version": 0}), "bad_tag_id", 400)
    error(api.put("/api/tags/2", json={"role": "anchor", "size_mm": 90, "grid": [0, 0], "expected_version": 0}), "validation", 400)
    error(api.delete("/api/tags/1?expected_version=9"), "version_conflict", 409)
    error(api.delete("/api/tags/1"), "validation", 400)
    assert api.delete("/api/tags/1?expected_version=2").json() == {"id": 1, "role": "unassigned"}
    assert api.get("/api/tags").json()["tags"] == []


def test_tags_safety_rule(api):
    api.sv.safety.provider = type("M", (), {"moving_cars": lambda self: ["DUO-A"]})()
    r = api.put("/api/tags/1", json={"role": "node", "size_mm": 90, "expected_version": 0})
    assert error(r, "requires_stopped", 409)["details"] == {"moving": ["DUO-A"]}
    api.post("/api/control/stop_all")
    assert api.put("/api/tags/1", json={"role": "node", "size_mm": 90, "expected_version": 0}).status_code == 200


def test_tags_batch(api):
    ok = {"changes": [{"id": 2, "doc": {"role": "node", "size_mm": 90, "grid": [0, 0]}, "expected_version": 0},
                      {"id": 3, "doc": {"role": "node", "size_mm": 90, "grid": [0, 1]}, "expected_version": 0}]}
    r = api.post("/api/tags/batch", json=ok)
    assert r.status_code == 200 and [t["id"] for t in r.json()["tags"]] == [2, 3] and r.json()["registry_version"] == 1
    bad = {"changes": [{"id": 2, "doc": None, "expected_version": 1},
                       {"id": 3, "doc": {"role": "node", "size_mm": 90, "grid": [0, 1]}, "expected_version": 0}]}
    error(api.post("/api/tags/batch", json=bad), "version_conflict", 409)
    assert {t["id"] for t in api.get("/api/tags").json()["tags"]} == {2, 3}                     # nothing was half applied
    r = api.post("/api/tags/batch", json={"changes": [{"id": 2, "doc": None, "expected_version": 1}]})
    assert r.json()["tags"][0]["role"] == "unassigned"
    error(api.post("/api/tags/batch", json={}), "validation", 400)
    error(api.post("/api/tags/batch", json={"changes": ["x"]}), "validation", 400)
    error(api.post("/api/tags/batch", json={"changes": [{"id": 4, "doc": {"role": "ignore"}}]}), "validation", 400)


def test_seen_unassigned_tags_are_listed_with_live_fields(api):
    sess, scene = live_camera(api)
    api.delete("/api/tags/5?expected_version=1")                   # DUO-B's tag becomes unassigned but is still seen
    feed_frames(api.sv, sess, scene, 3)
    lst = api.get("/api/tags").json()
    TagList.model_validate(lst)
    by = {t["id"]: t for t in lst["tags"]}
    assert by[5]["role"] == "unassigned" and by[5]["seen"] and by[5]["version"] == 0 and by[5]["size_mm"] is None
    assert by[10]["role"] == "node" and by[10]["seen"] and by[10]["placed"] is True and by[10]["origin"] is True
    assert by[6]["grid"] == [1, 1] and by[6]["placed"] is True and 88 < by[6]["measured_size_mm"] < 92 and by[6]["size_mm"] == 90
    assert by[2]["station"] == {"name": "pickup", "kind": "pickup"} and by[1]["role"] == "car" and by[1]["cams"] == [1]


# ---- layout ----------------------------------------------------------------------------------------------------


def test_layout_suggest_measure_and_blocked(api):
    sess, scene = live_camera(api)
    before = api.get("/api/layout").json()
    Layout.model_validate(before)
    assert {n["state"] for n in before["nodes"]} == {"unmeasured"}
    s = api.post("/api/layout/suggest", json={}).json()
    assert len(s["suggestions"]) == 9 and s["conflicts"] == [] and abs(s["grid_rotation_deg"] - 8) < 1.5
    error(api.post("/api/layout/measure", json={}), "validation", 400)
    error(api.post("/api/layout/measure", json={"expected_layout_version": 99}), "version_conflict", 409)
    doc = api.post("/api/layout/measure", json={"expected_layout_version": before["version"]}).json()
    Layout.model_validate(doc)
    assert doc["version"] == before["version"] + 1 and {n["state"] for n in doc["nodes"]} == {"ok"}
    assert len(doc["edges"]) == 12 and abs(doc["grid_rotation_deg"] - 8) < 0.5
    assert api.get("/api/layout").json() == doc
    b = api.put("/api/layout/blocked", json={"nodes": [6], "edges": [], "expected_layout_version": doc["version"]}).json()
    assert [n["blocked_by"] for n in b["nodes"] if n["id"] == 6] == ["admin"]
    error(api.put("/api/layout/blocked", json={"nodes": [99], "edges": [], "expected_layout_version": b["version"]}), "validation", 400)
    scene.hidden.add(7)
    feed_frames(api.sv, sess, scene, 2)
    error(api.post("/api/layout/measure", json={"expected_layout_version": b["version"]}), "nodes_not_visible", 409)
    api.sv.safety.provider = type("M", (), {"moving_cars": lambda self: ["DUO-A"]})()
    error(api.post("/api/layout/measure", json={"expected_layout_version": b["version"]}), "requires_stopped", 409)


# ---- presets ---------------------------------------------------------------------------------------------------


def test_venue_presets(api):
    lst = api.get("/api/venue-presets").json()
    assert {p["name"] for p in lst} == {"sim_3x3", "duoware1_kit"} and all(p["builtin"] for p in lst)
    r = apply_sim(api)
    assert r["registry_version"] == 1 and r["check"] == {"matched": [], "moved": [], "missing": []}
    error(api.post("/api/venue-presets/sim_3x3/apply", json={"expected_registry_version": 0}), "version_conflict", 409)
    error(api.post("/api/venue-presets/nope/apply", json={}), "not_found", 404)
    error(api.post("/api/venue-presets/sim_3x3/apply", json={"expected_registry_version": "x"}), "validation", 400)
    saved = api.post("/api/venue-presets", json={"name": "Venue 1", "description": "d"}).json()
    assert saved["builtin"] is False and saved["tag_count"] == 11
    error(api.post("/api/venue-presets", json={"name": "Venue 1"}), "preset_exists", 409)
    assert api.post("/api/venue-presets", json={"name": "Venue 1", "overwrite": True}).status_code == 200
    error(api.post("/api/venue-presets", json={"name": "sim_3x3"}), "preset_builtin", 409)
    error(api.post("/api/venue-presets", json=["x"]), "validation", 400)
    assert api.delete("/api/venue-presets/Venue%201").status_code == 204
    error(api.delete("/api/venue-presets/Venue%201"), "not_found", 404)
    error(api.delete("/api/venue-presets/sim_3x3"), "preset_builtin", 409)


def test_preset_apply_checks_positions_against_the_camera(api):
    sess, scene = live_camera(api)
    v = api.get("/api/layout").json()["version"]
    api.post("/api/layout/measure", json={"expected_layout_version": v})
    api.post("/api/venue-presets", json={"name": "measured"})
    scene.floor_tags[6][1] += 80.0                                   # node 6 is pushed before the preset is applied again
    scene.hidden.add(13)
    feed_frames(api.sv, sess, scene, 25)
    r = api.post("/api/venue-presets/measured/apply", json={}).json()
    assert [m["id"] for m in r["check"]["moved"]] == [6] and r["check"]["missing"] == [13]
    assert set(r["check"]["matched"]) == {10, 2, 11, 3, 4, 12, 7}
    lay = api.get("/api/layout").json()
    assert [n["state"] for n in lay["nodes"] if n["id"] == 6] == ["moved"]


# ---- cameras, pairing, events ---------------------------------------------------------------------------------


def test_cameras_list_and_settings_and_recalibrate(api):
    assert api.get("/api/cameras").json() == []
    error(api.post("/api/cameras/1/recalibrate", json={}), "not_found", 404)
    sess, scene = live_camera(api)
    (cam,) = api.get("/api/cameras").json()
    assert cam["cam"] == 1 and cam["online"] and cam["calib"]["status"] == "OK" and cam["sync"]["ok"]
    assert cam["caps"]["sdk"] == 33 and cam["caps"]["camera"]["hw_level"] == "FULL"
    assert cam["pose_age_ms"]["n"] > 0 and abs(cam["pose_age_ms"]["p50"] - 40) < 2
    assert cam["link"]["mode"] == "wireless" and cam["app_mode"] == "tracking" and cam["fps"] == 30.0
    assert api.post("/api/cameras/1/recalibrate", json={}).json() == {"cam": 1, "status": "UNCALIBRATED"}
    assert api.get("/api/cameras").json()[0]["calib"]["status"] == "UNCALIBRATED"
    r = api.put("/api/camera-settings", json={"exposure_ms": 4.5, "iso": 400})
    assert r.json() == {"overrides": {"exposure_ms": 4.5, "iso": 400}}
    assert api.put("/api/camera-settings", json={"iso": None}).json() == {"overrides": {"exposure_ms": 4.5}}
    error(api.put("/api/camera-settings", json={"zoom": 3}), "validation", 400)
    error(api.put("/api/camera-settings", json={"exposure_ms": -1}), "validation", 400)


def test_pairing(api):
    p = api.get("/api/pairing").json()
    assert p["devices"] == [] and len(p["pair_code"]) == 7
    sv = api.sv
    opened = sv.sessions.open(make_hello(pair_code=p["pair_code"]), "127.0.0.1")
    (d,) = api.get("/api/pairing").json()["devices"]
    assert d["device_id"] == "dev-1" and d["cam"] == 1 and d["model"] == "test phone"
    assert api.delete("/api/pairing/dev-1").json() == {"device_id": "dev-1", "revoked": True}
    assert api.get("/api/pairing").json()["devices"] == [] and sv.sessions.by_sid(opened.session.sid) is None


def test_events(api):
    api.put("/api/tags/5", json={"role": "car", "size_mm": 80, "car": "DUO-B", "expected_version": 0})
    api.post("/api/control/stop_all")
    r = api.get("/api/events").json()
    assert r["last_id"] >= 3 and r["events"][0]["id"] == r["last_id"]
    types = [e["type"] for e in r["events"]]
    assert "registry" in types and "operator" in types and "system" in types
    reg = api.get("/api/events?type=registry").json()["events"]
    assert len(reg) == 1 and reg[0]["key"] == "tag:5" and reg[0]["facts"]["new"]["car"] == "DUO-B"
    assert api.get(f"/api/events?since_id={r['last_id']}").json()["events"] == []
    assert len(api.get("/api/events?limit=1").json()["events"]) == 1
    assert api.get("/api/events?car=DUO-A").json()["events"] == []
    error(api.get("/api/events?limit=0"), "validation", 400)
    error(api.get("/api/events?limit=1001"), "validation", 400)
    error(api.get("/api/events?limit=abc"), "validation", 400)
    from duoware.protocol.dashboard import EventRecord
    EventRecord.model_validate(reg[0])


def test_static_dashboard_is_served_when_built(tmp_path):
    from conftest import make_app
    from fastapi.testclient import TestClient
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>DUO</html>")
    with TestClient(make_app(tmp_path, dashboard_dir=dist)) as c:
        assert "DUO" in c.get("/").text
        assert c.get("/api/health").json()["status"] == "ok"          # the API still wins over the static mount
    with TestClient(make_app(tmp_path / "b")) as c:
        assert c.get("/").status_code == 404
