"""GET /api/config: a read-only summary of the configuration the dashboard needs (PROTOCOL.md §7.2)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends

from duoware.api.deps import dashboard_guard, get_services
from duoware.registry.model import ROLES, STATION_KINDS
from duoware.services import Services

router = APIRouter(dependencies=[Depends(dashboard_guard)])


@router.get("/api/config")
async def get_config(sv: Services = Depends(get_services)) -> dict:
    s = sv.settings
    rules = s.map_rules
    return {"cars": [{"name": c.name, "priority": c.priority, "footprint_mm": list(c.footprint_mm), "color": c.color,
                      "transport": c.transport} for c in s.cars],
            "pose": {"stale_ms": s.tuning.pose.stale_ms},
            "markers": {"dictionary": s.tuning.markers.dictionary, "default_size_mm": s.tuning.markers.default_size_mm},
            "roles": list(ROLES), "station_kinds": list(STATION_KINDS),
            "layout_rules": {"validation": asdict(rules.validation), "tracking": asdict(rules.tracking)}}
