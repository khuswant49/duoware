"""Shared fixtures. Every test gets its own data directory and a fake clock."""

import pytest

from duoware.clock import FakeClock
from duoware.ingest.overrides import CameraOverrides
from duoware.registry.service import TagRegistry
from duoware.safety import SafetyState
from duoware.settings import load_settings
from duoware.store.db import StateDb
from duoware.store.events import EventLog


class FakeMover:
    """A motion provider whose moving cars the test sets."""

    def __init__(self) -> None:
        self.moving: list[str] = []

    def moving_cars(self) -> list[str]:
        return list(self.moving)


class Env:
    """The step 3-4 building blocks wired together."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.settings = load_settings(data_dir=tmp_path / "data")
        self.clock = FakeClock(start_ns=1_000_000_000)
        self.open()

    def open(self) -> None:
        self.db = StateDb(self.settings.data_dir / "state.db")
        self.events = EventLog(self.settings.data_dir / "events.db", self.clock)
        self.mover = FakeMover()
        self.safety = SafetyState(self.clock, self.events, self.mover)
        self.registry = TagRegistry(self.db, self.events, self.settings, self.safety, self.clock)
        self.overrides = CameraOverrides(self.db, self.events, self.clock)

    def close(self) -> None:
        self.events.close()
        self.db.close()

    def reopen(self) -> None:
        self.close()
        self.open()


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


# ---------------------------------------------------------------------------- helpers shared by ingest/API tests

import dataclasses

from duoware.protocol.phone import CameraCaps, CpuInfo, Hello, Link, WifiInfo


def make_hello(device_id="dev-1", token=None, pair_code=None, mode="wireless", model="test phone") -> Hello:
    caps = CameraCaps("0", "FULL", ("MANUAL_SENSOR",), "REALTIME", (10000, 500000000), (100, 6400), ((15, 30),),
                      ((1280, 720, 30.0),), False, None, None, None, None)
    wifi = WifiInfo(5.0, -50, 800) if mode == "wireless" else None
    return Hello(device_id, token, pair_code, "0.0.1", model, "13", 33, Link(mode, "wlan0", "192.168.1.23", wifi),
                 CpuInfo(8, (2000000,) * 8), caps)


def patched_settings(settings, **ports):
    """Copy of `settings` with ports replaced: http_port, frames_port, beacon_port, tcp_port, beacon_interval_s."""
    srv = settings.server
    udp = dataclasses.replace(srv.udp, **{k: v for k, v in {"frames_port": ports.get("frames_port"),
                                                            "beacon_port": ports.get("beacon_port"),
                                                            "beacon_interval_s": ports.get("beacon_interval_s")}.items()
                                          if v is not None})
    http = dataclasses.replace(srv.http, port=ports["http_port"]) if "http_port" in ports else srv.http
    tcp = dataclasses.replace(srv.tcp, frames_port=ports["tcp_port"]) if "tcp_port" in ports else srv.tcp
    return dataclasses.replace(settings, server=dataclasses.replace(srv, udp=udp, http=http, tcp=tcp))


# ---------------------------------------------------------------------------- API fixtures

from fastapi.testclient import TestClient

from duoware.api.deps import client_host
from duoware.app import create_app
from duoware.settings import load_settings


def make_app(tmp_path, local=True, dashboard_dir=None, allow_remote=False):
    """App on a temp data dir, free UDP ports, no beacon. TestClient's host is "testclient" (a REMOTE address for
    the guards); `local=True` overrides `client_host` so ordinary tests act like the local dashboard."""
    st = patched_settings(load_settings(data_dir=tmp_path / "data"), frames_port=0, beacon_port=0, beacon_interval_s=3600)
    if allow_remote:
        acc = dataclasses.replace(st.server.access, allow_remote_dashboard=True)
        st = dataclasses.replace(st, server=dataclasses.replace(st.server, access=acc))
    app = create_app(st, mode="sim", interfaces_fn=lambda: [], dashboard_dir=dashboard_dir or tmp_path / "no-dist")
    if local:
        app.dependency_overrides[client_host] = lambda: "127.0.0.1"
    return app


@pytest.fixture
def api(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as c:
        c.sv = app.state.get_services()
        yield c
