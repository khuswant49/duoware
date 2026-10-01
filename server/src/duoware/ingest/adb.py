"""`adb reverse` runner for connection mode `wired_adb` (PROTOCOL.md §4.1, DECISIONS.md D33).

Every `wired.adb_poll_s` it lists the attached Android devices and, for each one in state `device`, maps the phone's
127.0.0.1:<http port> and 127.0.0.1:<TCP frames port> to this laptop, so the phone's WIRED fallback needs no manual
command. `adb reverse` is idempotent, and re-running it every poll restores the mappings after a cable is re-plugged.

Each `adb` call runs in a worker thread with a timeout (`subprocess.run` kills it when the timeout passes), so a hung
`adb` never stalls the event loop. A missing `adb` is logged once. Services starts this only in hardware mode: the
simulator never touches hardware (DECISIONS.md D14).
"""

import asyncio
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from duoware.clock import Clock
from duoware.settings import Settings
from duoware.store.events import EventLog

log = logging.getLogger(__name__)

ADB_TIMEOUT_S = 5.0     # one adb call; `adb devices` answers in well under a second unless the adb server is stuck
SDK_ADB = Path("Android") / "Sdk" / "platform-tools" / "adb.exe"     # under %LOCALAPPDATA% (Android Studio default)


def find_adb(configured: str) -> str | None:
    """`wired.adb_path`, else `adb` on PATH, else the Android Studio SDK location; None when there is none."""
    if configured:
        return configured
    found = shutil.which("adb")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / SDK_ADB).exists():
        return str(Path(local) / SDK_ADB)
    return None


def parse_devices(output: str) -> list[str]:
    """Serials in state `device` from `adb devices` (unauthorized and offline devices cannot be reversed)."""
    serials = []
    for line in output.splitlines()[1:]:                        # the first line is "List of devices attached"
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


class AdbReverse:
    def __init__(self, settings: Settings, ports: Callable[[], tuple[int, int]], events: EventLog, clock: Clock,
                 command: Sequence[str] | None = None, timeout_s: float = ADB_TIMEOUT_S) -> None:
        """`ports()` gives the bound (http, tcp frames) ports. `command` replaces the adb executable (tests run a fake
        adb as `[sys.executable, "fake_adb.py"]`)."""
        self.poll_s = settings.server.wired.adb_poll_s
        self._ports, self._events, self._clock = ports, events, clock
        self._timeout_s = timeout_s
        adb = find_adb(settings.server.wired.adb_path)
        self._command: list[str] | None = list(command) if command is not None else ([adb] if adb else None)
        self._missing_logged = False
        self._reversed: set[str] = set()                        # devices reversed at least once (for the event)
        self._task: asyncio.Task | None = None
        self.polls = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:                                   # never let one bad poll end the runner
                log.exception("adb reverse: poll failed")
            await asyncio.sleep(self.poll_s)

    async def _run(self, *args: str) -> str | None:
        """stdout of one adb call, or None (missing adb, error exit, or killed after the timeout)."""
        if self._command is None:
            self._log_missing("no adb found (wired.adb_path, PATH, %LOCALAPPDATA%\\Android\\Sdk\\platform-tools)")
            return None
        cmd = [*self._command, *args]
        try:
            done = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True,
                                           timeout=self._timeout_s)
        except FileNotFoundError:
            self._log_missing(f"{self._command[0]} not found")
            return None
        except subprocess.TimeoutExpired:
            log.warning("adb reverse: %s timed out after %.0f s and was killed", " ".join(args), self._timeout_s)
            return None
        if done.returncode != 0:
            log.info("adb reverse: %s failed (%d): %s", " ".join(args), done.returncode, done.stderr.strip())
            return None
        return done.stdout

    def _log_missing(self, why: str) -> None:
        if not self._missing_logged:
            self._missing_logged = True
            log.warning("adb reverse disabled: %s. The wired_adb fallback will not work.", why)

    async def poll_once(self) -> list[str]:
        """One round. Returns the serials whose mappings were (re)applied."""
        self.polls += 1
        out = await self._run("devices")
        if out is None:
            return []
        http, tcp = self._ports()
        done = []
        for serial in parse_devices(out):
            ok = all([await self._run("-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}") is not None
                      for port in (http, tcp)])
            if not ok:
                continue
            done.append(serial)
            if serial not in self._reversed:
                self._reversed.add(serial)
                self._events.log("system", key="adb_reverse", value=serial,
                                 facts={"serial": serial, "ports": [http, tcp]},
                                 reason="adb reverse mapped the phone's local ports to this laptop (wired_adb)")
        return done
