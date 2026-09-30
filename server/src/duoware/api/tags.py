"""Tag registry endpoints (PROTOCOL.md §7.2, §7.4)."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from duoware.api.control import OPERATOR
from duoware.api.deps import dashboard_guard, get_services
from duoware.api.util import json_body, require_int
from duoware.errors import DuoError
from duoware.registry.model import TagDoc
from duoware.registry.service import Change
from duoware.services import Services

NS_PER_MS = 1_000_000
router = APIRouter(dependencies=[Depends(dashboard_guard)])


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def tag_entry(sv: Services, tag_id: int, doc: TagDoc | None, world) -> dict[str, Any]:
    """The stored document (only the fields of its role) plus the live fields of `state.tags`. The measured floor
    size is `measured_size_mm`, because `size_mm` is the printed size in the document."""
    o = world.tags.get(tag_id)
    ft = sv.floor.get(tag_id)
    now = world.now_ns
    base = doc.to_json() if doc else {"id": tag_id, "role": "unassigned", "size_mm": None, "label": None, "version": 0,
                                      "updated_wall_ms": None, "updated_by": None}
    base.update(placed=None if ft is None else ft.placed, seen=bool(o and o.seen), cams=list(o.cams) if o else [],
                x_mm=_r(o.x) if o else None, y_mm=_r(o.y) if o else None, heading_deg=_r(o.heading) if o else None,
                age_ms=_r((now - o.capture_ns) / NS_PER_MS) if o and o.capture_ns else None,
                measured_size_mm=_r(o.size_mm) if o else None, size_warn=bool(o and o.size_warn))
    return base


def tag_list(sv: Services) -> dict[str, Any]:
    world = sv.world.snapshot()
    reg = sv.registry.snapshot()
    ids = sorted(set(reg.tags) | {t for t, o in world.tags.items() if o.seen})
    return {"registry_version": reg.version, "tags": [tag_entry(sv, i, reg.get(i), world) for i in ids]}


@router.get("/api/tags")
async def get_tags(sv: Services = Depends(get_services)) -> dict:
    return tag_list(sv)


@router.put("/api/tags/{tag_id}")
async def put_tag(tag_id: int, request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request)
    expected = require_int(body, "expected_version")
    doc = sv.registry.put(tag_id, body, expected, OPERATOR)
    return tag_entry(sv, tag_id, doc, sv.world.snapshot())


@router.delete("/api/tags/{tag_id}")
async def delete_tag(tag_id: int, expected_version: int, sv: Services = Depends(get_services)) -> dict:
    sv.registry.delete(tag_id, expected_version, OPERATOR)
    return {"id": tag_id, "role": "unassigned"}


@router.post("/api/tags/batch")
async def batch_tags(request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request)
    raw = body.get("changes") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        raise DuoError("validation", 400, "changes (a list) is required.", {"fields": ["changes"]})
    changes = []
    for c in raw:
        if not isinstance(c, dict):
            raise DuoError("validation", 400, "Every change must be an object.", {"fields": ["changes"]})
        changes.append(Change(c.get("id"), c.get("doc"), require_int(c, "expected_version")))   # type: ignore[arg-type]
    sv.registry.batch(changes, OPERATOR)
    world, reg = sv.world.snapshot(), sv.registry.snapshot()
    return {"registry_version": reg.version,
            "tags": [tag_entry(sv, c.id, reg.get(c.id), world) for c in changes]}
