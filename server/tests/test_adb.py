"""M2 step 4 / B4: the adb-reverse runner with a fake adb (tests/fake_adb.py). No real device is touched."""

import asyncio
import statistics
import sys
import time
from pathlib import Path

import pytest
from conftest import make_app

from duoware.ingest import adb as adb_module
from duoware.ingest.adb import AdbReverse, find_adb, parse_devices

FAKE = [sys.executable, str(Path(__file__).with_name("fake_adb.py"))]
PORTS = (8000, 47802)


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    log = tmp_path / "adb.log"
    monkeypatch.setenv("FAKE_ADB_LOG", str(log))
    monkeypatch.setenv("FAKE_ADB_DEVICES", "PHONE1:device,PHONE2:unauthorized,PHONE3:offline")
    monkeypatch.delenv("FAKE_ADB_HANG", raising=False)
    monkeypatch.delenv("FAKE_ADB_FAIL", raising=False)
    return log


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def runner(env, command=FAKE, timeout_s=5.0) -> AdbReverse:
    return AdbReverse(env.settings, lambda: PORTS, env.events, env.clock, command=command, timeout_s=timeout_s)


def test_parse_devices_keeps_only_ready_devices():
    out = "List of devices attached\nA1\tdevice\nB2\tunauthorized\n\nC3\toffline\nD4\tdevice product:x\n"
    assert parse_devices(out) == ["A1", "D4"]


async def test_reverses_both_ports_on_every_ready_device(env, fake_env):
    r = runner(env)
    assert await r.poll_once() == ["PHONE1"]
    assert calls(fake_env) == ["devices", "-s PHONE1 reverse tcp:8000 tcp:8000", "-s PHONE1 reverse tcp:47802 tcp:47802"]
    events = [e for e in env.events.query(type="system") if e.key == "adb_reverse"]
    assert len(events) == 1 and events[0].value == "PHONE1" and events[0].facts["ports"] == list(PORTS)


async def test_polling_again_is_idempotent_and_logs_the_device_once(env, fake_env):
    r = runner(env)
    for _ in range(3):
        assert await r.poll_once() == ["PHONE1"]
    assert calls(fake_env).count("-s PHONE1 reverse tcp:8000 tcp:8000") == 3
    assert len([e for e in env.events.query(type="system") if e.key == "adb_reverse"]) == 1


async def test_a_failing_reverse_is_not_reported_as_done(env, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_FAIL", "reverse")
    assert await runner(env).poll_once() == []
    assert not [e for e in env.events.query(type="system") if e.key == "adb_reverse"]


async def test_a_missing_adb_is_logged_once(env, caplog):
    r = runner(env, command=[str(Path("Z:/definitely/not/adb.exe"))])
    for _ in range(3):
        assert await r.poll_once() == []
    assert len([m for m in caplog.messages if "adb reverse disabled" in m]) == 1


async def test_no_adb_anywhere_is_logged_once(env, caplog, monkeypatch):
    monkeypatch.setattr(adb_module, "find_adb", lambda configured: None)
    r = AdbReverse(env.settings, lambda: PORTS, env.events, env.clock)
    assert await r.poll_once() == [] and await r.poll_once() == []
    assert len([m for m in caplog.messages if "adb reverse disabled" in m]) == 1


def test_find_adb_prefers_the_configured_path(monkeypatch, tmp_path):
    assert find_adb("C:/tools/adb.exe") == "C:/tools/adb.exe"
    monkeypatch.setattr(adb_module.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert find_adb("") is None
    sdk = tmp_path / adb_module.SDK_ADB
    sdk.parent.mkdir(parents=True)
    sdk.write_text("")
    assert find_adb("") == str(sdk)


async def test_a_hanging_adb_is_killed_without_stalling_the_event_loop(env, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_ADB_HANG", "reverse")
    r = runner(env, timeout_s=1.0)
    lags: list[float] = []
    stop = asyncio.Event()

    async def probe() -> None:                                  # how late does a 10 ms sleep wake up?
        while not stop.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            lags.append((time.perf_counter() - t0 - 0.01) * 1000)

    task = asyncio.get_running_loop().create_task(probe())
    t0 = time.monotonic()
    assert await r.poll_once() == []                            # both reverse calls time out
    elapsed = time.monotonic() - t0
    stop.set()
    await task
    p95 = statistics.quantiles(lags, n=20)[-1]
    print(f"adb hang: poll took {elapsed:.2f} s, loop lag p95 {p95:.1f} ms over {len(lags)} probes")
    assert 1.0 <= elapsed < 5.0                                 # killed at the timeout, not after the 60 s hang
    assert p95 < 20.0


def test_runs_only_in_hardware_mode(tmp_path):
    from fastapi.testclient import TestClient
    app = make_app(tmp_path)                                     # sim mode, as every test app
    with TestClient(app):
        assert app.state.get_services().adb is None
