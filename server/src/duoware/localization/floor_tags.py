"""Where the floor tags are (PROTOCOL.md §7.3-7.4, DECISIONS.md D13, D28).

The origin tag is fixed at (0, 0, -90); other floor tags (role `node` or `anchor`) either carry a typed-in pose
or are located automatically by a calibrated camera. Changing a floor tag's role, size, origin or pose in the
registry clears every automatically located position and bumps `version`, which resets every camera's fit.
"""

import threading
from dataclasses import dataclass

import numpy as np

from duoware.localization.geometry import square_corners
from duoware.registry.model import ORIGIN_POSE, RegistrySnapshot, TagDoc


@dataclass
class FloorTag:
    id: int
    size_mm: float
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    placed: bool = False
    source: str = "auto"              # "origin" | "typed" | "auto"

    def world_corners(self) -> np.ndarray:
        return square_corners(self.x, self.y, self.size_mm, self.yaw)


def _signature(doc: TagDoc) -> tuple:
    f = doc.fields
    pose = getattr(f, "pose", None)
    return (doc.id, doc.role, doc.size_mm, doc.origin, None if pose is None else (pose.x_mm, pose.y_mm, pose.yaw_deg))


class FloorTags:
    def __init__(self, registry) -> None:
        self._lock = threading.RLock()
        self.tags: dict[int, FloorTag] = {}
        self.version = 0                  # bumped when the registry changes the floor tags' geometry
        self.placements = 0               # bumped whenever a camera places or un-places a tag
        self._sig: frozenset = frozenset()
        self._rebuild(registry.snapshot(), keep_auto=False)
        registry.on_change(self._on_registry_change)

    def _on_registry_change(self, old: RegistrySnapshot, new: RegistrySnapshot) -> None:
        self._rebuild(new, keep_auto=True)

    def _rebuild(self, snap: RegistrySnapshot, keep_auto: bool) -> None:
        with self._lock:
            sig = frozenset(_signature(d) for d in snap.floor_tags())
            changed = sig != self._sig
            old = self.tags
            self.tags = {}
            for d in snap.floor_tags():
                pose = getattr(d.fields, "pose", None)
                t = FloorTag(d.id, d.size_mm)
                if d.origin:
                    t.x, t.y, t.yaw = ORIGIN_POSE
                    t.placed, t.source = True, "origin"
                elif pose is not None:
                    t.x, t.y, t.yaw, t.placed, t.source = pose.x_mm, pose.y_mm, pose.yaw_deg, True, "typed"
                elif keep_auto and not changed and d.id in old and old[d.id].placed:
                    t = old[d.id]
                self.tags[d.id] = t
            if changed:
                self._sig = sig
                self.version += 1
                self.placements += 1

    # ------------------------------------------------------------------------------ access

    def get(self, tag_id: int) -> FloorTag | None:
        return self.tags.get(tag_id)

    def is_floor(self, tag_id: int) -> bool:
        return tag_id in self.tags

    def placed(self) -> dict[int, FloorTag]:
        return {i: t for i, t in self.tags.items() if t.placed}

    def origin(self) -> FloorTag | None:
        return next((t for t in self.tags.values() if t.source == "origin"), None)

    # ------------------------------------------------------------------------------ automatic placement

    def place(self, tag_id: int, x: float, y: float, yaw: float) -> bool:
        """A calibrated camera measured an unplaced tag. The first measurement wins."""
        with self._lock:
            t = self.tags.get(tag_id)
            if t is None or t.placed:
                return False
            t.x, t.y, t.yaw, t.placed, t.source = float(x), float(y), float(yaw), True, "auto"
            self.placements += 1
            return True

    def unplace(self, tag_id: int, reason: str = "") -> bool:
        """Forgets an automatically located position (the tag moved): it is measured again."""
        with self._lock:
            t = self.tags.get(tag_id)
            if t is None or not t.placed or t.source != "auto":
                return False
            t.placed = False
            self.placements += 1
            return True

    def refine(self, poses: dict[int, tuple[float, float, float]]) -> None:
        """Replaces the positions of automatically located tags by better estimates (same tags, same source)."""
        with self._lock:
            for tid, (x, y, yaw) in poses.items():
                t = self.tags.get(tid)
                if t is not None and t.placed and t.source == "auto":
                    t.x, t.y, t.yaw = float(x), float(y), float(yaw)
            self.placements += 1
