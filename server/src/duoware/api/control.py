"""Emergency stop (PROTOCOL.md §7.2). `stop_all` is exempt from the local-only and Origin guards (§7.1)."""

from fastapi import APIRouter, Depends, Request

from duoware.api.deps import dashboard_guard, get_services
from duoware.api.util import json_body
from duoware.errors import DuoError
from duoware.services import Services

OPERATOR = "local"           # PROTOCOL.md §7.1: until M8 adds named logins

open_router = APIRouter()
router = APIRouter(dependencies=[Depends(dashboard_guard)])


@open_router.post("/api/control/stop_all")
async def stop_all(sv: Services = Depends(get_services)) -> dict:
    sv.safety.stop_all(OPERATOR)
    return {"estop": True}


@router.post("/api/control/resume")
async def resume(request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request, allow_empty=True)
    if not isinstance(body, dict) or body.get("confirm") is not True:
        raise DuoError("confirm_required", 400, "Send {\"confirm\": true} to resume.", {})
    sv.safety.resume(OPERATOR)
    return {"estop": sv.safety.estop}
