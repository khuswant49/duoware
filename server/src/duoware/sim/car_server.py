"""One simulated car on a localhost TCP port, speaking the car line protocol of PROTOCOL.md §5 exactly (the server's
`--sim` car transport connects here from M3; M1 tests connect directly)."""

import asyncio
import logging
import time

import numpy as np

from duoware.clock import Clock
from duoware.protocol import ProtocolError
from duoware.protocol.car import (
    MAX_LINE, Identify, Led, Move, Ping, Stop, encode_boot, encode_car_error, encode_id, encode_ping_reply,
    encode_stop_ack, encode_ttl_expired, parse_command_line,
)
from duoware.sim.car_physics import CarPhysics
from duoware.sim.scenario import SimCarCfg

log = logging.getLogger(__name__)

FIRMWARE = "0.1.0-sim"           # sim firmware version reported in ID and BOOT
PROTO = 1                        # PROTOCOL.md §5.3
CAPS = ("ttl", "led")            # PROTOCOL.md §5.3
NS_PER_MS = 1_000_000


class CarServer:
    def __init__(self, cfg: SimCarCfg, physics: CarPhysics, clock: Clock, rng: np.random.Generator) -> None:
        self.cfg, self.physics, self.clock, self.rng = cfg, physics, clock, rng
        self.led = False
        self.port = cfg.tcp_port
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._last_effect_ns = 0
        self._effects: asyncio.Queue = asyncio.Queue()
        self._executor: asyncio.Task | None = None
        self._boot = time.monotonic()
        physics._on_expired = self._on_expired

    # ------------------------------------------------------------------------------ lifecycle

    async def start(self, host: str = "127.0.0.1") -> None:
        self._server = await asyncio.start_server(self._handle, host, self.cfg.tcp_port)
        self.port = self._server.sockets[0].getsockname()[1]
        self._executor = asyncio.get_running_loop().create_task(self._run_effects())

    async def stop(self) -> None:
        if self._executor:
            self._executor.cancel()
            await asyncio.gather(self._executor, return_exceptions=True)
            self._executor = None
        if self._server:
            self._server.close()
            for w in list(self._writers):
                w.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------------------ protocol

    def _send(self, data: bytes) -> None:
        for w in list(self._writers):
            try:
                w.write(data)
            except Exception:
                self._writers.discard(w)

    def _on_expired(self, count: int) -> None:
        self._send(encode_ttl_expired(count))

    def _apply_later(self, fn) -> None:
        """PROTOCOL.md §5.4 / sim.toml link_delay_ms: a command reaches the car after mean +- uniform, in arrival
        order (one executor applies them one after the other)."""
        mean, spread = self.cfg.link_delay_ms
        at = self.clock.mono_ns() + int((mean + self.rng.uniform(-spread, spread)) * NS_PER_MS)
        self._last_effect_ns = max(self._last_effect_ns, at)
        self._effects.put_nowait((self._last_effect_ns, fn))

    async def _run_effects(self) -> None:
        try:
            while True:
                at, fn = await self._effects.get()
                wait = (at - self.clock.mono_ns()) / 1e9
                if wait > 0:
                    await asyncio.sleep(wait)
                fn()
        except asyncio.CancelledError:
            pass

    def _handle_line(self, line: bytes, writer: asyncio.StreamWriter, too_long: bool) -> None:
        try:
            if too_long:
                raise ProtocolError("long")
            cmd = parse_command_line(line)
        except ProtocolError as e:
            self.physics.stop(self.clock.mono_ns())
            writer.write(encode_car_error(e.code))
            return
        if isinstance(cmd, Move):
            self._apply_later(lambda: self.physics.command(cmd.left, cmd.right, cmd.ttl_ms, self.clock.mono_ns()))
        elif isinstance(cmd, Stop):
            self._apply_later(lambda: self.physics.stop(self.clock.mono_ns()))
            writer.write(encode_stop_ack())
        elif isinstance(cmd, Ping):
            writer.write(encode_ping_reply(cmd.n))
        elif isinstance(cmd, Identify):
            writer.write(encode_id(self.cfg.name, FIRMWARE, PROTO, CAPS, int(time.monotonic() - self._boot)))
        elif isinstance(cmd, Led):
            self.led = cmd.on

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        writer.write(encode_boot(self.cfg.name, FIRMWARE, "power"))
        buf = bytearray()
        too_long = False
        try:
            while data := await reader.read(256):
                for b in data:
                    if b == 0x0A:                                   # "\n"
                        if buf or too_long:
                            self._handle_line(bytes(buf), writer, too_long)
                        buf.clear()
                        too_long = False
                    elif b == 0x0D:                                 # "\r" is ignored (PROTOCOL.md §5.1)
                        continue
                    elif not too_long:
                        buf.append(b)
                        if len(buf) > MAX_LINE:                     # discard up to the next "\n", then reply E long
                            too_long = True
                            buf.clear()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._writers.discard(writer)
            self.physics.stop(self.clock.mono_ns())                 # PROTOCOL.md §5.4: a disconnect stops the car
            writer.close()
