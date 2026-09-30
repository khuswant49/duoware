"""
DUO-WARE Replayable Event Log

One JSON object per line (JSONL) in logs/events_<start time>.jsonl, plus the last events in memory
for the dashboard. Every event keeps MEASURED FACTS (positions, distances, modes, what was sent)
separate from the REASON (the explanation text the software gave at that moment), so a reader can
check the explanation against the facts.

    {"t": 1790..., "type": "state", "key": "robot_state", "value": "LOCALIZATION_HOLD",
     "prev": "DRIVING", "facts": {...}, "reason": "cameras disagree on the car position by 120 mm"}

Event types
    state      drive state (robot_state) changed          operator   E-stop, resume, target, mode, mission
    gate       localization gate mode changed             camera     stale / calibration changes
    mission    4-stage mission stage changed              link       car connection changed
    command    a distinct command was sent to the car (or refused under E-stop)

why_stopped(events) answers "why is / did the car stop?" from the log alone.
"""

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Optional

STOP_STATES = {"EMERGENCY_STOP", "LOCALIZATION_HOLD", "ARRIVED", "STALLED", "WAITING_VISION", "IDLE",
               "COMPLETED", "ABORTED", "WAITING_OBJECT", "WAITING_DESTINATION"}
MOVING_STATES = {"TURNING", "SETTLING", "DRIVING", "BRAKING", "TO_OBJECT", "TO_DESTINATION", "RETURNING_ORIGIN"}


def _clean(v: Any) -> Any:
    """JSON-safe, compact values (round floats, drop numpy types)."""
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):                                   # numpy scalar
        return _clean(v.item())
    if v is None or isinstance(v, (str, int, bool)):
        return v
    return str(v)


class EventLog:
    def __init__(self, directory: Optional[str] = None, keep: int = 2000):
        self._lock = threading.Lock()
        self._recent: deque = deque(maxlen=keep)
        self._last: Dict[str, Any] = {}
        self._last_cmd = (None, 0.0)
        self.path: Optional[str] = None
        if directory:
            os.makedirs(directory, exist_ok=True)
            self.path = os.path.join(directory, time.strftime("events_%Y%m%d_%H%M%S.jsonl"))

    def log(self, type_: str, facts: Optional[Dict[str, Any]] = None, reason: str = "", t: Optional[float] = None,
            **fields) -> Dict[str, Any]:
        ev = {"t": round(time.time() if t is None else t, 3), "type": type_}
        ev.update(_clean(fields))
        ev["facts"] = _clean(facts or {})
        ev["reason"] = reason or ""
        with self._lock:
            self._recent.append(ev)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
        return ev

    def changed(self, type_: str, key: str, value: Any, facts=None, reason: str = "", t=None) -> bool:
        """Logs only when `key` takes a new value (state machines, modes, statuses)."""
        v = _clean(value)
        with self._lock:
            prev = self._last.get(key, "__unset__")
            if prev == v:
                return False
            self._last[key] = v
        self.log(type_, facts, reason, t=t, key=key, value=v, prev=None if prev == "__unset__" else prev)
        return True

    def command(self, cmd: str, accepted: bool = True, t: Optional[float] = None):
        """A command to the car. Repeats of the same command within 1 s are not logged (keep-alives)."""
        now = time.time() if t is None else t
        with self._lock:
            last, when = self._last_cmd
            if accepted and cmd == last and now - when < 1.0:
                return
            if accepted:
                self._last_cmd = (cmd, now)
        self.log("command", {"cmd": cmd, "accepted": accepted}, "" if accepted else "refused: E-stop active", t=now)

    def recent(self, since: float = 0.0, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            evs = [e for e in self._recent if e["t"] > since]
        return evs[-limit:]


# ---------------------------------------------------------------------- analysis

_CAUSE_PRIORITY = [
    ("EMERGENCY_STOP", "operator E-stop"),
    ("LOCALIZATION_HOLD", "cameras disagreed about the car position"),
    ("WAITING_VISION", "the camera lost the car (or its frames were too old)"),
    ("STALLED", "no progress while driving (blocked or slipping)"),
    ("ARRIVED", "reached the target"),
]


def load(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def why_stopped(events: Iterable[Dict[str, Any]], window_sec: float = 3.0) -> Optional[Dict[str, Any]]:
    """The last time the car went from moving to stopped, and why, from the log alone."""
    evs = sorted(events, key=lambda e: e["t"])
    stop = None
    for e in evs:
        if e["type"] == "state" and e.get("key") == "robot_state" and e.get("value") in STOP_STATES \
                and (e.get("prev") in MOVING_STATES or e.get("value") in ("EMERGENCY_STOP", "LOCALIZATION_HOLD")):
            stop = e
    if stop is None:
        return None
    t0 = stop["t"]
    context = [e for e in evs if t0 - window_sec <= e["t"] <= t0 + 0.5 and e is not stop]
    evidence = []
    for e in context:
        if e["type"] == "operator":
            evidence.append(f"{e['t'] - t0:+.2f}s operator: {e.get('action')} {e['reason']}".rstrip())
        elif e["type"] == "gate" and e.get("value") == "HOLD":
            evidence.append(f"{e['t'] - t0:+.2f}s localization gate HOLD: {e['reason']}")
        elif e["type"] == "camera":
            evidence.append(f"{e['t'] - t0:+.2f}s camera: {e.get('key')} = {e.get('value')} {e['reason']}".rstrip())
        elif e["type"] == "link":
            evidence.append(f"{e['t'] - t0:+.2f}s car link: {e.get('value')}")
        elif e["type"] == "command" and e["facts"].get("cmd", "")[:1] in ("S", "K", "X"):
            evidence.append(f"{e['t'] - t0:+.2f}s command sent: {e['facts']['cmd']}")
    cause = dict(_CAUSE_PRIORITY).get(stop["value"], f"state {stop['value']}")
    return {
        "t": t0,
        "state": stop["value"],
        "previous_state": stop.get("prev"),
        "cause": cause,
        "software_reason": stop.get("reason", ""),          # what the software said at the time
        "facts": stop.get("facts", {}),                     # what was measured at the time
        "evidence": evidence,                               # other events around the stop
    }


def explain(result: Optional[Dict[str, Any]]) -> str:
    if result is None:
        return "No stop found in the log (the car never went from moving to stopped)."
    f = result["facts"]
    lines = [f"Stopped ({result['previous_state']} -> {result['state']}): {result['cause']}."]
    if result["software_reason"]:
        lines.append(f"  Software said: {result['software_reason']}")
    if f:
        lines.append("  Measured: " + ", ".join(f"{k}={v}" for k, v in f.items()))
    for ev in result["evidence"]:
        lines.append("  " + ev)
    return "\n".join(lines)
