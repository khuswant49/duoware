"""Step 9: the simulated detector and the scenario loader."""

import dataclasses
import shutil
from pathlib import Path

import numpy as np
import pytest

from duoware.settings import SettingsError
from duoware.sim.detector_model import DetectorModel
from duoware.sim.scenario import DEFAULT_SCENARIO, load_scenario

SC = load_scenario()
MS = 1_000_000
TAG = np.array([[100, 100], [140, 100], [140, 140], [100, 140]], dtype=float)


def test_scenario_loads_strictly():
    assert [c.name for c in SC.car] == ["DUO-A", "DUO-B"] and len(SC.world.floor_tags) == 9
    assert SC.camera.resolution == (1280, 720) and SC.script.routes["DUO-A"] == (10, 2, 11, 2)
    assert SC.car_by_name("DUO-B").right_gain == 1.08


def test_scenario_errors_name_the_key(tmp_path):
    bad = tmp_path / "sim.toml"
    text = DEFAULT_SCENARIO.read_text(encoding="utf-8")
    bad.write_text(text.replace("hfov_deg = 70", "hfov_deg = 70\nextra = 1"), encoding="utf-8")
    with pytest.raises(SettingsError, match=r"camera\.extra"):
        load_scenario(bad)
    bad.write_text(text.replace("fps = 30 ", "fpz = 30 ", 1), encoding="utf-8")
    with pytest.raises(SettingsError, match="fps"):
        load_scenario(bad)
    bad.write_text(text.replace("drop_fraction = 0.01", 'drop_fraction = "x"'), encoding="utf-8")
    with pytest.raises(SettingsError, match="drop_fraction"):
        load_scenario(bad)


def test_probability_curve():
    d = DetectorModel(SC.phone_model, np.random.default_rng(0))
    m = SC.phone_model
    assert d.probability(0) == 1.0 and d.probability(m.blur_ok_px) == 1.0
    assert d.probability(m.blur_half_px) == pytest.approx(0.5)
    assert d.probability(m.blur_zero_px) == 0.0 and d.probability(100) == 0.0
    assert d.probability((m.blur_ok_px + m.blur_half_px) / 2) == pytest.approx(0.75)
    assert d.probability((m.blur_half_px + m.blur_zero_px) / 2) == pytest.approx(0.25)
    assert d.blur_px(300, 26 * MS) == pytest.approx(7.8)


def rate(exposure_ms: float, speed: float, n: int = 3000) -> float:
    d = DetectorModel(SC.phone_model, np.random.default_rng(1))
    return sum(len(d.observe({1: TAG}, {1: speed}, int(exposure_ms * MS))) for _ in range(n)) / n


def test_a_short_exposure_survives_motion_that_a_long_one_does_not():
    assert rate(3, 300) > 0.99                                    # 0.9 px of blur
    assert 0.25 < rate(26, 300) < 0.6                             # 7.8 px: between half and zero probability
    assert rate(26, 150) > 0.9 and rate(26, 600) == 0.0           # DUO-WARE 1: fine at 150 px/s, hopeless at 600
    assert rate(3, 0) == 1.0


def test_corner_noise_has_the_configured_sd_and_ids_are_kept():
    d = DetectorModel(SC.phone_model, np.random.default_rng(2))
    xs = []
    for _ in range(2000):
        (m,) = d.observe({7: TAG}, {}, 3 * MS)
        assert m.id == 7
        xs.append(np.array(m.corners) - TAG.ravel())
    sd = float(np.std(xs))
    assert sd == pytest.approx(SC.phone_model.corner_noise_px, rel=0.1)


def test_timing_draws_stay_within_the_configured_spread():
    d = DetectorModel(SC.phone_model, np.random.default_rng(3))
    p = [d.pipeline_ns() / MS for _ in range(500)]
    t = [d.detect_ns() / MS for _ in range(500)]
    (pm, ps), (tm, ts) = SC.phone_model.pipeline_ms, SC.phone_model.detect_ms
    assert pm - ps <= min(p) and max(p) <= pm + ps and abs(np.mean(p) - pm) < 1.5
    assert tm - ts <= min(t) and max(t) <= tm + ts and abs(np.mean(t) - tm) < 1.0
