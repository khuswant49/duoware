"""Step 4 / A4: venue presets (built-in, saved, applied)."""

import shutil
from pathlib import Path

import pytest

from duoware.errors import DuoError
from duoware.registry.presets import VenuePresets
from duoware.settings import SettingsError, load_settings

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def presets(env):
    return VenuePresets(env.db, env.registry, env.overrides, env.events, env.settings, env.safety, env.clock)


def test_builtins_are_listed_and_read_only(presets):
    by = {p["name"]: p for p in presets.list()}
    assert by["sim_3x3"]["builtin"] and by["sim_3x3"]["tag_count"] == 11
    assert by["duoware1_kit"]["tag_count"] == 9 and not by["duoware1_kit"]["has_layout"]
    with pytest.raises(DuoError) as e:
        presets.delete("sim_3x3")
    assert (e.value.code, e.value.http) == ("preset_builtin", 409)
    with pytest.raises(DuoError) as e:
        presets.save("sim_3x3", overwrite=True)
    assert e.value.code == "preset_builtin"


def test_apply_builtin_replaces_registry_and_records_one_event(env, presets):
    env.registry.put(30, {"role": "obstacle"}, 0)              # will be removed by the preset
    n = env.events.last_id
    res = presets.apply("sim_3x3", expected_registry_version=1)
    assert env.events.last_id == n + 1
    ev = env.events.query()[0]
    assert ev.type == "registry" and ev.key == "preset:sim_3x3" and ev.facts["preset"] == "sim_3x3"
    snap = env.registry.snapshot()
    assert res.registry_version == snap.version == 2
    assert snap.get(30) is None and snap.origin().id == 10 and snap.car_tag("DUO-B").id == 5
    assert snap.get(6).fields.grid == (1, 1) and snap.get(2).station.name == "pickup"
    assert res.check == {"matched": [], "moved": [], "missing": []}


def test_apply_is_motion_affecting_and_checks_the_version(env, presets):
    env.mover.moving = ["DUO-A"]
    with pytest.raises(DuoError) as e:
        presets.apply("sim_3x3")
    assert e.value.code == "requires_stopped"
    env.safety.stop_all("local")
    presets.apply("sim_3x3")                                     # allowed under E-stop
    with pytest.raises(DuoError) as e:
        presets.apply("duoware1_kit", expected_registry_version=0)
    assert e.value.code == "version_conflict" and e.value.details["current_registry_version"] == 1


def test_apply_unknown_preset_is_404(presets):
    with pytest.raises(DuoError) as e:
        presets.apply("nope")
    assert (e.value.code, e.value.http) == ("not_found", 404)


def test_save_apply_delete_a_saved_preset(env, presets):
    presets.apply("duoware1_kit")
    env.overrides.update({"exposure_ms": 4.0, "iso": 400, "fps": 30}, "local")
    info = presets.save("Event A", "kit at the venue", operator="local")
    assert info["builtin"] is False and info["tag_count"] == 9 and info["has_camera"] and not info["has_layout"]
    with pytest.raises(DuoError) as e:
        presets.save("Event A")
    assert e.value.code == "preset_exists"
    presets.save("Event A", "again", overwrite=True)
    for bad in ("", "x" * 41, "-x", "a/b", 5):
        with pytest.raises(DuoError) as e:
            presets.save(bad)
        assert e.value.code == "validation"
    presets.apply("sim_3x3")
    env.overrides.update({"exposure_ms": None, "iso": None}, "local")
    presets.apply("Event A")
    assert env.registry.snapshot().get(2).station.name == "pickup" and env.registry.snapshot().get(6) is None
    assert env.overrides.get() == {"fps": 30.0, "exposure_ms": 4.0, "iso": 400}
    presets.delete("Event A")
    assert "Event A" not in {p["name"] for p in presets.list()}
    with pytest.raises(DuoError) as e:
        presets.delete("Event A")
    assert e.value.code == "not_found"


def test_saved_presets_survive_reopen(env):
    p = VenuePresets(env.db, env.registry, env.overrides, env.events, env.settings, env.safety, env.clock)
    p.apply("sim_3x3")
    p.save("mine")
    env.reopen()
    p2 = VenuePresets(env.db, env.registry, env.overrides, env.events, env.settings, env.safety, env.clock)
    assert "mine" in {x["name"] for x in p2.list()}


def test_a_bad_builtin_is_a_startup_error(env, tmp_path):
    cfg = tmp_path / "config"
    shutil.copytree(CONFIG, cfg)
    bad = cfg / "venue_presets" / "sim_3x3.toml"
    bad.write_text(bad.read_text().replace('car = "DUO-B"', 'car = "DUO-A"'))        # two tags for one car
    with pytest.raises(SettingsError, match="car_already_bound"):
        VenuePresets(env.db, env.registry, env.overrides, env.events, load_settings(cfg, tmp_path / "d"),
                     env.safety, env.clock)
    bad.write_text(bad.read_text().replace('car = "DUO-A"', 'car = "DUO-Q"', 1))
    with pytest.raises(SettingsError, match="unknown_car"):
        VenuePresets(env.db, env.registry, env.overrides, env.events, load_settings(cfg, tmp_path / "d"),
                     env.safety, env.clock)


def test_camera_override_validation(env):
    for bad in ({"exposure_ms": 0}, {"iso": 1.5}, {"resolution": [1280]}, {"fps": -1}, {"zoom": 2}, {"full_scan_every": 0}):
        with pytest.raises(DuoError) as e:
            env.overrides.update(bad, "local")
        assert e.value.code == "validation"
    got = []
    env.overrides.on_change(got.append)
    env.overrides.update({"resolution": [960, 540], "exposure_ms": 2}, "local")
    env.overrides.update({"resolution": None}, "local")
    assert env.overrides.get() == {"exposure_ms": 2.0} and len(got) == 2
    env.reopen()
    assert env.overrides.get() == {"exposure_ms": 2.0}
