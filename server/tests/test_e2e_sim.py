"""Step 10 / A2, A3, A12, A14, A15: the real server app and the simulator, in one event loop, through the real
protocols (WebSocket pairing, UDP frames, clock sync, REST). Simulation only: nothing here touches hardware.

Numbers measured by this test are printed as `E2E key=value` lines (run with `-s`) and recorded in docs/plans/M1.md."""

import asyncio
import math
import statistics
import time

import httpx2
import numpy as np
import pytest
from harness import ServerHarness, SimRig, make_scenario

from duoware.localization.geometry import wrap_deg
from duoware.protocol.dashboard import Layout

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

NODE_TRUTH = {10: (0.0, 0.0), 2: (454.0, 58.0), 11: (821.9, 115.5), 3: (-55.7, 396.1), 6: (380.0, 471.0),
              4: (766.3, 511.6), 12: (-122.5, 871.4), 7: (323.1, 934.1), 13: (715.0, 993.0)}
ROW_COL_EDGES = {frozenset(p) for p in [(10, 2), (2, 11), (3, 6), (6, 4), (12, 7), (7, 13),
                                        (10, 3), (3, 12), (2, 6), (6, 7), (11, 4), (4, 13)]}
MS = 1_000_000
RESULTS: dict[str, object] = {}


def note(key: str, value) -> None:
    RESULTS[key] = value
    print(f"E2E {key}={value}")


async def until(cond, timeout: float, step: float = 0.05, what: str = "condition") -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return time.monotonic() - t0
        await asyncio.sleep(step)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for {what}")


async def measure(http: httpx2.AsyncClient, tries: int = 20) -> dict:
    """The admin presses "Measure layout"; a node under a moving car makes it fail, so they press it again."""
    seen_ids: list[list[int]] = []
    for _ in range(tries):
        v = (await http.get("/api/layout")).json()["version"]
        r = await http.post("/api/layout/measure", json={"expected_layout_version": v})
        if r.status_code == 200:
            return r.json()
        assert r.json()["error"]["code"] == "nodes_not_visible", r.text
        seen_ids.append(r.json()["error"]["details"]["ids"])
        await asyncio.sleep(0.15)
    raise AssertionError(f"the layout could not be measured; not visible each time: {seen_ids}")


def strip_live(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in ("seen", "cams", "x_mm", "y_mm", "heading_deg", "age_ms",
                                                      "measured_size_mm", "size_warn", "placed")}


async def test_server_and_simulator_end_to_end(tmp_path):
    data = tmp_path / "data"
    t_start = time.monotonic()
    async with ServerHarness(data) as h:
        sv = h.sv
        # The cars start parked between the nodes and stay put until the layout is measured (a node under a car cannot
        # be measured); then the scripted driving starts.
        rig = await SimRig(make_scenario(h.http_port, parked=True), script=False).start()
        async with httpx2.AsyncClient(base_url=h.url, timeout=10) as http:
            # ---- the phone pairs, syncs and streams --------------------------------------------------------------
            await rig.phone.wait_connected()
            await until(lambda: sv.sessions.by_cam(1) and sv.sessions.by_cam(1).clock_sync.ok(sv.clock.mono_ns()), 15,
                        what="the camera to be synced")
            sess = sv.sessions.by_cam(1)
            assert sess.hello.link.mode == "wired_tether"

            # ---- roles and calibration ---------------------------------------------------------------------------
            r = await http.post("/api/venue-presets/sim_3x3/apply", json={})
            assert r.status_code == 200, r.text
            t_ok = await until(lambda: sv.world.snapshot().cameras.get(1) is not None
                               and sv.world.snapshot().cameras[1].calib["status"] == "OK"
                               and sv.world.calibs[1].model == "homography+metric", 30, what="the camera to be calibrated")
            note("seconds_from_preset_to_calibrated", round(t_ok, 1))
            await until(lambda: sum(t.seen for t in sv.world.snapshot().tags.values() if t.id in NODE_TRUTH) >= 8, 10,
                        what="the nodes to be seen")
            assert sorted(sv.world.snapshot().cameras[1].calib["floor_tags_seen"]) != []

            # ---- A2 (a)-(c): measure the layout ------------------------------------------------------------------
            layout = await measure(http)
            rig.script.start()
            Layout.model_validate(layout)
            errs = {n["id"]: math.hypot(n["x_mm"] - NODE_TRUTH[n["id"]][0], n["y_mm"] - NODE_TRUTH[n["id"]][1])
                    for n in layout["nodes"]}
            note("node_position_error_mm_max", round(max(errs.values()), 2))
            note("node_position_error_mm_mean", round(statistics.mean(errs.values()), 2))
            note("grid_rotation_deg", layout["grid_rotation_deg"])
            assert len(layout["nodes"]) == 9 and all(n["state"] == "ok" for n in layout["nodes"])      # (a) ok ...
            assert max(errs.values()) < 10.0, errs                                                     # ... within 10 mm
            assert abs(layout["grid_rotation_deg"] - 8.0) < 1.5                                        # (b)
            assert {frozenset((e["a"], e["b"])) for e in layout["edges"]} == ROW_COL_EDGES             # (c) 12 edges, no diagonal
            assert {n["id"]: n["station"]["name"] for n in layout["nodes"] if n["station"]} == \
                {2: "pickup", 3: "home", 4: "drop-off"}

            # ---- A2 (d): car poses vs ground truth, cars driving at 150 mm/s -------------------------------------
            tags = {t["id"]: t for t in (await http.get("/api/tags")).json()["tags"]}
            for name, cfg in ((c.name, c) for c in rig.sc.car):
                doc = {k: v for k, v in strip_live(tags[cfg.tag]).items() if k not in ("version", "updated_wall_ms", "updated_by")}
                doc.update(offset_mm=list(cfg.tag_offset_mm), expected_version=tags[cfg.tag]["version"])
                assert (await http.put(f"/api/tags/{cfg.tag}", json=doc)).status_code == 200
            samples: dict[str, dict[int, tuple[float, float, float, float, float]]] = {"DUO-A": {}, "DUO-B": {}}
            speeds = []

            def collect() -> None:
                for name, c in sv.world.snapshot().cars.items():
                    if c.capture_ns not in samples[name] and c.fresh and c.usable_for_control:
                        tx, ty, th = rig.world.true_pose(name, c.capture_ns)
                        samples[name][c.capture_ns] = (c.x, c.y, c.heading, tx, ty, th)
                        speeds.append(abs(rig.world.physics[name].speed_mm_s))

            t_end = time.monotonic() + 20
            while time.monotonic() < t_end and min(len(v) for v in samples.values()) < 120:
                await asyncio.sleep(0.01)
                collect()
            for name, rows in samples.items():
                a = np.array(list(rows.values()))
                pos = np.hypot(a[:, 0] - a[:, 3], a[:, 1] - a[:, 4])
                hdg = np.abs([wrap_deg(h - th) for h, th in zip(a[:, 2], a[:, 5])])
                note(f"{name}_pose_error_mm_median", round(float(np.median(pos)), 2))
                note(f"{name}_pose_error_mm_p95", round(float(np.percentile(pos, 95)), 2))
                note(f"{name}_heading_error_deg_median", round(float(np.median(hdg)), 2))
                note(f"{name}_samples", len(rows))
                assert len(rows) >= 60
                assert np.median(pos) < 20.0 and np.median(hdg) < 3.0, name                            # (d)
            note("car_speed_mm_s_max_seen", round(max(speeds), 1))
            assert max(speeds) > 100                                                                   # they really moved

            # ---- A2 (e) pose age and A14 link statistics ---------------------------------------------------------
            now = sv.clock.mono_ns()
            ages = sess.stats.pose_age()
            window_ns = int(sv.settings.tuning.pose.stats_window_s * 1e9)
            recent = [r for r in rig.phone.truth_log.values() if r.arrival_ns and r.arrival_ns > now - window_ns + 500 * MS]
            true_delay = statistics.median((r.arrival_ns - r.t_mid_ns) / 1e6 for r in recent)
            note("pose_age_p50_ms_server", round(ages.p50, 2))
            note("pose_age_p50_ms_true", round(true_delay, 2))
            assert abs(ages.p50 - true_delay) < 5.0, (ages.p50, true_delay)                            # (e)
            ls = sess.link_stats.summary()
            seqs = [s for _, s in sess.link_stats._seqs]
            first, last = seqs[0], seqs[-1]
            truth_window = [rig.phone.truth_log[k] for k in range(first, last + 1) if k in rig.phone.truth_log]
            true_loss = 100.0 * sum(r.dropped for r in truth_window) / len(truth_window)
            true_net = statistics.median(r.net_delay_ns / 1e6 for r in truth_window if not r.dropped)
            note("loss_pct_server", round(ls.loss_pct, 2))
            note("loss_pct_true", round(true_loss, 2))
            note("latency_p50_ms_server", round(ls.latency_p50_ms, 2))
            note("latency_p50_ms_true", round(true_net, 2))
            note("jitter_ms", round(ls.jitter_ms, 2))
            assert abs(ls.loss_pct - true_loss) <= 1.0                                                 # A14
            assert abs(ls.latency_p50_ms - true_net) <= 1.5

            # ---- the dashboard state is valid and shows both cars ----------------------------------------------
            state = sv.broadcaster.tick()
            from duoware.protocol.dashboard import StateMsg
            m = StateMsg.model_validate_json(state)
            assert m.cameras[0].calib.status == "OK" and m.cameras[0].sync.ok and all(c.pose and c.pose.fresh for c in m.cars)
            assert {c.pose.at_node for c in m.cars} <= set(NODE_TRUTH) | {None}

            # ---- A15: benchmark mode gives no control poses -----------------------------------------------------
            rig.phone.set_app_mode("benchmark")
            await until(lambda: sv.world.snapshot().cameras[1].app_mode == "benchmark", 3, what="the status update")
            snap = sv.world.snapshot()
            assert snap.cameras[1].usable and not snap.cameras[1].usable_for_control
            assert all(c.fresh and not c.usable_for_control for c in snap.cars.values())
            rig.phone.set_app_mode("tracking")
            await until(lambda: sv.world.snapshot().cameras[1].usable_for_control, 3, what="tracking mode again")

            # ---- A12: session switch -----------------------------------------------------------------------------
            fit_before = sv.world.calibs[1].fit_version
            assert sv.world.calibs[1].status.value == "OK"
            stale_ms = sv.settings.tuning.pose.stale_ms
            assert rig.phone.sessions_opened == 1 and sv.sessions.by_cam(1).sid == sess.sid, rig.phone.sessions_opened
            old_sid = sess.sid
            switch = asyncio.get_running_loop().create_task(rig.phone.switch_session("wireless", gap_s=stale_ms / 1000 + 0.4))
            await until(lambda: sv.sessions.by_sid(old_sid) is None, 2, 0.005, "the old session to end")
            t_last_frame = sv.clock.mono_ns()
            await asyncio.sleep((stale_ms + 100) / 1000)
            snap = sv.world.snapshot()
            from duoware.state_builder import camera_state
            cam = camera_state(sv, 1, sv.clock.mono_ns(), snap)
            note("switch_age_ms_of_last_pose", round(max(c.age_ms for c in snap.cars.values()), 1))
            assert all(not c.fresh for c in snap.cars.values())                                        # both cars stale ...
            assert cam["sync"]["ok"] is False and cam["online"] is False                               # ... and the camera unsynced
            await switch
            new = sv.sessions.by_cam(1)
            assert new.sid != old_sid and new.link_mode == "wireless"
            t_fresh = await until(lambda: all(c.fresh for c in sv.world.snapshot().cars.values())
                                  and len(sv.world.snapshot().cars) == 2, 8, what="fresh poses after the switch")
            note("seconds_until_fresh_after_reconnect", round(t_fresh, 2))
            assert sv.world.calibs[1].fit_version == fit_before and sv.world.calibs[1].status.value == "OK"   # not refitted
            mode_events = [e for e in sv.events.query(type="camera", limit=500) if e.key == "link_mode"]
            assert mode_events and mode_events[0].value == "wireless" and mode_events[0].prev == "wired_tether"

            # ---- numbers for the record + what must survive a restart ---------------------------------------------
            layout_before = (await http.get("/api/layout")).json()
            tags_before = (await http.get("/api/tags")).json()
            await rig.stop()

    # ---- A3: a restart on the same data directory -----------------------------------------------------------------
    async with ServerHarness(data) as h2:
        async with httpx2.AsyncClient(base_url=h2.url, timeout=10) as http:
            layout_after = (await http.get("/api/layout")).json()
            tags_after = (await http.get("/api/tags")).json()
    assert tags_after["registry_version"] == tags_before["registry_version"]
    assert [strip_live(t) for t in tags_after["tags"] if t["version"]] == [strip_live(t) for t in tags_before["tags"] if t["version"]]
    assert layout_after["version"] == layout_before["version"]
    assert layout_after["measured_wall_ms"] == layout_before["measured_wall_ms"]
    assert layout_after["grid_rotation_deg"] == layout_before["grid_rotation_deg"]
    key = lambda d: [(n["id"], n["grid"], n["x_mm"], n["y_mm"], n["station"], n["blocked_by"]) for n in d["nodes"]]  # noqa: E731
    assert key(layout_after) == key(layout_before) and layout_after["edges"] == layout_before["edges"]
    assert {n["state"] for n in layout_after["nodes"]} == {"outside_view"}                     # no camera after the restart
    note("test_seconds", round(time.monotonic() - t_start, 1))
    assert time.monotonic() - t_start < 60
