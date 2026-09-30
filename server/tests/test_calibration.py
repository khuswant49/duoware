"""Step 6: camera calibration from floor tags (PROTOCOL.md §7.3, DECISIONS.md D28)."""

import math

import numpy as np
import pytest
from scene import Scene

from duoware.localization.calibration import CalibStatus, CameraCalibration
from duoware.localization.floor_tags import FloorTags
from duoware.registry.presets import VenuePresets


def setup(env, preset=True, noise=0.07):
    if preset:
        VenuePresets(env.db, env.registry, env.overrides, env.events, env.settings, env.safety, env.clock).apply("sim_3x3")
    floor = FloorTags(env.registry)
    events = []
    cal = CameraCalibration(1, floor, env.settings.tuning.localization, env.settings.tuning.markers.size_warn_fraction,
                            (1280, 720), lambda *a: events.append(a))
    return floor, cal, Scene(noise_px=noise), events


def floor_markers(scene, floor_ids, only=None):
    return {t: px for t, px in scene.markers(only).items() if t in floor_ids}


def feed(cal, scene, floor, n):
    for _ in range(n):
        cal.observe(floor_markers(scene, floor.tags))


def test_model_selection_by_number_of_visible_tags(env):
    # Typed-in poses (the true ones) so that tags count as placed from the start.
    for tid, x, y, yaw, size in [(10, 0, 0, -90, 90), (2, 454, 58, -83, 90), (11, 821.9, 115.5, -80, 90)]:
        body = {"role": "node", "size_mm": size}
        body.update({"origin": True} if tid == 10 else {"pose": {"x_mm": x, "y_mm": y, "yaw_deg": yaw}})
        env.registry.put(tid, body, 0)
    floor = FloorTags(env.registry)
    scene = Scene(noise_px=0.0)
    for visible, model, status in [({10}, "similarity", CalibStatus.WEAK), ({10, 2}, "affine", CalibStatus.OK),
                                   ({10, 2, 11}, "homography", CalibStatus.OK)]:
        cal = CameraCalibration(1, floor, env.settings.tuning.localization, 0.15, (1280, 720))
        for _ in range(12):
            cal.observe(floor_markers(scene, floor.tags, visible))
        assert (cal.model.split("+")[0], cal.status) == (model, status), visible
        assert cal.rms_mm < 12 and sorted(cal.tags_used) == sorted(visible)


def test_unplaced_tags_are_located_within_a_few_mm(env):
    floor, cal, scene, events = setup(env)
    assert [t for t in floor.placed()] == [10]                        # only the origin is known
    feed(cal, scene, floor, 80)
    assert cal.status == CalibStatus.OK and len(floor.placed()) == 9 and cal.model == "homography+metric"
    truth = {t[0]: t for t in __import__("scene").load_sim()["world"]["floor_tags"]}
    for tid, t in floor.placed().items():
        assert math.hypot(t.x - truth[tid][1], t.y - truth[tid][2]) < 3.0, tid
        assert abs(((t.yaw - truth[tid][3]) + 180) % 360 - 180) < 0.5
    assert cal.rms_mm < 1.0
    assert {e[0] for e in events} >= {"calibration", "floor_tag_located"}


def test_a_bumped_camera_is_misaligned_then_refits(env):
    floor, cal, scene, events = setup(env)
    feed(cal, scene, floor, 80)
    assert cal.status == CalibStatus.OK
    fit = cal.fit_version
    scene.shift_px = (20.0, 0.0)                                      # the whole image slides by 20 px (~40 mm)
    cal.observe(floor_markers(scene, floor.tags))
    assert cal.status == CalibStatus.MISALIGNED and cal.residual_mm > 20
    assert len(floor.placed()) == 9                                    # nobody was un-placed: most tags disagree
    feed(cal, scene, floor, env.settings.tuning.localization.drift_frames + env.settings.tuning.localization.settle_frames + 2)
    assert cal.status == CalibStatus.OK and cal.fit_version > fit
    assert ("calibration", "MISALIGNED") in [(e[0], e[1]) for e in events]


def test_one_moved_floor_tag_is_unplaced_and_located_again(env):
    floor, cal, scene, events = setup(env)
    feed(cal, scene, floor, 80)
    fit = cal.fit_version
    scene.floor_tags[7][1] += 60.0                                    # node 7 is pushed 60 mm
    cal.observe(floor_markers(scene, floor.tags))
    assert cal.status == CalibStatus.OK                                # the camera stays calibrated
    assert [t for t in floor.tags if not floor.tags[t].placed] == [7]  # and only that tag is forgotten
    assert ("floor_tag_moved", 7) in [(e[0], e[1]) for e in events]
    feed(cal, scene, floor, env.settings.tuning.localization.place_frames + 2)
    assert floor.tags[7].placed
    assert math.hypot(floor.tags[7].x - (323.1 + 60), floor.tags[7].y - 934.1) < 3.0
    assert cal.status == CalibStatus.OK and cal.fit_version == fit    # never refitted, never misaligned


def test_a_tag_that_ransac_rejects_during_a_fit_is_unplaced(env):
    floor, cal, scene, events = setup(env)
    feed(cal, scene, floor, 80)
    scene.floor_tags[13][2] += 80.0
    cal.request_recalibration()
    feed(cal, scene, floor, env.settings.tuning.localization.settle_frames + 3)
    assert cal.status == CalibStatus.OK and 13 not in cal.tags_used or floor.tags[13].placed
    feed(cal, scene, floor, 30)
    assert floor.tags[13].placed and abs(floor.tags[13].y - (993.0 + 80)) < 3.0


def test_changing_a_floor_tag_in_the_registry_resets_the_fit(env):
    floor, cal, scene, events = setup(env)
    feed(cal, scene, floor, 80)
    v = floor.version
    env.registry.put(9, {"role": "anchor", "size_mm": 90}, 0)         # a new floor tag
    assert floor.version == v + 1 and [t for t in floor.tags if floor.tags[t].placed] == [10]
    cal.observe(floor_markers(scene, floor.tags))
    assert cal.status == CalibStatus.UNCALIBRATED and cal.h is None
    feed(cal, scene, floor, 80)
    assert cal.status == CalibStatus.OK
    env.registry.put(2, {**env.registry.snapshot().get(2).to_body(), "label": "x"}, 1)     # not a geometry change
    assert floor.version == v + 1 or floor.version == v + 1


def test_non_geometry_changes_keep_positions(env):
    floor, cal, scene, _ = setup(env)
    feed(cal, scene, floor, 80)
    version = floor.version
    env.registry.put(2, {**env.registry.snapshot().get(2).to_body(), "grid": [5, 5]}, 1)
    assert floor.version == version and len(floor.placed()) == 9


def test_footprint_and_nadir(env):
    floor, cal, scene, _ = setup(env)
    assert cal.footprint() is None and cal.nadir() is None
    feed(cal, scene, floor, 80)
    fp = cal.footprint()
    assert fp.shape == (4, 2) and fp[:, 0].max() - fp[:, 0].min() > 2000          # about 2.5 m wide at 1.8 m height
    nadir = cal.nadir()
    # The lens is at (350, 500); the image centre hits the floor ~78 mm away because the camera is tilted by
    # (2.0, -1.5) degrees (1800 mm x tan(tilt)). DECISIONS.md D13 defines the nadir as the image centre.
    assert np.linalg.norm(nadir - [350, 500]) < 100


def test_size_warning_when_a_tag_is_not_the_printed_size(env):
    floor, cal, scene, _ = setup(env)
    scene.floor_tags[6][4] = 120.0                                    # printed as 90 mm but 120 mm on the floor
    feed(cal, scene, floor, 80)
    assert bool(cal.size_warn.get(6)) and not cal.size_warn.get(2)


def test_resolution_change_scales_the_fit_then_checks_it(env):
    floor, cal, scene, _ = setup(env)
    feed(cal, scene, floor, 80)
    before = cal.to_world(np.array([[640.0, 360.0]]))
    scene.camera = __import__("scene").sim_camera((960, 540))
    cal.on_resolution(960, 540)
    assert cal.status == CalibStatus.MISALIGNED and cal.image_size == (960, 540)
    assert np.linalg.norm(cal.to_world(np.array([[480.0, 270.0]])) - before) < 0.5       # same floor point
    fit = cal.fit_version
    cal.observe(floor_markers(scene, floor.tags))
    assert cal.status == CalibStatus.OK and cal.fit_version == fit                # confirmed by the tags, no refit
    for tid, t in floor.placed().items():
        px = floor_markers(scene, floor.tags)[tid]
        assert np.linalg.norm(cal.to_world(px).mean(axis=0) - [t.x, t.y]) < 3.0
    cal.on_resolution(1000, 1000)                                      # another aspect ratio: the fit is dropped
    assert cal.status == CalibStatus.UNCALIBRATED and cal.h is None
