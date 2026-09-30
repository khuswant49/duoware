"""/ws/phone: the phone session (PROTOCOL.md §4.3-4.6). Always reachable; authenticated by pairing code or token."""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from duoware.ingest.sessions import Opened, Session
from duoware.protocol import ProtocolError
from duoware.protocol.phone import Bench, Hello, Status, parse_preview, parse_ws_message
from duoware.protocol.phone_session import Welcome, encode_error, encode_welcome
from duoware.services import Preview, Services

log = logging.getLogger(__name__)
router = APIRouter()

HELLO_TIMEOUT_S = 5                # PROTOCOL.md §4.3: the phone sends `hello` within 5 s or the server closes with 4000
CLOSE = {"bad_message": 4000, "bad_pair_code": 4001, "bad_token": 4001, "version": 4002, "locked": 4003,
         "replaced": 4004}         # PROTOCOL.md §4.3 error/close table


async def _reject(ws: WebSocket, code: str, message: str) -> None:
    try:
        await ws.send_text(encode_error(code, message))
        await ws.close(CLOSE[code])
    except Exception:
        pass


@router.websocket("/ws/phone")
async def phone_ws(ws: WebSocket) -> None:
    sv: Services = ws.app.state.get_services()
    await ws.accept()
    try:
        text = await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _reject(ws, "bad_message", "No hello within 5 s.")
        return
    except (WebSocketDisconnect, RuntimeError):
        return
    try:
        hello = parse_ws_message(text)
    except ProtocolError as e:
        await _reject(ws, "version" if e.code == "version" else "bad_message",
                      "This server speaks protocol v1." if e.code == "version" else f"Bad hello: {e.detail}")
        return
    if not isinstance(hello, Hello):
        await _reject(ws, "bad_message", "The first message must be hello.")
        return
    result = sv.sessions.open(hello, ws.client.host if ws.client else "")
    if not isinstance(result, Opened):
        await _reject(ws, result.code, result.message)
        return
    sess = result.session
    sv.phone_sockets[sess.sid] = ws
    if result.replaced is not None:
        old = sv.phone_sockets.pop(result.replaced.sid, None)
        if old is not None:
            await _reject(old, "replaced", "A newer session of this device took over.")
    try:
        await ws.send_text(encode_welcome(Welcome(
            sess.cam, sess.sid, result.token, sv.ports["frames"], sv.ports["tcp"], sv.db.server_id,
            sv.settings_for_phones())))
        await _receive_loop(sv, ws, sess)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sv.phone_sockets.pop(sess.sid, None)
        sv.sessions.close(sess.sid)


async def _receive_loop(sv: Services, ws: WebSocket, sess: Session) -> None:
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            return
        if (text := msg.get("text")) is not None:
            try:
                m = parse_ws_message(text)
            except ProtocolError as e:
                log.warning("cam %d: bad message on the session socket: %s", sess.cam, e)
                continue
            if isinstance(m, Status) and m.cam == sess.cam:
                sv.sessions.on_status(sess, m)
            elif isinstance(m, Bench):
                pass                                   # accepted and ignored until M2 stores benchmark runs
        elif (data := msg.get("bytes")) is not None:
            try:
                head, jpeg = parse_preview(data)
            except ProtocolError:
                continue                               # PROTOCOL.md §4.6: bad or oversized previews are dropped
            sv.previews[sess.cam] = Preview(head.cap_ns, head.w, head.h, jpeg, sv.clock.mono_ns())

