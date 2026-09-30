"""Phone pairing endpoints (PROTOCOL.md §7.2, §4.3)."""

from fastapi import APIRouter, Depends

from duoware.api.control import OPERATOR
from duoware.api.deps import dashboard_guard, get_services
from duoware.protocol.phone_session import encode_error
from duoware.services import Services

CLOSE_BAD_TOKEN = 4001            # PROTOCOL.md §4.3 close code for failed authentication

router = APIRouter(dependencies=[Depends(dashboard_guard)])


@router.get("/api/pairing")
async def get_pairing(sv: Services = Depends(get_services)) -> dict:
    return {"pair_code": sv.sessions.pair_code, "devices": sv.sessions.devices()}


@router.delete("/api/pairing/{device_id}")
async def revoke(device_id: str, sv: Services = Depends(get_services)) -> dict:
    sess = sv.sessions.revoke(device_id, OPERATOR)
    if sess is not None:
        ws = sv.phone_sockets.get(sess.sid)
        sv.sessions.close(sess.sid, "pairing revoked by the operator")
        if ws is not None:
            try:
                await ws.send_text(encode_error("bad_token", "The pairing of this phone was revoked."))
                await ws.close(CLOSE_BAD_TOKEN)
            except Exception:
                pass
    return {"device_id": device_id, "revoked": True}
