"""Frame recordings for offline diagnosis (DECISIONS.md D42): `python -m duoware --record-frames`.

One JSON line per accepted frame of every camera, written by a background thread from a bounded queue: the ingest
never waits on disk. When the queue is full a line is dropped and counted, never blocking. The first line is a header:

    {"t":"recording","v":1,"server_id":…,"started_wall_ms":…,"settings":{"pose":…,"clock_sync":…}}
    {"recv_ns":…,"wall_ms":…,"cam":1,"transport":"udp","synced":true,"theta_ns":…,"frame":{…as received…}}

`theta_ns` is the clock-sync offset (phone minus server) at receive time, null while unsynced, so a replay can turn
the frame's phone times into server times. Read with `load()`.
"""

import dataclasses
import json
import logging
import queue
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from duoware import PROTOCOL_VERSION
from duoware.clock import Clock
from duoware.settings import Settings

log = logging.getLogger(__name__)

RECORDER_QUEUE_MAX = 10000     # lines waiting for the writer (~3 min at 60 fps); beyond this, drop and count
FLUSH_EVERY_LINES = 200        # flush the file at least this often (and whenever the queue runs empty)
_STOP = object()


class FrameRecorder:
    def __init__(self, directory: Path, settings: Settings, server_id: str, clock: Clock) -> None:
        self.directory, self._settings, self._server_id, self._clock = directory, settings, server_id, clock
        self.path: Path | None = None
        self.dropped = 0
        self.written = 0
        self._q: queue.Queue = queue.Queue(maxsize=RECORDER_QUEUE_MAX)
        self._thread: threading.Thread | None = None

    def start(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock.wall_ms() / 1000))
        self.path = self.directory / f"{stamp}.jsonl"
        n = 1
        while self.path.exists():                               # two starts in the same second
            self.path = self.directory / f"{stamp}-{n}.jsonl"
            n += 1
        tun = self._settings.tuning
        header = {"t": "recording", "v": PROTOCOL_VERSION, "server_id": self._server_id,
                  "started_wall_ms": self._clock.wall_ms(),
                  "settings": {"pose": dataclasses.asdict(tun.pose), "clock_sync": dataclasses.asdict(tun.clock_sync)}}
        f = open(self.path, "w", encoding="utf-8")
        f.write(json.dumps(header) + "\n")
        self._thread = threading.Thread(target=self._run, args=(f,), name="frame-recorder", daemon=True)
        self._thread.start()
        log.info("recording frames to %s", self.path)
        return self.path

    def record(self, recv_ns: int, cam: int, transport: str, synced: bool, theta_ns: int | None, frame: bytes) -> None:
        """Called by the ingest for each accepted frame; never blocks."""
        try:
            self._q.put_nowait((recv_ns, self._clock.wall_ms(), cam, transport, synced, theta_ns, frame))
        except queue.Full:
            self.dropped += 1

    def stop(self) -> None:
        if self._thread is None:
            return
        self._q.put(_STOP)                                      # waits only for room in the queue, never for disk
        self._thread.join()
        self._thread = None
        if self.dropped:
            log.warning("frame recorder dropped %d lines (queue full)", self.dropped)

    def _run(self, f) -> None:
        since_flush = 0
        with f:
            while True:
                try:
                    item = self._q.get(timeout=0.5)
                except queue.Empty:
                    if since_flush:
                        f.flush()
                        since_flush = 0
                    continue
                if item is _STOP:
                    break
                recv_ns, wall_ms, cam, transport, synced, theta_ns, frame = item
                try:
                    obj = json.loads(frame)
                except ValueError:                              # only accepted frames arrive here; be safe anyway
                    continue
                f.write(json.dumps({"recv_ns": recv_ns, "wall_ms": wall_ms, "cam": cam, "transport": transport,
                                    "synced": synced, "theta_ns": theta_ns, "frame": obj}, separators=(",", ":")))
                f.write("\n")
                self.written += 1
                since_flush += 1
                if since_flush >= FLUSH_EVERY_LINES or self._q.empty():
                    f.flush()
                    since_flush = 0


def load(path: Path) -> tuple[dict, Iterator[dict]]:
    """(header, frame lines) of a recording."""
    f = open(path, encoding="utf-8")
    header = json.loads(f.readline())
    if header.get("t") != "recording":
        f.close()
        raise ValueError(f"{path} is not a DUO-WARE frame recording (no header line)")

    def lines() -> Iterator[dict]:
        with f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    return header, lines()
