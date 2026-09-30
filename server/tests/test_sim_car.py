"""Step 9: simulated car physics and the car line protocol over a real TCP connection (PROTOCOL.md §5)."""

import asyncio
import dataclasses
import math

import numpy as np
import pytest

from duoware.clock import SystemClock
from duoware.protocol.car import IdReply, Boot, CarError, PingReply, StopAck, TtlExpired, parse_car_line
from duoware.sim.car_physics import BRAKE_MS, CarPhysics
from duoware.sim.car_server import CarServer
from duoware.sim.scenario import load_scenario
from duoware.sim.world import SimWorld

SC = load_scenario()
A, B = SC.car_by_name("DUO-A"), SC.car_by_name("DUO-B")
MS = 1_000_000


def run(ph: CarPhysics, seconds: float, t0: int = 0, dt: float = 0.005) -> int:
    t = t0
    for _ in range(round(seconds / dt)):
        t += int(dt * 1e9)
        ph.step(dt, t)
    return t


def at(cfg, **kw):
    return dataclasses.replace(cfg, start=(0.0, 0.0, 0.0), **kw)


def test_below_the_dead_zone_nothing_moves():
    ph = CarPhysics(at(A))
    ph.command(A.dead_zone_pwm - 1, A.dead_zone_pwm - 1, 500, 0)
    run(ph, 0.4)
    assert not ph.is_moving and ph.x == 0 and ph.heading == 0


def test_speed_follows_pwm_with_the_configured_lag():
    ph = CarPhysics(at(A, right_gain=1.0))
    ph.command(255, 255, 500, 0)
    t = run(ph, A.time_constant_ms / 1000)                     # one time constant: 63 % of the way
    assert ph.speed_mm_s == pytest.approx(A.max_speed_mm_s * (1 - math.exp(-1)), rel=0.05)
    run(ph, 0.4, t)
    assert ph.speed_mm_s == pytest.approx(A.max_speed_mm_s, rel=0.03) and abs(ph.heading) < 1e-9 and abs(ph.y) < 1e-6
    half = A.dead_zone_pwm + (255 - A.dead_zone_pwm) // 2
    ph2 = CarPhysics(at(A, right_gain=1.0))
    ph2.command(half, half, 500, 0)
    run(ph2, 0.4)
    assert ph2.speed_mm_s == pytest.approx(A.max_speed_mm_s * 0.5, rel=0.02)


def test_wheel_asymmetry_curves_the_path_to_the_expected_side():
    a = CarPhysics(at(A))                                      # right_gain 0.93: right wheel slower -> turns right (clockwise)
    a.command(200, 200, 500, 0)
    run(a, 0.45)
    assert a.heading > 1 and a.y > 0
    b = CarPhysics(at(B))                                      # right_gain 1.08: right wheel faster -> turns left
    b.command(200, 200, 500, 0)
    run(b, 0.45)
    assert b.heading < -1 and b.y < 0


def test_reverse_and_turning_direction():
    ph = CarPhysics(at(A, right_gain=1.0))
    ph.command(-200, -200, 400, 0)
    run(ph, 0.3)
    assert ph.x < -10
    ph = CarPhysics(at(A, right_gain=1.0))
    ph.command(200, -200, 400, 0)                              # left forward, right backward: clockwise spin
    run(ph, 0.3)
    assert ph.heading > 10


def test_spin_in_place_keeps_the_axle_midpoint_fixed():
    ph = CarPhysics(at(A, right_gain=1.0))
    ph.command(180, -180, 500, 0)
    run(ph, 0.45)
    assert abs(ph.heading) > 20 and math.hypot(ph.x, ph.y) < 1e-6


def test_ttl_expiry_brakes_within_150ms_plus_coast_and_reports_once():
    expired = []
    ph = CarPhysics(at(A, right_gain=1.0), on_ttl_expired=expired.append)
    ph.command(255, 255, 100, 0)
    t = run(ph, 0.09)
    assert expired == []
    v_at_expiry = ph.speed_mm_s
    t = run(ph, 0.03, t)
    assert expired == [1]                                      # exactly once, on expiry
    x_expiry = ph.x
    run(ph, (BRAKE_MS + 100) / 1000, t)
    assert ph.speed_mm_s < 0.05 * v_at_expiry
    assert ph.ttl_expiries == 1
    stop_distance = ph.x - x_expiry
    assert 0 < stop_distance < v_at_expiry * A.time_constant_ms / 1000 * 1.2      # braking beats coasting
    run(ph, 1.0, t)
    assert ph.ttl_expiries == 1


def test_stop_brakes_faster_than_coasting():
    def dist(brake: bool) -> float:
        ph = CarPhysics(at(A, right_gain=1.0))
        ph.command(255, 255, 500, 0)
        t = run(ph, 0.45)
        x0 = ph.x
        if brake:
            ph.stop(t)
        else:
            ph._moving = False                                  # coast: no brake window
        run(ph, 1.0, t)
        return ph.x - x0
    assert dist(True) < 0.6 * dist(False)


def test_a_new_command_replaces_the_current_motion_at_once():
    ph = CarPhysics(at(A, right_gain=1.0))
    ph.command(255, 255, 500, 0)
    t = run(ph, 0.3)
    ph.command(-255, -255, 500, t)
    run(ph, 0.4, t)
    assert ph.speed_mm_s < -0.8 * A.max_speed_mm_s


async def test_world_history_gives_the_true_pose_at_any_instant_and_covered_tags():
    clock = SystemClock()
    w = SimWorld(SC, clock, {"DUO-A": (200.0, 160.0), "DUO-B": (200.0, 160.0)})
    w.physics["DUO-A"].command(200, 200, 500, clock.mono_ns())
    t0 = clock.mono_ns()
    for _ in range(40):
        await asyncio.sleep(0.005)
        w.step(0.005, clock.mono_ns())
    t1 = clock.mono_ns()
    p_mid = w.true_pose("DUO-A", (t0 + t1) // 2)
    assert w.true_pose("DUO-A", t0 - 10**9) == w._poses["DUO-A"][0]
    assert p_mid[0] < w.true_pose("DUO-A", t1)[0]
    assert w.covered_floor_tags(t1) >= {10}                     # DUO-A starts on node 10 and is still near it
    w.physics["DUO-A"].x, w.physics["DUO-A"].y, w.physics["DUO-A"].heading = 300.0, 300.0, 0.0
    w._record(clock.mono_ns())
    assert w.covered_floor_tags(clock.mono_ns()) == {12}                       # only DUO-B (parked on node 12)
    w.physics["DUO-A"].x, w.physics["DUO-A"].y = 380.0 + 90.0, 471.0           # 90 mm ahead of node 6: inside the 200 mm footprint
    w._record(clock.mono_ns())
    assert w.covered_floor_tags(clock.mono_ns()) == {6, 12}
    w.physics["DUO-A"].x = 380.0 + 110.0                                       # 110 mm: beyond half the 200 mm length
    w._record(clock.mono_ns())
    assert w.covered_floor_tags(clock.mono_ns()) == {12}


# ---- the line protocol over TCP ---------------------------------------------------------------------------------


class Conn:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer

    async def line(self, timeout=1.0):
        return parse_car_line((await asyncio.wait_for(self.r.readline(), timeout)).decode())

    async def raw(self, timeout=1.0) -> str:
        return (await asyncio.wait_for(self.r.readline(), timeout)).decode().rstrip("\r\n")

    def send(self, data: bytes | str):
        self.w.write(data if isinstance(data, bytes) else data.encode())


async def connect(cfg=None, fast=False):
    cfg = dataclasses.replace(cfg or A, tcp_port=0, link_delay_ms=(1.0, 0.0) if fast else (cfg or A).link_delay_ms)
    clock = SystemClock()
    world = SimWorld(dataclasses.replace(SC, car=(cfg,)), clock)
    srv = CarServer(cfg, world.physics[cfg.name], clock, np.random.default_rng(0))
    await srv.start()
    world.start()
    r, w = await asyncio.open_connection("127.0.0.1", srv.port)
    return srv, world, Conn(r, w)


async def close(srv, world, conn):
    conn.w.close()
    await world.stop()
    await srv.stop()


async def test_boot_identify_ping_stop():
    srv, world, c = await connect()
    try:
        assert await c.line() == Boot("DUO-A", "0.1.0-sim", "power")
        c.send("?\n")
        idr = await c.line()
        assert isinstance(idr, IdReply) and (idr.name, idr.proto, idr.caps) == ("DUO-A", 1, ("ttl", "led"))
        c.send("P 4242\n")
        assert await c.line() == PingReply(4242)
        c.send("S\n")
        assert await c.line() == StopAck()
        c.send("L 1\r\n")
        c.send("P 1\r\n")                                        # "\r" is ignored
        assert await c.line() == PingReply(1)
        assert srv.led is True
    finally:
        await close(srv, world, c)


async def test_move_then_silence_expires_with_x_1_and_the_car_moved():
    srv, world, c = await connect(fast=True)
    try:
        await c.line()
        c.send("M 255 255 60\n")
        assert await c.line(1.0) == TtlExpired(1)
        await asyncio.sleep(0.4)
        assert world.physics["DUO-A"].x > 5 and not world.physics["DUO-A"].is_moving
        c.send("M 255 255 900\n")                                # ttl above 500 is clamped, not an error
        t0 = asyncio.get_running_loop().time()
        assert await c.line(2.0) == TtlExpired(2)
        assert 0.4 < asyncio.get_running_loop().time() - t0 < 0.7
    finally:
        await close(srv, world, c)


async def test_error_codes_and_every_error_stops_the_car():
    srv, world, c = await connect(fast=True)
    try:
        await c.line()
        cases = [("M 300 0 10\n", "range"), ("M 1 1 0\n", "range"), ("M 1 1\n", "parse"), ("M a b c\n", "parse"),
                 ("P 70000\n", "range"), ("Z\n", "unknown"), ("x" * 32 + "\n", "long"), ("M  1 1 10\n", "parse")]
        for line, code in cases:
            c.send("M 255 255 500\n")
            await asyncio.sleep(0.15)
            assert world.physics["DUO-A"].is_moving
            c.send(line)
            assert await c.line() == CarError(code), line
            await asyncio.sleep(0.5)
            assert not world.physics["DUO-A"].is_moving, line
    finally:
        await close(srv, world, c)


async def test_31_characters_are_fine_32_are_too_long_and_the_line_is_discarded_to_its_newline():
    srv, world, c = await connect(fast=True)
    try:
        await c.line()
        c.send("?" + " " * 30 + "\n")                                 # 31 characters: read as "?" with trailing spaces
        assert await c.line() == CarError("parse")
        c.send("P 1" + " " * 29 + "\n")                                # 32 characters
        assert await c.line() == CarError("long")
        c.send("P 2\n")                                              # the next line is handled normally
        assert await c.line() == PingReply(2)
        c.send("P 3 " + "9" * 40 + "\nP 4\n")                          # the rest of a long line is thrown away
        assert await c.line() == CarError("long")
        assert await c.line() == PingReply(4)
    finally:
        await close(srv, world, c)


async def test_commands_arrive_in_order_after_the_link_delay():
    srv, world, c = await connect()                                 # link_delay_ms = [20, 10]
    try:
        await c.line()
        t0 = asyncio.get_running_loop().time()
        c.send("M 255 255 500\nS\nM -200 -200 500\n")
        await asyncio.sleep(0.6)
        assert world.physics["DUO-A"].x < 0                         # the last command won: the car ended up reversing
        assert await c.line() == StopAck()
    finally:
        await close(srv, world, c)


async def test_a_dropped_connection_stops_the_car():
    srv, world, c = await connect(fast=True)
    await c.line()
    c.send("M 255 255 500\n")
    await asyncio.sleep(0.2)
    assert world.physics["DUO-A"].is_moving
    c.w.close()
    await asyncio.sleep(0.6)
    assert not world.physics["DUO-A"].is_moving
    await world.stop()
    await srv.stop()
