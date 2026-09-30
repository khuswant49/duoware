"""
DUO-WARE decision history.

Every important fleet decision is stored with its inputs, the options considered, the chosen
action and a plain-language reason, so an operator can ask "why?" and a run can be replayed.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DecisionEvent:
    event_id: int
    time: float
    kind: str                            # ASSIGN, RESERVE, TRAFFIC, REROUTE, FAULT, RECOVERY, ...
    decision: str
    reason: str
    robot_id: Optional[str] = None
    task_id: Optional[str] = None
    location: Optional[str] = None
    battery: Optional[float] = None
    prev_state: Optional[str] = None
    new_state: Optional[str] = None
    sensors: Dict[str, Any] = field(default_factory=dict)
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    human_override: bool = False
    operator_id: Optional[str] = None
    ai_recommendation: Optional[str] = None
    result: Optional[str] = None


class DecisionLog:
    def __init__(self):
        self.events: List[DecisionEvent] = []

    def record(self, time: float, kind: str, decision: str, reason: str, **kw) -> DecisionEvent:
        ev = DecisionEvent(event_id=len(self.events) + 1, time=time, kind=kind,
                           decision=decision, reason=reason, **kw)
        self.events.append(ev)
        return ev

    def find(self, kind: Optional[str] = None, robot_id: Optional[str] = None,
             task_id: Optional[str] = None) -> List[DecisionEvent]:
        return [e for e in self.events
                if (kind is None or e.kind == kind)
                and (robot_id is None or e.robot_id == robot_id)
                and (task_id is None or e.task_id == task_id)]

    def replay(self, start: float = float("-inf"), end: float = float("inf")) -> List[DecisionEvent]:
        return [e for e in self.events if start <= e.time <= end]

    def why(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Audit view for the latest assignment decision about a task (the dashboard WHY? button)."""
        hits = [e for e in self.events if e.task_id == task_id and e.kind in ("ASSIGN", "RESERVE", "OVERRIDE")]
        if not hits:
            return None
        e = hits[-1]
        return {
            "task": task_id,
            "decision": e.decision,
            "selected": e.robot_id,
            "candidates": e.alternatives,
            "explanation": e.reason,
            "human_override": e.human_override,
            "operator": e.operator_id,
            "ai_recommendation": e.ai_recommendation,
            "time": e.time,
        }

    def to_jsonl(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            for e in self.events:
                f.write(json.dumps(asdict(e)) + "\n")

    @staticmethod
    def format(e: DecisionEvent, t0: float = 0.0) -> str:
        who = " ".join(x for x in (e.robot_id, e.task_id) if x)
        tag = f" [HUMAN OVERRIDE by {e.operator_id}]" if e.human_override else \
            (f" [by {e.operator_id}]" if e.operator_id else "")
        return f"t={e.time - t0:7.1f}s  {e.kind:<10} {who:<18} {e.decision}{tag} | {e.reason}"
