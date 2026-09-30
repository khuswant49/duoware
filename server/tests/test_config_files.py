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
