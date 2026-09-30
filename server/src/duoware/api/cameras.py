"""Camera endpoints (PROTOCOL.md §7.2): state + capabilities, settings overrides, recalibrate."""

from fastapi import APIRouter, Depends, Request

from duoware.api.control import OPERATOR
from duoware.api.deps import dashboard_guard, get_services
from duoware.api.util import json_body
from duoware.errors import DuoError
from duoware.services import Services
from duoware.state_builder import camera_state

router = APIRouter(dependencies=[Depends(dashboard_guard)])


@router.get("/api/cameras")
async def list_cameras(sv: Services = Depends(get_services)) -> list[dict]:
    now = sv.clock.mono_ns()
    world = sv.world.snapshot(now)
    return [camera_state(sv, cam, now, world) for cam in sorted(set(sv.sessions.known_cams()) | set(world.cameras))]


@router.put("/api/camera-settings")
async def put_camera_settings(request: Request, sv: Services = Depends(get_services)) -> dict:
    body = await json_body(request)
    return {"overrides": sv.overrides.update(body, OPERATOR)}


@router.post("/api/cameras/{cam}/recalibrate")
async def recalibrate(cam: int, sv: Services = Depends(get_services)) -> dict:
    if not sv.world.recalibrate(cam):
        raise DuoError("not_found", 404, f"Camera {cam} has no calibration yet.", {"cam": cam})
    sv.events.log("operator", operator=OPERATOR, key="recalibrate", value=cam, facts={"cam": cam},
                  reason="operator asked for a new camera calibration")
    return {"cam": cam, "status": "UNCALIBRATED"}
