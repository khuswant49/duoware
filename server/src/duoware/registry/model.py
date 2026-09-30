"""Tag documents and their validation (PROTOCOL.md Â§7.3, Â§7.4).

`parse_body` checks one document on its own (codes `validation`, `bad_tag_id`); `check_conflicts` checks the
rules that involve other tags over a whole resulting set (`unknown_car`, `car_already_bound`,
`grid_position_taken`, `station_name_taken`, `origin_exists`). Checking the final set rather than one change
at a time lets a batch swap two nodes' rows and columns.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from duoware.errors import DuoError
from duoware.store.db import TAG_ID_MAX

ROLES = ("car", "node", "anchor", "obstacle", "ignore")
FLOOR_ROLES = ("node", "anchor")                          # PROTOCOL.md Â§7.3
MOTION_ROLES = ("car", "node", "anchor")                  # PROTOCOL.md Â§7.4 safety rule
STATION_KINDS = ("pickup", "dropoff", "home", "charging", "custom")
SIZE_MM_RANGE = (10.0, 500.0)                             # PROTOCOL.md Â§7.4 size_mm
LABEL_MAX = 40                                            # PROTOCOL.md Â§7.4 label
STATION_NAME_MAX = 24                                     # PROTOCOL.md Â§7.4 station.name
RADIUS_MM_RANGE = (0.0, 1000.0)                           # PROTOCOL.md Â§7.4 radius_mm
ORIGIN_POSE = (0.0, 0.0, -90.0)                           # PROTOCOL.md Â§0: origin tag centre, TOP edge to -y

COMMON_KEYS = {"id", "role", "size_mm", "label"}
ROLE_KEYS: dict[str, set[str]] = {
    "car": {"car", "offset_mm", "heading_offset_deg"},
    "node": {"origin", "pose", "grid", "station"},
    "anchor": {"origin", "pose"},
    "obstacle": {"radius_mm"},
    "ignore": set(),
}
READ_ONLY_KEYS = {"placed", "version", "updated_wall_ms", "updated_by", "expected_version"}   # PROTOCOL.md Â§7.4


class RegistryError(DuoError):
    """A tag-registry error in the §7.1 shape."""


def _validation(message: str, **details: Any) -> RegistryError:
    return RegistryError("validation", 400, message, details)


@dataclass(frozen=True)
class Station:
    name: str
    kind: str


@dataclass(frozen=True)
class Pose:
    x_mm: float
    y_mm: float
    yaw_deg: float


@dataclass(frozen=True)
class CarRole:
    car: str
    offset_mm: tuple[float, float] | None = None
    heading_offset_deg: float | None = None


@dataclass(frozen=True)
class NodeRole:
    origin: bool = False
    pose: Pose | None = None
    grid: tuple[int, int] | None = None
    station: Station | None = None


@dataclass(frozen=True)
class AnchorRole:
    origin: bool = False
    pose: Pose | None = None


@dataclass(frozen=True)
class ObstacleRole:
    radius_mm: float | None = None


@dataclass(frozen=True)
class IgnoreRole:
    pass


RoleFields = CarRole | NodeRole | AnchorRole | ObstacleRole | IgnoreRole


@dataclass(frozen=True)
class TagDoc:
    id: int
    role: str
    size_mm: float
    label: str | None
    fields: RoleFields
    version: int = 0
    updated_wall_ms: int = 0
    updated_by: str = ""

    @property
    def is_floor(self) -> bool:
        return self.role in FLOOR_ROLES

    @property
    def origin(self) -> bool:
        return bool(getattr(self.fields, "origin", False))

    @property
    def grid(self) -> tuple[int, int] | None:
        return getattr(self.fields, "grid", None)

    @property
    def station(self) -> Station | None:
        return getattr(self.fields, "station", None)

    def to_body(self) -> dict:
        """The writable part: what `PUT` takes and what a venue preset stores."""
        d: dict[str, Any] = {"id": self.id, "role": self.role, "size_mm": self.size_mm, "label": self.label}
        f = self.fields
        if isinstance(f, CarRole):
            d.update(car=f.car, offset_mm=list(f.offset_mm) if f.offset_mm else None,
                     heading_offset_deg=f.heading_offset_deg)
        elif isinstance(f, (NodeRole, AnchorRole)):
            d["origin"] = f.origin
            d["pose"] = None if f.pose is None else {"x_mm": f.pose.x_mm, "y_mm": f.pose.y_mm, "yaw_deg": f.pose.yaw_deg}
            if isinstance(f, NodeRole):
                d["grid"] = list(f.grid) if f.grid else None
                d["station"] = None if f.station is None else {"name": f.station.name, "kind": f.station.kind}
        elif isinstance(f, ObstacleRole):
            d["radius_mm"] = f.radius_mm
        return d

    def to_json(self) -> dict:
        """The Â§7.4 document as returned by the API (without live fields)."""
        d = self.to_body()
        d.update(version=self.version, updated_wall_ms=self.updated_wall_ms, updated_by=self.updated_by)
        return d


def doc_from_row(row: Any) -> TagDoc:
    import json
    body = json.loads(row["doc"])
    doc = parse_body(row["id"], body, default_size_mm=body["size_mm"])
    return TagDoc(doc.id, doc.role, doc.size_mm, doc.label, doc.fields, row["version"], row["updated_wall_ms"],
                  row["updated_by"])


@dataclass(frozen=True)
class RegistrySnapshot:
    """Immutable view of the registry; replaced atomically after every commit."""

    version: int
    tags: Mapping[int, TagDoc] = field(default_factory=dict)

    def get(self, tag_id: int) -> TagDoc | None:
        return self.tags.get(tag_id)

    def car_tag(self, name: str) -> TagDoc | None:
        return next((t for t in self.tags.values() if isinstance(t.fields, CarRole) and t.fields.car == name), None)

    def car_tags(self) -> list[TagDoc]:
        return sorted((t for t in self.tags.values() if t.role == "car"), key=lambda t: t.id)

    def floor_tags(self) -> list[TagDoc]:
        return sorted((t for t in self.tags.values() if t.is_floor), key=lambda t: t.id)

    def nodes(self) -> list[TagDoc]:
        return sorted((t for t in self.tags.values() if t.role == "node"), key=lambda t: t.id)

    def obstacles(self) -> list[TagDoc]:
        return sorted((t for t in self.tags.values() if t.role == "obstacle"), key=lambda t: t.id)

    def origin(self) -> TagDoc | None:
        return next((t for t in self.floor_tags() if t.origin), None)


# ----------------------------------------------------------------------------------------- parsing


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _validation(f"{what} must be a number", fields=[what])
    return float(v)


def _opt_pair(v: Any, what: str) -> tuple[float, float] | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise _validation(f"{what} must be [a, b] or null", fields=[what])
    return (_num(v[0], what), _num(v[1], what))


def _parse_pose(v: Any) -> Pose | None:
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) != {"x_mm", "y_mm", "yaw_deg"}:
        raise _validation("pose must be {x_mm, y_mm, yaw_deg} or null", fields=["pose"])
    return Pose(_num(v["x_mm"], "pose"), _num(v["y_mm"], "pose"), _num(v["yaw_deg"], "pose"))


def _parse_station(v: Any) -> Station | None:
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) != {"name", "kind"}:
        raise _validation("station must be {name, kind} or null", fields=["station"])
    name, kind = v["name"], v["kind"]
    if not isinstance(name, str) or not 1 <= len(name) <= STATION_NAME_MAX:
        raise _validation(f"station.name must be 1-{STATION_NAME_MAX} characters", fields=["station"])
    if kind not in STATION_KINDS:
        raise _validation(f"station.kind must be one of {list(STATION_KINDS)}", fields=["station"])
    return Station(name, kind)


def _parse_grid(v: Any) -> tuple[int, int] | None:
    if v is None:
        return None
    ok = isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0
                                                              for x in v)
    if not ok:
        raise _validation("grid must be [row, col] (integers >= 0) or null", fields=["grid"])
    return (v[0], v[1])


def parse_body(tag_id: Any, body: Any, default_size_mm: float) -> TagDoc:
    """One document on its own. Raises `bad_tag_id` (400) or `validation` (400)."""
    if isinstance(tag_id, bool) or not isinstance(tag_id, int) or not 0 <= tag_id <= TAG_ID_MAX:
        raise RegistryError("bad_tag_id", 400, f"Tag ID must be 0-{TAG_ID_MAX} (DICT_4X4_50).", {"id": tag_id})
    if not isinstance(body, dict):
        raise _validation("The tag document must be an object.")
    role = body.get("role")
    if role not in ROLES:
        raise _validation(f"role must be one of {list(ROLES)}", fields=["role"])
    if "id" in body and body["id"] != tag_id:
        raise _validation("id in the body differs from the id in the path", fields=["id"])
    wrong = sorted(k for k in body if k not in COMMON_KEYS | ROLE_KEYS[role] | READ_ONLY_KEYS)
    if wrong:
        raise _validation(f"Field(s) {wrong} do not belong to role {role!r}.", fields=wrong)

    size = body.get("size_mm")
    if size is None:
        if role in ("car", "node", "anchor"):
            raise _validation(f"size_mm is required for role {role!r}", fields=["size_mm"])
        size = default_size_mm
    size = _num(size, "size_mm")
    if not SIZE_MM_RANGE[0] <= size <= SIZE_MM_RANGE[1]:
        raise _validation(f"size_mm must be {SIZE_MM_RANGE[0]:g}-{SIZE_MM_RANGE[1]:g}", fields=["size_mm"])
    label = body.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > LABEL_MAX):
        raise _validation(f"label must be text of at most {LABEL_MAX} characters or null", fields=["label"])

    f: RoleFields
    if role == "car":
        name = body.get("car")
        if not isinstance(name, str) or not name:
            raise _validation("car is required for role 'car'", fields=["car"])
        hdg = body.get("heading_offset_deg")
        f = CarRole(name, _opt_pair(body.get("offset_mm"), "offset_mm"), None if hdg is None else _num(hdg, "heading_offset_deg"))
    elif role in FLOOR_ROLES:
        origin = body.get("origin", False)
        if not isinstance(origin, bool):
            raise _validation("origin must be true or false", fields=["origin"])
        pose = _parse_pose(body.get("pose"))
        if origin and pose is not None:
            raise _validation("pose is not allowed on the origin tag (it is always 0, 0, -90)", fields=["pose"])
        f = (NodeRole(origin, pose, _parse_grid(body.get("grid")), _parse_station(body.get("station")))
             if role == "node" else AnchorRole(origin, pose))
    elif role == "obstacle":
        r = body.get("radius_mm")
        if r is not None:
            r = _num(r, "radius_mm")
            if not RADIUS_MM_RANGE[0] <= r <= RADIUS_MM_RANGE[1]:
                raise _validation(f"radius_mm must be {RADIUS_MM_RANGE[0]:g}-{RADIUS_MM_RANGE[1]:g}", fields=["radius_mm"])
        f = ObstacleRole(r)
    else:
        f = IgnoreRole()
    return TagDoc(tag_id, role, size, label, f)


# ----------------------------------------------------------------------------------------- conflicts


def check_conflicts(docs: Mapping[int, TagDoc], car_names: set[str], only: set[int] | None = None) -> None:
    """Rules involving other tags, over the resulting set `docs`. On a clash, `details.id` is the tag in `only`
    (the one being changed) when there is one, and `details.other` the tag it clashes with."""
    seen: dict[tuple, int] = {}

    def clash(kind: tuple, tid: int, code: str, message: str, **extra: Any) -> None:
        if kind in seen:
            a, b = seen[kind], tid
            if only is not None and a in only and b not in only:
                a, b = b, a
            raise RegistryError(code, 409, message, {"id": b, "other": a, **extra})
        seen[kind] = tid

    for tid in sorted(docs):
        d = docs[tid]
        f = d.fields
        if isinstance(f, CarRole):
            if f.car not in car_names and (only is None or tid in only):
                raise RegistryError("unknown_car", 409, f"{f.car!r} is not a car in cars.toml.",
                                    {"id": tid, "car": f.car, "cars": sorted(car_names)})
            clash(("car", f.car), tid, "car_already_bound", f"Car {f.car} already has another tag.", car=f.car)
        if isinstance(f, NodeRole):
            if f.grid is not None:
                clash(("grid", f.grid), tid, "grid_position_taken",
                      f"Row {f.grid[0]}, column {f.grid[1]} is already another node.", grid=list(f.grid))
            if f.station is not None:
                clash(("station", f.station.name), tid, "station_name_taken",
                      f"Station {f.station.name!r} already exists.", name=f.station.name)
        if d.is_floor and d.origin:
            clash(("origin",), tid, "origin_exists", "Another floor tag is already the origin (clear it first).")
