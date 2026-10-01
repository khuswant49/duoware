"""Camera setting overrides (PROTOCOL.md §7.2 `PUT /api/camera-settings`): values that replace the defaults of
`tuning.toml [camera]` / `[phone]`, stored in state.db and saved with venue presets."""

import json
import threading
from collections.abc import Callable
from typing import Any

from duoware.clock import Clock
from duoware.errors import DuoError
from duoware.store.db import StateDb
from duoware.store.events import EventLog

KEYS = ("exposure_ms", "iso", "fps", "resolution", "full_scan_every", "aruco3", "threads")
PRESET_KEYS = ("exposure_ms", "iso")          # PROTOCOL.md §7.2 / DECISIONS.md D28: presets carry exposure and ISO


def _bad(msg: str, key: str) -> DuoError:
    return DuoError("validation", 400, msg, {"fields": [key]})


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_value(key: str, v: Any) -> Any:
    if key in ("exposure_ms",):
        if not _is_num(v) or v <= 0:
            raise _bad("exposure_ms must be a number > 0", key)
        return float(v)
    if key == "fps":
        if not _is_num(v) or v < 0:
            raise _bad("fps must be a number >= 0 (0 = the sensor's maximum)", key)
        return float(v)
    if key in ("iso", "full_scan_every"):
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise _bad(f"{key} must be an integer >= 1", key)
        return v
    if key == "aruco3":
        if not isinstance(v, bool):
            raise _bad("aruco3 must be true or false", key)
        return v
    if key == "threads":
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise _bad("threads must be an integer >= 0 (0 = the fastest CPU cluster)", key)
        return v
    if key == "resolution":
        if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool)
                                                                      and x > 0 for x in v)):
            raise _bad("resolution must be [width, height] (positive integers)", key)
        return [v[0], v[1]]
    raise _bad(f"Unknown camera setting {key!r}", key)


class CameraOverrides:
    def __init__(self, db: StateDb, events: EventLog, clock: Clock) -> None:
        self._db, self._events, self._clock = db, events, clock
        self._lock = threading.RLock()
        self._observers: list[Callable[[dict], None]] = []
        self._values: dict[str, Any] = {r["key"]: json.loads(r["value"])
                                        for r in db.query("SELECT key, value FROM camera_overrides")}

    def get(self) -> dict[str, Any]:
        return dict(self._values)

    def on_change(self, cb: Callable[[dict], None]) -> None:
        self._observers.append(cb)

    def update(self, patch: dict[str, Any], operator: str | None, log: bool = True,
               reason: str = "operator changed the camera settings") -> dict[str, Any]:
        """`null` clears an override. Unknown keys and bad values raise `validation`."""
        if not isinstance(patch, dict):
            raise DuoError("validation", 400, "The body must be an object.")
        clean = {k: (None if v is None else validate_value(k, v)) for k, v in patch.items()}
        with self._lock:
            old = dict(self._values)
            new = dict(old)
            for k, v in clean.items():
                if v is None:
                    new.pop(k, None)
                else:
                    new[k] = v
            if new == old:
                return new
            with self._db.tx() as c:
                c.execute("DELETE FROM camera_overrides")
                c.executemany("INSERT INTO camera_overrides VALUES (?, ?)", [(k, json.dumps(v)) for k, v in new.items()])
            self._values = new
        if log:
            self._events.log("operator", operator=operator, key="camera_settings", value=new, prev=old,
                             facts={"old": old, "new": new}, reason=reason)
        for cb in self._observers:
            cb(new)
        return new
