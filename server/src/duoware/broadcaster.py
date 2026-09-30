"""State broadcaster: one `state` snapshot per tick, shared by all dashboard clients (PROTOCOL.md §6.2).

A client whose socket is still sending the previous snapshot is skipped for this tick, never queued.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fastapi import WebSocket

from duoware import PROTOCOL_VERSION
from duoware.state_builder import build_state

if TYPE_CHECKING:
    from duoware.services import Services

log = logging.getLogger(__name__)

LOG_EVERY_S = 60              # one traceback per minute at most
NS_PER_S = 1_000_000_000


class DashboardClient:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.sending = False
        self.skipped = 0
        self.lock = asyncio.Lock()                # one send at a time on this socket (state snapshot or event)


class Broadcaster:
    def __init__(self, sv: "Services") -> None:
        self.sv = sv
        self.clients: set[DashboardClient] = set()
        self.seq = 0
        self.last_text: str | None = None
        self._task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self.errors = 0                                   # ticks that raised (counted, never fatal)
        self._last_error_log_ns = -LOG_EVERY_S * NS_PER_S

    def _error(self, what: str) -> None:
        """One failing tick must not end the broadcaster (dashboard freeze, node-moved detection stops): count it and
        log the traceback at most once per minute."""
        self.errors += 1
        now = self.sv.clock.mono_ns()
        if now - self._last_error_log_ns >= LOG_EVERY_S * NS_PER_S:
            self._last_error_log_ns = now
            log.exception("state broadcaster: %s failed (%d errors so far); continuing", what, self.errors)

    def tick(self) -> str | None:
        """One snapshot (also advances node-moved detection, PROTOCOL.md §7.5). None if the snapshot failed."""
        now = self.sv.clock.mono_ns()
        try:
            self.sv.layout.tick(now)
        except Exception:
            self._error("layout.tick")
        try:
            text = json.dumps(build_state(self.sv, now, self.seq + 1), separators=(",", ":"), allow_nan=False)
        except Exception:
            self._error("build_state")
            return None
        self.seq += 1
        self.last_text = text
        return text

    async def _send(self, client: DashboardClient, text: str) -> None:
        try:
            async with client.lock:
                await client.ws.send_text(text)
        except Exception:                                # the handler notices the dead socket and cleans up
            pass
        finally:
            client.sending = False

    async def _send_event(self, client: DashboardClient, text: str) -> None:
        """Events are never skipped: they queue on the client's lock in order."""
        try:
            async with client.lock:
                await client.ws.send_text(text)
        except Exception:
            pass

    async def _forward_events(self, q: asyncio.Queue) -> None:
        try:
            while True:
                rec = await q.get()
                text = json.dumps({"v": PROTOCOL_VERSION, "t": "event", "event": rec.to_dict()}, separators=(",", ":"))
                for c in list(self.clients):
                    asyncio.get_running_loop().create_task(self._send_event(c, text))
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        period = 1.0 / self.sv.settings.server.dashboard.state_hz
        try:
            while True:
                text = self.tick()
                for c in list(self.clients) if text is not None else []:
                    if c.sending:
                        c.skipped += 1
                    else:
                        c.sending = True
                        asyncio.get_running_loop().create_task(self._send(c, text))
                await asyncio.sleep(period)
        except asyncio.CancelledError:
            pass

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._events_q = self.sv.events.subscribe()
        self._event_task = loop.create_task(self._forward_events(self._events_q))
        self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        for t in (self._task, self._event_task):
            if t:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)
        self._task = self._event_task = None
        if getattr(self, "_events_q", None) is not None:
            self.sv.events.unsubscribe(self._events_q)
