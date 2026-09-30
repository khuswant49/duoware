"""M1 review fixes F1-F14 (docs/plans/M1.md "Opus review"): one test or more per fix, named test_fN_..."""

import asyncio
import dataclasses
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from conftest import patched_settings
from scene import MS, Rig, Scene, load_sim

from duoware.clock import SystemClock
from duoware.localization.calibration import CalibStatus, CameraCalibration
from duoware.localization.floor_tags import FloorTags
from duoware.monitor import LoopLagMonitor
from duoware.protocol.dashboard import StateMsg
from duoware.protocol.phone import Frame, encode_frame, parse_datagram
from duoware.registry.presets import VenuePresets
from duoware.registry.service import Change
from duoware.services import Services
from duoware.settings import SettingsError, load_settings

CONFIG = Path(__file__).resolve().parents[2] / "config"
SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def rig(env):
    r = Rig(env)
    r.converge(60)
    return r


def snap(r):
    return r.world.snapshot(r.env.clock.mono_ns())


def build_services(tmp_path):
    return Services.build(patched_settings(load_settings(data_dir=tmp_path / "d"), frames_port=0), mode="sim",
                          interfaces_fn=lambda: [])


# ---- F1: the broadcaster survives a failing tick ---------------------------------------------------------------


def test_f1_a_failing_build_state_does_not_stop_the_broadcaster(tmp_path, monkeypatch):
    import duoware.broadcaster as bc
    sv = build_services(tmp_path)
    try:
        calls = {"n": 0}
        real = bc.build_state

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real(*a, **k)

        monkeypatch.setattr(bc, "build_state", flaky)
        b = sv.broadcaster
        assert b.tick() is None and b.errors == 1
        StateMsg.model_validate_json(b.tick())
        assert b.errors == 1 and b.seq == 1

        def failing_tick(now):
            raise RuntimeError("tick")

        monkeypatch.setattr(sv.layout, "tick", failing_tick)
        assert b.tick() is not None and b.errors == 2          # layout.tick failing is survived too
    finally:
        sv.events.close()
        sv.db.close()


async def test_f1_the_broadcast_task_keeps_running_after_errors(tmp_path, monkeypatch):
    import duoware.broadcaster as bc
    sv = build_services(tmp_path)
    sent = []

    class C:
        async def send_text(self, t):
            sent.append(t)

    try:
        real = bc.build_state
        n = {"i": 0}

        def flaky(*a, **k):
            n["i"] += 1
            if n["i"] <= 2:
                raise RuntimeError("boom")
            return real(*a, **k)

        monkeypatch.setattr(bc, "build_state", flaky)
        sv.broadcaster.clients.add(bc.DashboardClient(C()))         # type: ignore[arg-type]
        sv.broadcaster.start()
        await asyncio.sleep(0.4)
        await sv.broadcaster.stop()
        assert len(sent) >= 3 and sv.broadcaster.errors == 2
    finally:
        sv.events.close()
        sv.db.close()


# ---- F3: a WEAK camera never gives control poses ---------------------------------------------------------------


def test_f3_weak_camera_has_poses_but_not_for_control(env):
    r = Rig(env)
    for _ in range(40):
        r.step(1, only={10, 1, 5})                                   # only the origin tag and the cars are visible
    s = snap(r)
    assert s.cameras[1].calib["status"] == "WEAK" and s.cameras[1].usable
    assert set(s.cars) == {"DUO-A", "DUO-B"} and all(c.fresh for c in s.cars.values())
    assert not s.cameras[1].usable_for_control and not any(c.usable_for_control for c in s.cars.values())


# ---- F4: a bumped camera is not read as "some tags moved" ------------------------------------------------------


def floor_only(scene, floor, only=None):
    return {t: px for t, px in scene.markers(only).items() if t in floor.tags}


def test_f4_a_small_camera_rotation_is_misaligned_and_no_tag_is_unplaced(rig):
    cal = rig.world.calib(1)
    placed = set(rig.floor.placed())
    assert len(placed) == 9 and cal.status == CalibStatus.OK
    a = np.radians(2.5)                                              # the image turns about its centre
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    for _ in range(3):
        m = floor_only(rig.scene, rig.floor)
        cal.observe({t: (px - [640, 360]) @ rot.T + [640, 360] for t, px in m.items()})
    assert cal.status == CalibStatus.MISALIGNED
    assert set(rig.floor.placed()) == placed


def test_f4_two_moved_tags_are_a_camera_problem_not_two_moved_tags(rig):
    cal = rig.world.calib(1)
    rig.scene.floor_tags[7][1] += 60.0
    rig.scene.floor_tags[13][1] += 60.0
    cal.observe(floor_only(rig.scene, rig.floor))
    assert cal.status == CalibStatus.MISALIGNED and len(rig.floor.placed()) == 9


def test_f4_one_moved_tag_needs_three_agreeing_tags(rig):
    cal = rig.world.calib(1)
    rig.scene.floor_tags[2][1] += 60.0
    cal.observe(floor_only(rig.scene, rig.floor, {10, 2, 3}))        # two witnesses are not enough
    assert cal.status == CalibStatus.MISALIGNED and rig.floor.tags[2].placed


def test_f4_a_fit_with_too_few_agreeing_tags_is_refused(rig):
    cal = rig.world.calib(1)
    rig.scene.floor_tags[2][1] += 80.0
    rig.scene.floor_tags[3][1] += 80.0
    for _ in range(15):                                               # let the moved tags fill their steady buffers
        cal.observe(floor_only(rig.scene, rig.floor, {10, 2, 3, 6}))
    fit = cal.fit_version
    cal.request_recalibration()
    for _ in range(15):
        cal.observe(floor_only(rig.scene, rig.floor, {10, 2, 3, 6}))   # 2 of 4 disagree: no majority
    assert cal.fit_version == fit and rig.floor.tags[2].placed and rig.floor.tags[3].placed


# ---- F5: a repeated marker ID is ignored --------------------------------------------------------------------


def dup_frame(f: Frame, tag_id: int, times: int = 1) -> Frame:
    mk = next(m for m in f.markers if m.id == tag_id)
    return dataclasses.replace(f, markers=f.markers + (mk,) * times)


def test_f5_codec_drops_every_entry_of_a_repeated_id_and_counts_each(rig):
    f = rig.scene.frame(rig.session, rig.env.clock.mono_ns())
    parsed = parse_datagram(encode_frame(dup_frame(f, 1)))
    assert parsed.bad_markers == 2 and all(m.id != 1 for m in parsed.markers) and len(parsed.markers) == len(f.markers) - 1
    p3 = parse_datagram(encode_frame(dup_frame(f, 6, 2)))
    assert p3.bad_markers == 3 and all(m.id != 6 for m in p3.markers)
    raw = json.loads(encode_frame(f))
    with pytest.raises(Exception):
        parse_datagram(json.dumps({**raw, "w": 0}).encode())          # w or h <= 0 is a bad message


def test_f5_world_gives_no_pose_from_a_frame_with_a_duplicated_car_tag(rig):
    before = snap(rig).cars["DUO-A"]
    rig.env.clock.advance(33 * MS)
    rig.sync()
    now = rig.env.clock.mono_ns()
    f = parse_datagram(encode_frame(dup_frame(rig.scene.frame(rig.session, now), 1)))
    rig.world.on_frame(rig.session, f, now)
    s = snap(rig)
    assert s.cars["DUO-A"].capture_ns == before.capture_ns           # no new pose from that frame
    assert s.cars["DUO-B"].capture_ns > before.capture_ns            # the other car is fine


# ---- F6: rebinding a car drops its pose and parallax scale ------------------------------------------------


def test_f6_rebinding_a_car_clears_its_pose_and_scale(rig, env):
    assert "DUO-A" in snap(rig).cars and "DUO-A" in rig.world._scale
    v1 = env.registry.snapshot().get(1).version
    env.registry.batch([Change(1, None, v1), Change(20, {"role": "car", "size_mm": 80, "car": "DUO-A"}, 0)])
    s = snap(rig)
    assert "DUO-A" not in s.cars and "DUO-A" not in rig.world._scale and "DUO-B" in s.cars
    rig.step(3)
    assert "DUO-A" not in snap(rig).cars                              # tag 20 has not been seen yet
    rig.scene.cars["DUO-A"]["tag"] = 20                               # the physical tag is now tag 20
    rig.step(3)
    assert snap(rig).cars["DUO-A"].tag == 20 and "DUO-A" in rig.world._scale


def test_f6_changing_the_tag_size_also_clears_it_but_a_label_does_not(rig, env):
    body = env.registry.snapshot().get(1).to_body()
    env.registry.put(1, {**body, "size_mm": 82}, env.registry.snapshot().get(1).version)
    assert "DUO-A" not in snap(rig).cars and "DUO-A" not in rig.world._scale
    rig.step(2)
    assert "DUO-A" in snap(rig).cars
    env.registry.put(1, {**body, "size_mm": 82, "label": "y"}, env.registry.snapshot().get(1).version)   # label only
    assert "DUO-A" in snap(rig).cars


# ---- F9: the clock-sync threshold is configuration --------------------------------------------------------------


def test_f9_slope_threshold_comes_from_tuning_toml(tmp_path):
    assert load_settings().tuning.clock_sync.slope_min_span_fraction == 0.25
    assert "SLOPE_MIN" not in (SRC / "duoware" / "ingest" / "clocksync.py").read_text(encoding="utf-8")
    cfg = tmp_path / "config"
    shutil.copytree(CONFIG, cfg)
    p = cfg / "tuning.toml"
    orig = p.read_text(encoding="utf-8")
    p.write_text(orig.replace("slope_min_span_fraction = 0.25", "slope_min_span_fraction = 1.5"), encoding="utf-8")
    with pytest.raises(SettingsError, match="slope_min_span_fraction"):
        load_settings(cfg)
    p.write_text(orig.replace("slope_min_span_fraction = 0.25\n", ""), encoding="utf-8")
    with pytest.raises(SettingsError, match="slope_min_span_fraction.*missing"):
        load_settings(cfg)


def test_f9_a_larger_fraction_switches_the_slope_off():
    from duoware.ingest.clocksync import ClockSync
    base = load_settings().tuning.clock_sync
    for frac, slope_used in ((0.0, True), (1.0, False)):
        cs = ClockSync(dataclasses.replace(base, slope_min_span_fraction=frac))
        for i in range(40):                                           # a drifting clock on a perfect path
            t = 10_000_000_000 + i * 500 * MS
            th = i * 50_000
            cs.add(t, t + th + MS // 2, t + th + MS // 2, t + MS)
        t_now = 10_000_000_000 + 40 * 500 * MS
        err = abs(cs.offset_ns(t_now) - 40 * 50_000)
        assert (err < 100_000) == slope_used


# ---- F10: a tag with the wrong printed size stays out of the refinement ----------------------------------------


def test_f10_a_wrongly_sized_tag_does_not_bend_the_fit(env):
    VenuePresets(env.db, env.registry, env.overrides, env.events, env.settings, env.safety, env.clock).apply("sim_3x3")
    scene = Scene(noise_px=0.07)
    body = env.registry.snapshot().get(6).to_body()                    # tag 6 is registered as 75 mm but is 90 mm
    env.registry.put(6, {**body, "size_mm": 75}, env.registry.snapshot().get(6).version)
    floor = FloorTags(env.registry)
    cal = CameraCalibration(1, floor, env.settings.tuning.localization, env.settings.tuning.markers.size_warn_fraction,
                            (1280, 720))
    for _ in range(120):
        cal.observe({t: px for t, px in scene.markers().items() if t in floor.tags})
    assert cal.status == CalibStatus.OK
    truth = {t[0]: (t[1], t[2]) for t in load_sim()["world"]["floor_tags"]}
    for tid, t in floor.placed().items():
        if tid != 6:
            assert np.hypot(t.x - truth[tid][0], t.y - truth[tid][1]) < 3.0, tid


# ---- F11: no dead field, no mojibake ---------------------------------------------------------------------------


def test_f11_no_perf_ok_and_no_mojibake_in_sources():
    assert "perf_ok" not in (SRC / "duoware" / "protocol" / "dashboard.py").read_text(encoding="utf-8")
    bad = []
    for base in (SRC, Path(__file__).resolve().parents[2] / "dashboard" / "src"):
        for p in base.rglob("*"):
            if p.suffix in (".py", ".ts", ".tsx") and "Â" in p.read_text(encoding="utf-8"):
                bad.append(str(p))
    assert bad == []


# ---- F12: the event-loop lag monitor ---------------------------------------------------------------------------


def stalls(env):
    return [e for e in env.events.query(type="system") if e.key == "loop_stall"]


def test_f12_lag_statistics_window_and_stall_events(env):
    cfg = env.settings.server.monitor
    m = LoopLagMonitor(cfg, env.clock, env.events)
    assert m.stats() == {"p95": None, "max": None}
    for v in range(1, 101):
        m.record(v / 10)                                              # 0.1 .. 10 ms: below stall_ms
    st = m.stats()
    assert st["max"] == 10.0 and 9.4 < st["p95"] < 9.7
    assert not stalls(env)
    m.record(cfg.stall_ms + 50)
    m.record(cfg.stall_ms + 80)                                       # within a minute: no second event
    assert len(stalls(env)) == 1 and stalls(env)[0].facts["overshoot_ms"] == cfg.stall_ms + 50
    env.clock.advance_s(61)
    m.record(cfg.stall_ms + 10)
    assert len(stalls(env)) == 2
    env.clock.advance_s(cfg.window_s + 1)
    assert m.stats() == {"p95": None, "max": None}                    # old samples leave the window


async def test_f12_the_probe_measures_a_blocked_loop(env):
    cfg = dataclasses.replace(env.settings.server.monitor, lag_probe_ms=20, stall_ms=50)
    m = LoopLagMonitor(cfg, SystemClock(), env.events)
    m.start()
    await asyncio.sleep(0.1)
    time.sleep(0.15)                                                  # block the loop
    await asyncio.sleep(0.1)
    await m.stop()
    assert m.stats()["max"] > 100 and stalls(env)


def test_f12_health_reports_loop_lag(api):
    time.sleep(0.4)
    h = api.get("/api/health").json()
    assert set(h["loop_lag_ms"]) == {"p95", "max"} and h["loop_lag_ms"]["max"] is not None


def test_f12_health_before_the_services_exist_has_null_lag():
    from fastapi.testclient import TestClient

    from duoware.app import create_app
    h = TestClient(create_app(mode="sim")).get("/api/health").json()
    assert h["loop_lag_ms"] == {"p95": None, "max": None}


# ---- F13: floor tags are updated by full frames only ----------------------------------------------------------


def test_f13_a_roi_frame_does_not_move_a_floor_tag(rig):
    x0 = snap(rig).tags[6].x
    rig.scene.floor_tags[6][1] += 25.0                                # 25 mm: below "moved", visible in any frame
    rig.step(1, scan="roi", searched=(1, 5, 6), only={1, 5, 6})
    t = snap(rig).tags[6]
    assert t.seen and t.x == x0                                       # seen (the phone did look) but not re-positioned
    rig.step(1, scan="full")
    assert abs(snap(rig).tags[6].x - x0) > 15


def test_f13_car_tags_still_update_from_roi_frames(rig):
    c0 = snap(rig).cars["DUO-A"].x
    rig.scene.cars["DUO-A"]["pose"][0] += 30
    rig.step(1, scan="roi", searched=(1, 5), only={1, 5})
    assert abs(snap(rig).cars["DUO-A"].x - c0) > 20


# ---- F14: no stale position for a seen tag without a synced, calibrated camera -----------------------------------


def test_f14_a_seen_tag_loses_its_position_when_the_camera_is_no_longer_synced(rig):
    s = snap(rig)
    assert s.tags[6].seen and s.tags[6].x is not None and s.tags[1].x is not None
    for _ in range(int(rig.env.settings.tuning.clock_sync.unsynced_after_s * 1000 / 33) + 5):
        rig.env.clock.advance(33 * MS)                                # frames keep coming, sync samples do not
        now = rig.env.clock.mono_ns()
        rig.world.on_frame(rig.session, rig.scene.frame(rig.session, now), now)
    s = snap(rig)
    assert not s.cameras[1].usable_for_control
    for tid in (6, 1, 10):
        t = s.tags[tid]
        assert t.seen and t.x is None and t.y is None and t.heading is None and t.size_mm is None, tid
    rig.sync()
    rig.step(2)
    assert snap(rig).tags[6].x is not None


def test_f14_a_moving_tag_position_ages_out_but_an_unseen_tag_keeps_its_last_one(rig):
    stale = rig.env.settings.tuning.pose.stale_ms
    for _ in range(3):                                                # frames keep coming, without the car tag in view
        rig.env.clock.advance(int((stale + 100) * MS / 3))
        rig.sync()
        now = rig.env.clock.mono_ns()
        rig.world.on_frame(rig.session, rig.scene.frame(rig.session, now, scan="roi", searched=(), only={5}), now)
    s = snap(rig)
    assert s.tags[1].seen and s.tags[1].x is None                     # seen (not searched) but the position is too old
    rig.step(1, scan="full", only=set())
    s = snap(rig)
    assert not s.tags[6].seen and s.tags[6].x is not None             # unseen: the last known position is kept
