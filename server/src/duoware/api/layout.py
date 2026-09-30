"""Road-network layout endpoints (PROTOCOL.md §7.2, §7.5)."""

from fastapi import APIRouter, Depends, Request

from duoware.api.control import OPERATOR
from duoware.api.deps import dashboard_guard, get_services
from duoware.api.util import json_body, require_int
from duoware.services import Services

router = APIRouter(dependencies=[Depends(dashboard_guard)])


@router.get("/api/layout")
async def get_layout(sv: Services = Depends(get_services)) -> dict:
    return sv.layout.current()


@router.post("/api/layout/suggest")
async def suggest(sv: Services = Depends(get_services)) -> dict:
    return sv.layout.suggest()


@router.post("/api/layout/measure")
async def measure(request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request)
    return sv.layout.measure(require_int(body, "expected_layout_version"), OPERATOR)


@router.put("/api/layout/blocked")
async def blocked(request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request)
    return sv.layout.set_blocked(body.get("nodes", []) if isinstance(body, dict) else None,
                                 body.get("edges", []) if isinstance(body, dict) else None,
                                 require_int(body, "expected_layout_version"), OPERATOR)
