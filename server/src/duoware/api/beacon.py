"""GET /api/beacon (PROTOCOL.md §4.2, §7.2): the discovery beacon over HTTP, for `wired_adb` (through `adb reverse`,
the phone probes 127.0.0.1) and for a Wi-Fi-client phone that hears no broadcast (it probes its gateway).

Exempt from the local-only and Origin rules (§7.1): it is read-only, carries nothing but what the UDP beacon already
broadcasts, and the phone must reach it before it is paired.
"""

import json

from fastapi import APIRouter, Depends, Request

from duoware.api.deps import get_services
from duoware.ingest.beacon import SERVER_NAME
from duoware.protocol.phone import Beacon, encode_beacon
from duoware.services import Services

router = APIRouter()


def arrival_host(request: Request) -> str:
    """The address the request arrived on: the local end of the connection (`adb reverse` gives 127.0.0.1, a hotspot
    gives its own address), else the Host header without its port."""
    server = request.scope.get("server")
    if server and server[0]:
        return str(server[0])
    return request.headers.get("host", "").rsplit(":", 1)[0]


@router.get("/api/beacon")
async def get_beacon(request: Request, sv: Services = Depends(get_services)) -> dict:
    ports = sv.ports
    return json.loads(encode_beacon(Beacon(sv.db.server_id, SERVER_NAME, arrival_host(request), ports["http"],
                                           ports["frames"], ports["tcp"])))
