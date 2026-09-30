"""The event log (PROTOCOL.md §8, DECISIONS.md D23): SQLite `events.db`, written by one background thread from
a queue so the control loop never waits on disk. Measured `facts` stay separate from the rule `reason`."""

import asyncio
import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duoware.clock import Clock

log = logging.getLogger(__name__)

WRITE_BATCH = 256            # most events committed in one transaction by the writer thread
PAUSE_POLL_S = 0.005         # test hook: how often a paused writer re-checks

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS events ("
    "id INTEGER PRIMARY KEY, wall_ms INTEGER NOT NULL, mono_ms INTEGER NOT NULL, type TEXT NOT NULL, car TEXT, "
    "operator TEXT, key TEXT, value TEXT, prev TEXT, facts TEXT NOT NULL, reason TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS events_car ON events(car, id)",
    "CREATE INDEX IF NOT EXISTS events_type ON events(type, id)",
)


def clean(v: Any) -> Any:
    """JSON-safe, compact values (round floats, drop numpy types). Ported from DUO-WARE 1 event_log._clean."""
    if isinstance(v, float):
        return round(v, 2) if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [clean(x) for x in v]
    if hasattr(v, "item"):                                   # numpy scalar
        return clean(v.item())
    if v is None or isinstance(v, (str, int, bool)):
        return v
    return str(v)


def as_text(v: Any) -> str | None:
    """`key`/`value`/`prev` columns hold text."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(clean(v), separators=(",", ":"))
    return str(clean(v))


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: int
    wall_ms: int
    mono_ms: int
    type: str
    car: str | None
    operator: str | None
    key: str | None
    value: str | None
    prev: str | None
    facts: dict
    reason: str

    def to_dict(self) -> dict:
        return {"id": self.id, "wall_ms": self.wall_ms, "mono_ms": self.mono_ms, "type": self.type, "car": self.car,
                "operator": self.operator, "key": self.key, "value": self.value, "prev": self.prev,
                "facts": self.facts, "reason": self.reason}


@dataclass
class _Flush:
    done: threading.Event = field(default_factory=threading.Event)


class EventLog:
    def __init__(self, path: Path, clock: Clock) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._clock = clock
        self._q: queue.Queue[EventRecord | _Flush | None] = queue.Queue()
        self._id_lock = threading.Lock()
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._subs_lock = threading.Lock()
        self._paused = threading.Event()             # test hook: set() holds the writer back
        self._wconn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._wconn.execute("PRAGMA journal_mode=WAL")
        for statement in _SCHEMA:
            self._wconn.execute(statement)
        self._rconn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._rlock = threading.Lock()
        self._last_id = self._wconn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        self._closed = False
        self._thread = threading.Thread(target=self._writer, name="event-log-writer", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------------------ writing

    def log(self, type: str, *, car: str | None = None, operator: str | None = None, key: str | None = None,
            value: Any = None, prev: Any = None, facts: dict | None = None, reason: str = "") -> EventRecord:
        """Returns at once with the record (its `id` is final); the disk write happens on the writer thread."""
        with self._id_lock:
            self._last_id += 1
            rec = EventRecord(self._last_id, self._clock.wall_ms(), self._clock.mono_ns() // 1_000_000, type, car,
                              operator, key, as_text(value), as_text(prev), clean(facts or {}), reason or "")
            self._q.put(rec)
        self._publish(rec)
        return rec

    @property
    def last_id(self) -> int:
        with self._id_lock:
            return self._last_id

    def _writer(self) -> None:
        while True:
            item = self._q.get()
            while self._paused.is_set():
                time.sleep(PAUSE_POLL_S)
            batch: list[EventRecord] = []
            flushes: list[_Flush] = []
            stop = False
            while True:
                if isinstance(item, EventRecord):
                    batch.append(item)
                elif isinstance(item, _Flush):
                    flushes.append(item)
                else:
                    stop = True
                if len(batch) >= WRITE_BATCH or stop:
                    break
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
            self._write(batch)
            for f in flushes:
                f.done.set()
            if stop:
                return

    def _write(self, batch: list[EventRecord]) -> None:
        if not batch:
            return
        rows = [(r.id, r.wall_ms, r.mono_ms, r.type, r.car, r.operator, r.key, r.value, r.prev,
                 json.dumps(r.facts, separators=(",", ":")), r.reason) for r in batch]
        try:
            self._wconn.execute("BEGIN")
            self._wconn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._wconn.execute("COMMIT")
        except sqlite3.Error:
            log.exception("event log write failed (%d events lost)", len(batch))
            try:
                self._wconn.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    def flush(self, timeout_s: float = 10.0) -> None:
        if self._closed:
            return
        f = _Flush()
        self._q.put(f)
        f.done.wait(timeout_s)

    # ------------------------------------------------------------------------------ reading

    def query(self, since_id: int = 0, limit: int = 200, car: str | None = None,
              type: str | None = None) -> list[EventRecord]:
        """The newest `limit` events with `id > since_id`, newest first."""
        self.flush()
        sql, params = "SELECT * FROM events WHERE id > ?", [since_id]
        if car is not None:
            sql, params = sql + " AND car = ?", params + [car]
        if type is not None:
            sql, params = sql + " AND type = ?", params + [type]
        sql += " ORDER BY id DESC LIMIT ?"
        with self._rlock:
            rows = self._rconn.execute(sql, params + [limit]).fetchall()
        return [EventRecord(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], json.loads(r[9]), r[10])
                for r in rows]

    # ------------------------------------------------------------------------------ live feed

    def subscribe(self) -> asyncio.Queue:
        """Call from the event loop that will consume the queue (records arrive via call_soon_threadsafe)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._subs_lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._subs_lock:
            self._subs = [(lp, s) for lp, s in self._subs if s is not q]

    def _publish(self, rec: EventRecord) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for loop, q in subs:
            def put(q: asyncio.Queue = q) -> None:
                if not q.full():
                    q.put_nowait(rec)
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                pass                                        # that loop is closed

    # ------------------------------------------------------------------------------ test hooks / close

    def pause_writer(self) -> None:
        self._paused.set()

    def resume_writer(self) -> None:
        self._paused.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._paused.clear()
        self.flush()
        self._closed = True
        self._q.put(None)
        self._thread.join(5)
        self._wconn.close()
        with self._rlock:
            self._rconn.close()
