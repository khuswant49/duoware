"""Venue preset endpoints (PROTOCOL.md §7.2)."""

from fastapi import APIRouter, Depends, Request, Response

from duoware.api.control import OPERATOR
from duoware.api.deps import dashboard_guard, get_services
from duoware.api.util import json_body
from duoware.errors import DuoError
from duoware.services import Services

router = APIRouter(dependencies=[Depends(dashboard_guard)])


@router.get("/api/venue-presets")
async def list_presets(sv: Services = Depends(get_services)) -> list[dict]:
    return sv.presets.list()


@router.post("/api/venue-presets")
async def save_preset(request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request)
    if not isinstance(body, dict):
        raise DuoError("validation", 400, "The body must be an object.")
    return sv.presets.save(body.get("name"), str(body.get("description") or ""), body.get("overwrite") is True, OPERATOR)


@router.post("/api/venue-presets/{name}/apply")
async def apply_preset(name: str, request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request, allow_empty=True)
    exp = body.get("expected_registry_version") if isinstance(body, dict) else None
    if exp is not None and (isinstance(exp, bool) or not isinstance(exp, int)):
        raise DuoError("validation", 400, "expected_registry_version must be an integer.",
                       {"fields": ["expected_registry_version"]})
    r = sv.presets.apply(name, exp, OPERATOR)
    return {"registry_version": r.registry_version, "layout_version": r.layout_version, "check": r.check}


@router.delete("/api/venue-presets/{name}", status_code=204)
async def delete_preset(name: str, sv: Services = Depends(get_services)) -> Response:
    sv.presets.delete(name, OPERATOR)
    return Response(status_code=204)
