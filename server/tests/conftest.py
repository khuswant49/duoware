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
