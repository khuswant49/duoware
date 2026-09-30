"""M0: every file in config/ is valid TOML and has the top-level shape later milestones rely on.

M1 replaces the shape checks with the real loader (duoware/settings.py) and its own tests.
"""

import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "config"


def load(name: str) -> dict:
    with open(CONFIG / name, "rb") as f:
        return tomllib.load(f)


def test_all_config_files_parse():
    files = sorted(CONFIG.rglob("*.toml"))
    assert files, "no config files found"
    for path in files:
        with open(path, "rb") as f:
            tomllib.load(f)


def test_cars_have_unique_names_and_priorities():
    cars = load("cars.toml")["car"]
    assert len({c["name"] for c in cars}) == len(cars)
    assert len({c["priority"] for c in cars}) == len(cars)


def test_sim_cars_exist_in_cars_config():
    names = {c["name"] for c in load("cars.toml")["car"]}
    for car in load("sim.toml")["car"]:
        assert car["name"] in names


def test_venue_presets_are_consistent():
    car_names = {c["name"] for c in load("cars.toml")["car"]}
    for path in sorted((CONFIG / "venue_presets").glob("*.toml")):
        tags = load(f"venue_presets/{path.name}")["tag"]
        ids = [t["id"] for t in tags]
        assert len(set(ids)) == len(ids), path.name
        assert all(0 <= i <= 49 for i in ids), path.name
        assert sum(1 for t in tags if t.get("origin")) == 1, path.name
        grids = [tuple(t["grid"]) for t in tags if t.get("grid")]
        assert len(set(grids)) == len(grids), f"{path.name}: two nodes share a row/column"
        assert {t["car"] for t in tags if t["role"] == "car"} <= car_names, path.name


def test_sim_world_has_every_tag_of_the_sim_preset():
    world_ids = {t[0] for t in load("sim.toml")["world"]["floor_tags"]}
    world_ids |= {c["tag"] for c in load("sim.toml")["car"]}
    preset_ids = {t["id"] for t in load("venue_presets/sim_3x3.toml")["tag"]}
    assert preset_ids <= world_ids


# ---------------------------------------------------------------------------- M1 step 1: the real loader

import shutil

import pytest

from duoware.clock import FakeClock, SystemClock
from duoware.settings import SettingsError, load_env, load_settings


@pytest.fixture
def cfg_copy(tmp_path):
    dst = tmp_path / "config"
    shutil.copytree(CONFIG, dst)
    return dst


def test_real_config_loads():
    s = load_settings()
    assert s.server.http.port == 8000
    assert [c.name for c in s.cars] == ["DUO-A", "DUO-B"]
    assert s.tuning.camera.resolution == (1280, 720)
    assert s.tuning.phone.thermal.resolution_steps == ((960, 540),)
    assert s.map_rules.blocked.edges == ()
    assert s.car("DUO-B").priority == 1
    assert s.car("nope") is None


def test_unknown_key_names_the_key(cfg_copy):
    p = cfg_copy / "tuning.toml"
    p.write_text(p.read_text().replace("stale_ms = 300", "stale_ms = 300\nstale_msx = 3"))
    with pytest.raises(SettingsError, match=r"tuning\.toml.*pose\.stale_msx"):
        load_settings(cfg_copy)


def test_missing_key_names_the_key(cfg_copy):
    p = cfg_copy / "server.toml"
    p.write_text(p.read_text().replace("state_hz = 20", ""))
    with pytest.raises(SettingsError, match=r"dashboard\.state_hz.*missing"):
        load_settings(cfg_copy)


def test_wrong_type_names_the_key(cfg_copy):
    p = cfg_copy / "server.toml"
    p.write_text(p.read_text().replace("port = 8000", 'port = "8000"'))
    with pytest.raises(SettingsError, match=r"http\.port.*expected int"):
        load_settings(cfg_copy)


def test_duplicate_car_names_and_priorities_are_rejected(cfg_copy):
    p = cfg_copy / "cars.toml"
    p.write_text(p.read_text().replace('name = "DUO-B"', 'name = "DUO-A"'))
    with pytest.raises(SettingsError, match="unique"):
        load_settings(cfg_copy)
    p.write_text(p.read_text().replace('name = "DUO-A"\nfirmware = "car_uno"', 'name = "DUO-B"\nfirmware = "car_uno"')
                 .replace("priority = 1", "priority = 2"))
    with pytest.raises(SettingsError, match="priorities"):
        load_settings(cfg_copy)


def test_value_rules(cfg_copy):
    p = cfg_copy / "tuning.toml"
    orig = p.read_text()
    p.write_text(orig.replace("stale_ms = 300", "stale_ms = 0"))
    with pytest.raises(SettingsError, match="stale_ms"):
        load_settings(cfg_copy)
    p.write_text(orig.replace("best_fraction = 0.25", "best_fraction = 1.5"))
    with pytest.raises(SettingsError, match="best_fraction"):
        load_settings(cfg_copy)
    p.write_text(orig.replace("resolution = [1280, 720]", "resolution = [1280, 0]"))
    with pytest.raises(SettingsError, match="resolution"):
        load_settings(cfg_copy)
    p.write_text(orig.replace("resolution = [1280, 720]", "resolution = [1280]"))
    with pytest.raises(SettingsError, match="resolution"):
        load_settings(cfg_copy)


def test_env_file_parsing_and_environment_override(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nDUO_PAIR_CODE=ABCD-EF\nGROQ_API_KEY="quoted"\n\nBAD LINE\nDUO_ACCESS_CODE=\n')
    monkeypatch.delenv("DUO_PAIR_CODE", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    env = load_env(f)
    assert env["DUO_PAIR_CODE"] == "ABCD-EF"
    assert env["GROQ_API_KEY"] == "quoted"
    assert env["DUO_ACCESS_CODE"] == ""
    monkeypatch.setenv("DUO_PAIR_CODE", "ZZZZ-ZZ")
    assert load_env(f)["DUO_PAIR_CODE"] == "ZZZZ-ZZ"
    assert load_env(tmp_path / "missing.env") == {"DUO_PAIR_CODE": "ZZZZ-ZZ"}


def test_clocks():
    c = FakeClock(start_ns=5)
    assert c.mono_ns() == 5
    c.advance(1_000_000)
    assert c.mono_ns() == 1_000_005
    w0 = c.wall_ms()
    c.advance_s(2)
    assert c.wall_ms() == w0 + 2000
    s = SystemClock()
    assert s.mono_ns() <= s.mono_ns()
    assert s.wall_ms() > 1_700_000_000_000
