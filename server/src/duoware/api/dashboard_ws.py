"""/ws/dashboard: read-only live state (PROTOCOL.md §6). Local clients only (DECISIONS.md D17).

The handler owns no background tasks: the broadcaster sends state snapshots and events to every registered
client; the handler only says hello, registers the client and waits for the socket to close (anything the dashboard
sends is ignored).
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from duoware.api.deps import LOCAL_HOSTS
from duoware.broadcaster import DashboardClient
from duoware.services import Services
from duoware.state_builder import hello_msg

router = APIRouter()
CLOSE_POLICY = 1008            # WebSocket "policy violation": remote clients are refused until M8


@router.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket) -> None:
    sv: Services = ws.app.state.get_services()
    host = ws.client.host if ws.client else ""
    if host not in LOCAL_HOSTS and not sv.settings.server.access.allow_remote_dashboard:
        await ws.close(CLOSE_POLICY)
        return
    await ws.accept()
    client = DashboardClient(ws)
    try:
        async with client.lock:
            await ws.send_text(json.dumps(hello_msg(sv), separators=(",", ":")))
            if sv.broadcaster.last_text:
                await ws.send_text(sv.broadcaster.last_text)
        sv.broadcaster.clients.add(client)
        while True:
            if (await ws.receive())["type"] == "websocket.disconnect":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sv.broadcaster.clients.discard(client)
