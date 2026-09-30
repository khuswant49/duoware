import time

from fastapi import APIRouter, Request

from duoware import PROTOCOL_VERSION, __version__

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict:
    out = {"status": "ok", "version": __version__, "proto": PROTOCOL_VERSION, "mode": request.app.state.mode,
           "uptime_s": round(time.monotonic() - request.app.state.started, 1)}
    # services are built on first use; before that (or without a started loop) there is nothing measured yet
    out["loop_lag_ms"] = request.app.state.get_services().monitor.stats() if request.app.state.has_services()         else {"p95": None, "max": None}
    return out
