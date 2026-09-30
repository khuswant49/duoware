"""Step 6 / A13, A15: the world model — scan semantics, camera mode, parallax, sync, resolution change."""

import math

import numpy as np
import pytest
from scene import MS, Rig, sim_camera, status

from duoware.localization.geometry import wrap_deg

CAR_A, CAR_B = 1, 5                   # sim tag IDs of DUO-A and DUO-B


@pytest.fixture
def rig(env):
    r = Rig(env)
    r.converge(60)
    return r


def snap(r):
    return r.world.snapshot(r.env.clock.mono_ns())


def true_rotation_centre(scene, name):
    x, y, h = scene.cars[name]["pose"]
    return x, y, h


def test_floor_tags_are_located_and_cars_get_poses(rig):
    s = snap(rig)
    assert s.cameras[1].usable and s.cameras[1].usable_for_control and s.cameras[1].calib["status"] == "OK"
    assert s.tags[10].seen and s.tags[10].cams == (1,) and abs(s.tags[10].x) < 2 and abs(s.tags[10].y) < 2
    assert set(s.cars) == {"DUO-A", "DUO-B"} and all(c.fresh and c.usable_for_control for c in s.cars.values())


# ---- A13: ROI frames -------------------------------------------------------------------------------------------


def test_floor_tags_never_flip_to_unseen_between_full_scans(rig):
    scenes = rig.scene
    for i in range(40):
        full = i % 10 == 0
        rig.step(1, scan="full" if full else "roi", searched=() if full else (CAR_A, CAR_B),
                 only=None if full else {CAR_A, CAR_B})
        s = snap(rig)
        assert all(s.tags[t].seen for t in scenes.floor_tags), (i, [t for t in scenes.floor_tags if not s.tags[t].seen])


def test_a_node_tag_covered_by_a_car_becomes_unseen_after_the_next_full_scan(rig):
    rig.scene.hidden.add(6)                                        # a car drives over node 6
    rig.step(1, scan="roi", searched=(CAR_A, CAR_B), only={CAR_A, CAR_B})
    assert snap(rig).tags[6].seen                                   # the phone did not look for it in this frame
    rig.step(1, scan="full")
    t = snap(rig).tags[6]
    assert not t.seen and t.cams == () and t.x is not None          # last known position is kept
    rig.scene.hidden.clear()
    rig.step(1, scan="full")
    assert snap(rig).tags[6].seen


def test_a_car_tag_missing_from_a_roi_frame_is_unseen_only_if_it_was_searched(rig):
    assert snap(rig).tags[CAR_A].seen and snap(rig).tags[CAR_B].seen
    rig.step(1, scan="roi", searched=(CAR_A,), only=set())          # searched DUO-A's window: nothing there
    s = snap(rig)
    assert not s.tags[CAR_A].seen and s.tags[CAR_B].seen            # DUO-B was not searched: its observation stands
    assert "DUO-A" in s.cars                                        # the last pose is kept (and ages)
    a0 = s.cars["DUO-A"].age_ms
    rig.step(3, scan="roi", searched=(CAR_A,), only=set())
    assert snap(rig).cars["DUO-A"].age_ms > a0 + 80


def test_floor_fit_and_layout_logic_receive_full_frames_only(rig):
    calls = []
    cal = rig.world.calib(1)
    orig = cal.observe
    cal.observe = lambda m: (calls.append(1), orig(m))[1]
    rig.step(9, scan="roi", searched=(CAR_A, CAR_B), only={CAR_A, CAR_B})
    assert calls == []
    rig.step(1, scan="full")
    assert calls == [1]
    assert rig.world.full_frames_seen >= 61


def test_a_roi_frame_updates_car_poses(rig):
    x0 = snap(rig).cars["DUO-A"].x
    rig.scene.cars["DUO-A"]["pose"][0] += 40.0
    rig.step(1, scan="roi", searched=(CAR_A, CAR_B), only={CAR_A, CAR_B})
    assert abs(snap(rig).cars["DUO-A"].x - (x0 + 40)) < 6


# ---- A15: camera mode ------------------------------------------------------------------------------------------


def test_benchmark_and_setup_modes_give_no_control_poses(rig):
    for mode in ("benchmark", "setup"):
        rig.sessions.on_status(rig.session, status(mode))
        rig.step(3)
        s = snap(rig)
        assert s.cameras[1].app_mode == mode and s.cameras[1].usable and not s.cameras[1].usable_for_control
        assert s.cars["DUO-A"].fresh and not s.cars["DUO-A"].usable_for_control      # recorded, not for control
        assert not any(c.usable_for_control for c in s.cars.values())
    rig.sessions.on_status(rig.session, status("tracking"))
    rig.step(3)
    assert all(c.usable_for_control for c in snap(rig).cars.values())


def test_no_status_yet_means_not_usable_for_control(env):
    r = Rig(env)
    r.session.last_status = None
    r.converge(60)
    assert not snap(r).cameras[1].usable_for_control


# ---- sync, time, parallax --------------------------------------------------------------------------------------


def test_unsynced_camera_yields_no_poses(env):
    r = Rig(env)
    r.session.clock_sync = type(r.session.clock_sync)(env.settings.tuning.clock_sync)       # empty estimator
    for _ in range(40):
        env.clock.advance(33 * MS)
        now = env.clock.mono_ns()
        r.world.on_frame(r.session, r.scene.frame(r.session, now), now)
    s = r.world.snapshot(env.clock.mono_ns())
    assert s.cars == {} and all(t.x is None for t in s.tags.values()) and not s.cameras[1].usable_for_control
    assert s.tags[10].seen                                                          # seen, but no positions
    assert r.session.stats.pose_age().n == 0


def test_pose_age_uses_the_capture_time(rig):
    rig.step(1, delay_ms=55.0)
    car = snap(rig).cars["DUO-A"]
    assert car.age_ms == pytest.approx(55.0, abs=1.0)                              # the snapshot is taken at receive time
    rig.env.clock.advance(100 * MS)
    assert snap(rig).cars["DUO-A"].age_ms == pytest.approx(155.0, abs=1.0)
    ages = rig.session.stats.pose_age()
    assert ages.n > 0 and ages.p50 == pytest.approx(40.0, abs=1.0)                 # the converge() frames had 40 ms delay


def test_pose_goes_stale_after_stale_ms(rig):
    stale = rig.env.settings.tuning.pose.stale_ms
    rig.step(1, delay_ms=10)
    rig.env.clock.advance(int((stale - 50) * MS))
    assert snap(rig).cars["DUO-A"].fresh
    rig.env.clock.advance(100 * MS)
    assert not snap(rig).cars["DUO-A"].fresh


def test_car_pose_matches_truth_with_the_tag_offset_and_heading(env):
    r = Rig(env)
    r.converge(60)
    sim_a = r.scene.cars["DUO-A"]
    env.registry.put(CAR_A, {**env.registry.snapshot().get(CAR_A).to_body(), "offset_mm": sim_a["tag_offset_mm"]}, 1)
    # the registry change is a car edit: floor fit is unaffected
    for pose in ([350.0, 500.0, 37.0], [0.0, 300.0, -120.0], [600.0, 800.0, 170.0]):
        r.scene.cars["DUO-A"]["pose"][:] = pose
        r.step(60)
        c = r.world.snapshot(env.clock.mono_ns()).cars["DUO-A"]
        assert math.hypot(c.x - pose[0], c.y - pose[1]) < 8.0, (pose, c)
        assert abs(wrap_deg(c.heading - pose[2])) < 1.0


def test_parallax_correction_places_a_raised_tag_within_3mm(env):
    # A level camera 1800 mm up, tag 80 mm above the floor, 600 mm from the nadir: uncorrected error ~27 mm.
    r = Rig(env)
    r.scene.camera = sim_camera()
    from duoware.sim.camera_model import PinholeCamera
    r.scene.camera = PinholeCamera((350, 500, 1800), (0, 0), 1.0, (1280, 720), 70, -0.03)
    r.converge(60)
    sim = r.scene.cars["DUO-A"]
    sim["tag_height_mm"], sim["tag_offset_mm"] = 80, [0, 0]
    sim["pose"][:] = [350.0 + 560.0, 500.0 + 200.0, 0.0]            # 594 mm from the nadir
    r.step(80)
    c = r.world.snapshot(env.clock.mono_ns()).cars["DUO-A"]
    err = math.hypot(c.x - sim["pose"][0], c.y - sim["pose"][1])
    nadir = r.world.calib(1).nadir()
    raw = np.array(sim["pose"][:2]) - nadir
    uncorrected = np.linalg.norm(raw) * (1800 / 1720 - 1)
    assert uncorrected > 25 and err < 3.0, (err, uncorrected)


def test_implausible_tag_scale_is_ignored(rig):
    before = snap(rig).cars["DUO-A"]
    rig.scene.cars["DUO-A"]["tag_size_mm"] = 160.0                   # looks twice as big as printed: bad detection
    rig.step(3)
    after = snap(rig).cars["DUO-A"]
    assert after.capture_ns == before.capture_ns
    assert after.x == pytest.approx(before.x, abs=0.01)             # the old pose is kept, not a wrong new one


def test_resolution_change_keeps_positions(rig):
    x0 = snap(rig).tags[6]
    fit0 = rig.world.calib(1).fit_version
    rig.scene.camera = sim_camera((960, 540))
    for _ in range(3):
        rig.step(1, size=(960, 540))
    s = snap(rig)
    assert s.cameras[1].image_size == (960, 540) and s.cameras[1].calib["status"] == "OK"
    assert math.hypot(s.tags[6].x - x0.x, s.tags[6].y - x0.y) < 3.0
    assert rig.world.calib(1).fit_version == fit0                   # confirmed by the tags, not refitted


def test_recalibrate_drops_and_refits(rig):
    assert rig.world.recalibrate(1) and not rig.world.recalibrate(9)
    assert snap(rig).cameras[1].calib["status"] == "UNCALIBRATED"
    rig.step(80)
    assert snap(rig).cameras[1].calib["status"] == "OK"


def test_unregistered_tags_are_reported_while_seen_and_then_dropped(rig):
    rig.scene.floor_tags[30] = [30, 100.0, 200.0, -90, 60]          # a tag nobody registered
    rig.step(2)
    t = snap(rig).tags[30]
    assert t.seen and t.x is not None
    rig.scene.floor_tags.pop(30)
    rig.step(1)                                                      # a full scan that does not find it
    assert 30 not in snap(rig).tags


def test_node_position_needs_a_current_sighting(rig):
    p = rig.world.node_position(6)
    assert p is not None and math.hypot(p[0] - 380.0, p[1] - 471.0) < 3.0
    rig.env.clock.advance(int(1000 * MS))
    assert rig.world.node_position(6) is None                       # no recent frames: nothing is current
    assert rig.world.node_position(99) is None
