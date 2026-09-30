"""Run the simulator: python -m duoware.sim [--scenario PATH]   (world + scripted cars + phone + car servers)."""

import argparse
import asyncio
import logging
from pathlib import Path

import numpy as np

from duoware.clock import SystemClock
from duoware.settings import load_settings
from duoware.sim.car_server import CarServer
from duoware.sim.phone import SimPhone
from duoware.sim.scenario import load_scenario
from duoware.sim.script import ScriptDriver
from duoware.sim.timing import set_timer_resolution
from duoware.sim.world import SimWorld

STATUS_EVERY_S = 10          # how often the console prints a one-line summary


async def amain(scenario_path: Path | None) -> None:
    sc = load_scenario(scenario_path)
    clock = SystemClock()
    rng = np.random.default_rng(0)
    footprints = {c.name: c.footprint_mm for c in load_settings().cars}
    world = SimWorld(sc, clock, footprints)
    servers = [CarServer(c, world.physics[c.name], clock, rng) for c in sc.car]
    for s in servers:
        await s.start()
    script = ScriptDriver(sc, world, clock)
    phone = SimPhone(sc, world, clock, rng=rng)
    world.start()
    script.start()
    phone.start()
    print("DUO-WARE 2 simulator running (Ctrl-C to stop)")
    print("  cars:  " + ", ".join(f"{s.cfg.name} on tcp://127.0.0.1:{s.port}" for s in servers))
    print(f"  phone: looking for the server ({'beacon' if sc.server.discovery else 'configured host'}), "
          f"{sc.camera.fps:g} fps, link {sc.phone_model.link_mode}")
    try:
        while True:
            await asyncio.sleep(STATUS_EVERY_S)
            state = f"session {phone.session.sid}" if phone.session else "not connected"
            poses = ", ".join(f"{n} ({p.x:.0f}, {p.y:.0f})" for n, p in world.physics.items())
            print(f"  {state}; frames sent {len(phone.truth_log)}, dropped {len(phone.dropped_seqs)}; {poses}")
    finally:
        await phone.stop()
        await script.stop()
        await world.stop()
        for s in servers:
            await s.stop()


def main() -> None:
    p = argparse.ArgumentParser(prog="duoware.sim", description="DUO-WARE 2 simulator")
    p.add_argument("--scenario", type=Path, default=None, help="scenario file (default: config/sim.toml)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    set_timer_resolution()
    try:
        asyncio.run(amain(args.scenario))
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
