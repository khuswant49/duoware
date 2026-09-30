import time

from fastapi import APIRouter, Request

from duoware import PROTOCOL_VERSION, __version__

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "version": __version__, "proto": PROTOCOL_VERSION, "mode": request.app.state.mode,
            "uptime_s": round(time.monotonic() - request.app.state.started, 1)}
