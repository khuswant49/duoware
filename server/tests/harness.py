"""Test harness: the real server app under uvicorn on free ports, plus the simulator pieces, all in the test's event
loop (used by the simulator-phone tests and the end-to-end test)."""

import asyncio
import dataclasses
import socket
from pathlib import Path

import numpy as np
import uvicorn
from conftest import patched_settings

from duoware.app import create_app
from duoware.clock import SystemClock
from duoware.settings import load_settings
from duoware.sim.car_server import CarServer
from duoware.sim.phone import SimPhone
from duoware.sim.scenario import Scenario, load_scenario
from duoware.sim.script import ScriptDriver
from duoware.sim.world import SimWorld

PAIR_CODE = "TEST-42"


def free_tcp_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerHarness:
    def __init__(self, data_dir: Path, allow_remote: bool = False) -> None:
        self.http_port = free_tcp_port()
        st = patched_settings(load_settings(data_dir=data_dir), http_port=self.http_port, frames_port=0, beacon_port=0,
                              beacon_interval_s=3600)
        st = dataclasses.replace(st, env={"DUO_PAIR_CODE": PAIR_CODE})
        self.settings = st
        self.app = create_app(st, mode="sim", interfaces_fn=lambda: [], dashboard_dir=data_dir / "no-dist")
        self.sv = self.app.state.get_services()
        self.sv.ports["http"] = self.http_port
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.http_port, log_level="warning",
                                                    ws_ping_interval=5, ws_ping_timeout=10))
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> "ServerHarness":
        self.task = asyncio.get_running_loop().create_task(self.server.serve())
        for _ in range(200):
            if self.server.started:
                return self
            await asyncio.sleep(0.05)
        raise RuntimeError("the server did not start")

    async def __aexit__(self, *exc) -> None:
        self.server.should_exit = True
        if self.task:
            await asyncio.wait_for(self.task, 15)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"


PARKED = {"DUO-A": (200.0, 250.0, 7.0), "DUO-B": (150.0, 650.0, 8.0)}     # between the nodes: no floor tag is covered


def make_scenario(http_port: int, parked: bool = False, **server_overrides) -> Scenario:
    """config/sim.toml pointed at the harness: no discovery, the test pair code, free car ports. `parked` starts the
    cars between the nodes (by default DUO-A sits on the origin tag and DUO-B on node 12, hiding them)."""
    sc = load_scenario()
    if parked:
        sc = dataclasses.replace(sc, car=tuple(dataclasses.replace(c, start=PARKED[c.name]) for c in sc.car))
    srv = dataclasses.replace(sc.server, discovery=False, host="127.0.0.1", http_port=http_port, pair_code=PAIR_CODE,
                              **server_overrides)
    return dataclasses.replace(sc, server=srv, car=tuple(dataclasses.replace(c, tcp_port=0) for c in sc.car))


class SimRig:
    """World + scripted cars + car servers + phone, started in the current loop."""

    def __init__(self, scenario: Scenario, script: bool = True, seed: int = 0) -> None:
        self.sc = scenario
        self.clock = SystemClock()
        self.rng = np.random.default_rng(seed)
        footprints = {c.name: c.footprint_mm for c in load_settings().cars}
        self.world = SimWorld(scenario, self.clock, footprints)
        self.cars = [CarServer(c, self.world.physics[c.name], self.clock, self.rng) for c in scenario.car]
        self.script = ScriptDriver(scenario, self.world, self.clock)
        self.phone = SimPhone(scenario, self.world, self.clock, rng=self.rng)
        self.use_script = script

    async def start(self) -> "SimRig":
        for c in self.cars:
            await c.start()
        self.world.start()
        if self.use_script:
            self.script.start()
        self.phone.start()
        return self

    async def stop(self) -> None:
        await self.phone.stop()
        await self.script.stop()
        await self.world.stop()
        for c in self.cars:
            await c.stop()
