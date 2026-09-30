"""Venue presets (DECISIONS.md D8, D28): the tag registry, the measured layout and camera exposure/ISO, saved
under a name. Built-ins are read-only data files in `config/venue_presets/`; saved ones live in state.db.
Applying one is an ordinary, safety-checked, logged change (one event)."""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from duoware.clock import Clock
from duoware.errors import DuoError
from duoware.ingest.overrides import PRESET_KEYS, CameraOverrides
from duoware.registry.model import check_conflicts, parse_body
from duoware.registry.service import TagRegistry
from duoware.safety import SafetyState
from duoware.settings import Settings, SettingsError
from duoware.store.db import StateDb
from duoware.store.events import EventLog

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}")
EMPTY_CHECK: dict[str, list] = {"matched": [], "moved": [], "missing": []}


class LayoutPort(Protocol):
    """What presets need from the layout service (step 7)."""

    def export(self) -> dict | None: ...

    def import_(self, doc: dict | None) -> None: ...

    def check_against_live(self, doc: dict | None) -> dict: ...

    @property
    def version(self) -> int: ...


@dataclass(frozen=True)
class ApplyResult:
    registry_version: int
    layout_version: int
    check: dict


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    builtin: bool
    doc: dict             # {"tags": [...], "layout": dict | None, "camera": dict | None}


def _validate_doc(doc: dict, settings: Settings, where: str) -> None:
    """Shape and conflicts of a preset's tags, as if they were applied to an empty registry."""
    tags = doc.get("tags")
    if not isinstance(tags, list):
        raise SettingsError(f"{where}: no [[tag]] entries")
    parsed = {}
    for t in tags:
        if not isinstance(t, dict) or "id" not in t:
            raise SettingsError(f"{where}: a tag entry has no id")
        try:
            d = parse_body(t["id"], t, settings.tuning.markers.default_size_mm)
        except DuoError as e:
            raise SettingsError(f"{where}: tag {t.get('id')}: {e.message}") from e
        if d.id in parsed:
            raise SettingsError(f"{where}: tag {d.id} appears twice")
        parsed[d.id] = d
    try:
        check_conflicts(parsed, {c.name for c in settings.cars})
    except DuoError as e:
        raise SettingsError(f"{where}: {e.message} ({e.code})") from e


def load_builtin_presets(config_dir: Path, settings: Settings) -> dict[str, Preset]:
    out: dict[str, Preset] = {}
    folder = config_dir / "venue_presets"
    for path in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SettingsError(f"{path}: invalid TOML ({e})") from e
        unknown = set(raw) - {"name", "description", "tag", "camera", "layout"}
        if unknown:
            raise SettingsError(f"{path}: unknown key(s) {sorted(unknown)}")
        name, desc = raw.get("name"), raw.get("description", "")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or not isinstance(desc, str):
            raise SettingsError(f"{path}: needs a valid name and description")
        if name in out:
            raise SettingsError(f"{path}: duplicate preset name {name!r}")
        doc = {"tags": raw.get("tag", []), "layout": raw.get("layout"), "camera": raw.get("camera")}
        _validate_doc(doc, settings, str(path))
        out[name] = Preset(name, desc, True, doc)
    return out


class VenuePresets:
    def __init__(self, db: StateDb, registry: TagRegistry, overrides: CameraOverrides, events: EventLog,
                 settings: Settings, safety: SafetyState, clock: Clock, layout: LayoutPort | None = None) -> None:
        self._db, self._registry, self._overrides, self._events = db, registry, overrides, events
        self._settings, self._safety, self._clock = settings, safety, clock
        self.layout = layout                            # wired in step 7
        self._builtin = load_builtin_presets(settings.config_dir, settings)

    # ------------------------------------------------------------------------------ reading

    def _saved(self) -> dict[str, Preset]:
        return {r["name"]: Preset(r["name"], r["description"], False, json.loads(r["doc"]))
                for r in self._db.query("SELECT * FROM venue_presets ORDER BY name")}

    def _get(self, name: str) -> Preset:
        p = self._builtin.get(name) or self._saved().get(name)
        if p is None:
            raise DuoError("not_found", 404, f"No venue preset named {name!r}.", {"name": name})
        return p

    def list(self) -> list[dict]:
        allp = list(self._builtin.values()) + list(self._saved().values())
        return [{"name": p.name, "description": p.description, "builtin": p.builtin,
                 "tag_count": len(p.doc["tags"]), "has_layout": bool(p.doc.get("layout")),
                 "has_camera": bool(p.doc.get("camera"))} for p in allp]

    # ------------------------------------------------------------------------------ writing

    def save(self, name: Any, description: str = "", overwrite: bool = False, operator: str = "local") -> dict:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise DuoError("validation", 400, "name must be 1-40 characters: letters, digits, space, _ . -",
                           {"fields": ["name"]})
        if name in self._builtin:
            raise DuoError("preset_builtin", 409, f"{name!r} is a built-in preset (read-only).", {"name": name})
        exists = name in self._saved()
        if exists and not overwrite:
            raise DuoError("preset_exists", 409, f"A preset named {name!r} already exists.", {"name": name})
        ov = self._overrides.get()
        camera = {k: ov[k] for k in PRESET_KEYS if k in ov} or None
        doc = {"tags": [t.to_body() for t in self._registry.snapshot().tags.values()],
               "layout": self.layout.export() if self.layout else None, "camera": camera}
        with self._db.tx() as c:
            c.execute("INSERT INTO venue_presets VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                      "description = excluded.description, doc = excluded.doc, created_wall_ms = excluded.created_wall_ms",
                      (name, description, json.dumps(doc), self._clock.wall_ms()))
        self._events.log("registry", operator=operator, key=f"preset:{name}", value="saved",
                         prev="overwritten" if exists else None,
                         facts={"tag_count": len(doc["tags"]), "has_layout": doc["layout"] is not None,
                                "camera": camera}, reason="operator saved the current setup as a venue preset")
        return next(p for p in self.list() if p["name"] == name)

    def delete(self, name: str, operator: str = "local") -> None:
        if name in self._builtin:
            raise DuoError("preset_builtin", 409, f"{name!r} is a built-in preset (read-only).", {"name": name})
        self._get(name)
        with self._db.tx() as c:
            c.execute("DELETE FROM venue_presets WHERE name = ?", (name,))
        self._events.log("registry", operator=operator, key=f"preset:{name}", value="deleted",
                         reason="operator deleted a saved venue preset")

    def apply(self, name: str, expected_registry_version: int | None = None, operator: str = "local") -> ApplyResult:
        """Motion-affecting (PROTOCOL.md §7.4). Replaces the registry, then the layout and camera exposure/ISO,
        and checks the preset's measured positions against what the camera sees now. One event."""
        preset = self._get(name)
        self._safety.require_motion_allowed()
        current = self._registry.snapshot().version
        if expected_registry_version is not None and expected_registry_version != current:
            raise DuoError("version_conflict", 409, "The tag registry changed since you last looked.",
                           {"current_registry_version": current})
        layout_doc = preset.doc.get("layout")
        camera = preset.doc.get("camera") or {}
        snap = self._registry.replace_all(
            preset.doc["tags"], operator, f"operator applied venue preset {name!r}", key=f"preset:{name}",
            extra_facts={"preset": name, "builtin": preset.builtin, "layout": layout_doc is not None,
                         "camera": camera or None})
        check = dict(EMPTY_CHECK)
        layout_version = 0
        if self.layout is not None:
            self.layout.import_(layout_doc)
            check = self.layout.check_against_live(layout_doc)
            layout_version = self.layout.version
        if camera:
            self._overrides.update({k: camera[k] for k in PRESET_KEYS if k in camera}, operator, log=False)
        return ApplyResult(snap.version, layout_version, check)
