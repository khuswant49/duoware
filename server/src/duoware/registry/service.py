"""The tag registry service: validated, versioned, safety-checked, logged changes (PROTOCOL.md §7.4)."""

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from duoware.clock import Clock
from duoware.errors import DuoError
from duoware.registry.model import (
    MOTION_ROLES, RegistryError, RegistrySnapshot, TagDoc, check_conflicts, doc_from_row, parse_body,
)
from duoware.safety import SafetyState
from duoware.settings import Settings
from duoware.store.db import StateDb
from duoware.store.events import EventLog

Observer = Callable[[RegistrySnapshot, RegistrySnapshot], None]


@dataclass(frozen=True)
class Change:
    id: int
    doc: dict | None              # None = unassign
    expected_version: int


def _role(d: TagDoc | None) -> str:
    return d.role if d else "unassigned"


class TagRegistry:
    def __init__(self, db: StateDb, events: EventLog, settings: Settings, safety: SafetyState, clock: Clock) -> None:
        self._db, self._events, self._settings, self._safety, self._clock = db, events, settings, safety, clock
        self._lock = threading.RLock()
        self._observers: list[Observer] = []
        docs = {r["id"]: doc_from_row(r) for r in db.query("SELECT * FROM tags")}
        self._snap = RegistrySnapshot(db.get_int_meta("registry_version"), docs)

    # ------------------------------------------------------------------------------ reading

    def snapshot(self) -> RegistrySnapshot:
        return self._snap

    def on_change(self, callback: Observer) -> None:
        self._observers.append(callback)

    # ------------------------------------------------------------------------------ writing

    def put(self, tag_id: int, body: Any, expected_version: int, operator: str = "local",
            reason: str = "operator changed the tag on the Tags page") -> TagDoc:
        self._apply([Change(tag_id, body, expected_version)], operator, f"tag:{tag_id}", reason)
        return self._snap.tags[tag_id]

    def delete(self, tag_id: int, expected_version: int, operator: str = "local") -> None:
        self._apply([Change(tag_id, None, expected_version)], operator, f"tag:{tag_id}",
                    "operator unassigned the tag on the Tags page")

    def batch(self, changes: list[Change], operator: str = "local",
              reason: str = "operator applied a batch of tag changes") -> list[TagDoc | None]:
        """All-or-nothing; one event."""
        self._apply(changes, operator, "batch", reason)
        return [self._snap.tags.get(c.id) for c in changes]

    def replace_all(self, bodies: list[dict], operator: str, reason: str, key: str = "replace_all",
                    extra_facts: dict | None = None) -> RegistrySnapshot:
        """Replaces the whole registry (venue presets). One event. Does not check the safety rule itself:
        callers that are motion-affecting by definition (preset apply) check it first."""
        ids = [b.get("id") if isinstance(b, dict) else None for b in bodies]
        if len(set(ids)) != len(ids):
            raise RegistryError("validation", 400, "Two tags in the list have the same id.", {"fields": ["id"]})
        self._apply([Change(i, b, 0) for i, b in zip(ids, bodies)], operator, key, reason, replace=True,
                    extra_facts=extra_facts, check_safety=False)
        return self._snap

    def _apply(self, changes: list[Change], operator: str, key: str, reason: str, replace: bool = False,
               extra_facts: dict | None = None, check_safety: bool = True) -> None:
        default_size = self._settings.tuning.markers.default_size_mm
        car_names = {c.name for c in self._settings.cars}
        with self._lock:
            cur = self._snap
            result: dict[int, TagDoc] = {} if replace else dict(cur.tags)
            touched: dict[int, tuple[TagDoc | None, TagDoc | None]] = {}     # id -> (old, new)
            now = self._clock.wall_ms()
            for ch in changes:
                old = cur.tags.get(ch.id)
                if not replace and ch.expected_version != (old.version if old else 0):
                    raise RegistryError("version_conflict", 409,
                                        f"Tag {ch.id} was changed by someone else (now version "
                                        f"{old.version if old else 0}).",
                                        {"id": ch.id, "current": old.to_json() if old else None})
                if ch.doc is None:
                    if ch.id in result and not replace:
                        del result[ch.id]
                        touched[ch.id] = (old, None)
                    continue
                new = parse_body(ch.id, ch.doc, default_size)
                if old is not None and old.to_body() == new.to_body():
                    result[ch.id] = old                                       # unchanged: keep its version
                    continue
                new = TagDoc(new.id, new.role, new.size_mm, new.label, new.fields, (old.version if old else 0) + 1,
                             now, operator)
                result[ch.id] = new
                touched[ch.id] = (old, new)
            if replace:
                for tid, old in cur.tags.items():
                    if tid not in result:
                        touched[tid] = (old, None)
            if not touched:
                return
            check_conflicts(result, car_names, only=set(touched))
            if check_safety and any((o and o.role in MOTION_ROLES) or (n and n.role in MOTION_ROLES)
                                    for o, n in touched.values()):
                self._safety.require_motion_allowed()
            version = cur.version + 1
            with self._db.tx() as c:
                for tid, (_, new) in touched.items():
                    if new is None:
                        c.execute("DELETE FROM tags WHERE id = ?", (tid,))
                    else:
                        c.execute("INSERT INTO tags(id, role, doc, version, updated_wall_ms, updated_by) "
                                  "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET role = excluded.role, "
                                  "doc = excluded.doc, version = excluded.version, "
                                  "updated_wall_ms = excluded.updated_wall_ms, updated_by = excluded.updated_by",
                                  (tid, new.role, json.dumps(new.to_body()), new.version, now, operator))
                c.execute("INSERT INTO meta(key, value) VALUES('registry_version', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(version),))
            snap = RegistrySnapshot(version, result)
            self._snap = snap
            self._log_event(touched, operator, key, reason, version, extra_facts)
            for cb in self._observers:
                cb(cur, snap)

    def _log_event(self, touched: dict[int, tuple[TagDoc | None, TagDoc | None]], operator: str, key: str,
                   reason: str, version: int, extra: dict | None) -> None:
        if len(touched) == 1 and key.startswith("tag:"):
            (old, new), = touched.values()
            self._events.log("registry", operator=operator, key=key, value=_role(new), prev=_role(old),
                             facts={"old": old.to_json() if old else None, "new": new.to_json() if new else None,
                                    "registry_version": version}, reason=reason)
            return
        self._events.log("registry", operator=operator, key=key, value=f"{len(touched)} tags", prev=None,
                         facts={"changes": [{"id": i, "old": o.to_json() if o else None, "new": n.to_json() if n else None}
                                            for i, (o, n) in sorted(touched.items())],
                                "registry_version": version, **(extra or {})}, reason=reason)


__all__ = ["Change", "DuoError", "TagRegistry"]
