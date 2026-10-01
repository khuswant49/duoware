"""Builds every server component in one place and owns their start/stop (CLAUDE.md architecture map)."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import WebSocket

from duoware.broadcaster import Broadcaster
from duoware.ingest.adb import AdbReverse
from duoware.clock import Clock, SystemClock
from duoware.ingest.beacon import BeaconSender, interfaces
from duoware.ingest.camera_settings import build_camera_settings
from duoware.ingest.overrides import CameraOverrides
from duoware.ingest.frames import FrameIngest
from duoware.ingest.sessions import PhoneSessions
from duoware.ingest.tcp import FramesTcpServer
from duoware.ingest.udp import FramesEndpoint
from duoware.monitor import LoopLagMonitor
from duoware.layout.service import LayoutService
from duoware.localization.floor_tags import FloorTags
from duoware.localization.world import WorldModel
from duoware.protocol.phone_session import encode_settings
from duoware.registry.presets import VenuePresets
from duoware.registry.service import TagRegistry
from duoware.safety import SafetyState
from duoware.settings import Settings
from duoware.store.db import StateDb
from duoware.store.events import EventLog

log = logging.getLogger(__name__)

SESSION_TICK_S = 1.0          # how often session bookkeeping runs (sync ok/lost events, 60 s status samples)


@dataclass
class Preview:
    cap_ns: int
    w: int
    h: int
    jpeg: bytes
    received_ns: int


@dataclass
class Services:
    settings: Settings
    clock: Clock
    mode: str
    db: StateDb
    events: EventLog
    safety: SafetyState
    registry: TagRegistry
    overrides: CameraOverrides
    floor: FloorTags
    sessions: PhoneSessions
    world: WorldModel
    layout: LayoutService
    presets: VenuePresets
    ingest: FrameIngest
    endpoint: FramesEndpoint
    tcp_server: FramesTcpServer
    beacon: BeaconSender | None
    monitor: LoopLagMonitor
    broadcaster: Broadcaster = field(init=False)
    adb: AdbReverse | None = None          # hardware mode with wired.adb_reverse only (the simulator never runs adb)
    ports: dict[str, int] = field(default_factory=dict)       # http / frames / tcp / beacon, as actually bound
    phone_sockets: dict[str, WebSocket] = field(default_factory=dict)
    previews: dict[int, Preview] = field(default_factory=dict)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _transport: asyncio.DatagramTransport | None = None

    @staticmethod
    def build(settings: Settings, clock: Clock | None = None, mode: str = "hardware",
              interfaces_fn: Callable[[], list[tuple[str, str]]] | None = None) -> "Services":
        clock = clock or SystemClock()
        db = StateDb(settings.data_dir / "state.db")
        events = EventLog(settings.data_dir / "events.db", clock)
        safety = SafetyState(clock, events)
        registry = TagRegistry(db, events, settings, safety, clock)
        overrides = CameraOverrides(db, events, clock)
        floor = FloorTags(registry)
        sessions = PhoneSessions(db, settings, events, clock)
        world = WorldModel(settings, registry, floor, clock, events, sessions)
        layout = LayoutService(db, registry, world, settings, safety, events, clock)
        presets = VenuePresets(db, registry, overrides, events, settings, safety, clock, layout)
        ingest = FrameIngest(sessions, world, clock, settings.tuning.clock_sync)
        endpoint = FramesEndpoint(sessions, world, clock, settings.tuning.clock_sync, ingest)
        tcp_server = FramesTcpServer(sessions, ingest, clock)
        ports = {"http": settings.server.http.port, "frames": settings.server.udp.frames_port,
                 "tcp": settings.server.tcp.frames_port, "beacon": settings.server.udp.beacon_port}
        beacon = BeaconSender(settings, db.server_id, interfaces_fn or interfaces,
                              lambda: (ports["http"], ports["frames"], ports["tcp"]))
        monitor = LoopLagMonitor(settings.server.monitor, clock, events)
        sv = Services(settings, clock, mode, db, events, safety, registry, overrides, floor, sessions, world, layout,
                      presets, ingest, endpoint, tcp_server, beacon, monitor, ports=ports)
        sv.broadcaster = Broadcaster(sv)
        if mode == "hardware" and settings.server.wired.adb_reverse:
            sv.adb = AdbReverse(settings, lambda: (sv.ports["http"], sv.ports["tcp"]), events, clock)
        overrides.on_change(lambda _new: sv.push_settings())
        registry.on_change(sv._on_registry_change)
        return sv

    # ------------------------------------------------------------------------------ phone settings

    def settings_for_phones(self):
        return build_camera_settings(self.settings, self.overrides.get(), self.registry)

    def push_settings(self) -> None:
        """Sends a new `settings` message to every live phone (overrides or the registry's car tags changed)."""
        if not self.phone_sockets:
            return
        text = encode_settings(self.settings_for_phones())
        for ws in list(self.phone_sockets.values()):
            try:
                asyncio.get_running_loop().create_task(self._send_quiet(ws, text))
            except RuntimeError:
                pass

    @staticmethod
    async def _send_quiet(ws: WebSocket, text: str) -> None:
        try:
            await ws.send_text(text)
        except Exception:
            pass

    def _on_registry_change(self, old, new) -> None:
        if [t.id for t in old.car_tags()] != [t.id for t in new.car_tags()]:
            self.push_settings()

    # ------------------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self.endpoint, local_addr=("0.0.0.0", self.settings.server.udp.frames_port))
        self.ports["frames"] = self._transport.get_extra_info("sockname")[1]
        self.ports["tcp"] = await self.tcp_server.start("0.0.0.0", self.settings.server.tcp.frames_port)
        if self.beacon is not None:
            self.beacon.start()
        self._tasks.append(loop.create_task(self._session_ticker()))
        self.broadcaster.start()
        self.monitor.start()
        if self.adb is not None:
            self.adb.start()
        self.events.log("system", key="server", value="started", facts={"mode": self.mode, "ports": dict(self.ports)},
                        reason="server started")

    async def _session_ticker(self) -> None:
        try:
            while True:
                self.sessions.tick()
                await asyncio.sleep(SESSION_TICK_S)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self.events.log("system", key="server", value="stopped", reason="server stopped")
        await self.monitor.stop()
        if self.adb is not None:
            await self.adb.stop()
        await self.broadcaster.stop()
        if self.beacon is not None:
            await self.beacon.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for ws in list(self.phone_sockets.values()):
            try:
                await ws.close(1001)
            except Exception:
                pass
        await self.tcp_server.stop()
        self.ingest.stop()
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self.events.close()
        self.db.close()
