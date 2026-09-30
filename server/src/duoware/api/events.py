"""GET /api/events (PROTOCOL.md §7.2, §8)."""

import asyncio

from fastapi import APIRouter, Depends

from duoware.api.deps import dashboard_guard, get_services
from duoware.errors import DuoError
from duoware.services import Services

EVENTS_LIMIT_MAX = 1000          # PROTOCOL.md §7.2
EVENTS_LIMIT_DEFAULT = 200       # PROTOCOL.md §7.2

router = APIRouter(dependencies=[Depends(dashboard_guard)])


@router.get("/api/events")
async def get_events(since_id: int = 0, limit: int = EVENTS_LIMIT_DEFAULT, car: str | None = None,
                     type: str | None = None, sv: Services = Depends(get_services)) -> dict:
    if not 1 <= limit <= EVENTS_LIMIT_MAX:
        raise DuoError("validation", 400, f"limit must be 1-{EVENTS_LIMIT_MAX}.", {"fields": ["limit"]})
    events = await asyncio.to_thread(sv.events.query, since_id, limit, car, type)
    return {"events": [e.to_dict() for e in events], "last_id": sv.events.last_id}
