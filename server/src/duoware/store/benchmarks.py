"""Benchmark runs from the phones (PROTOCOL.md §4.7, §7.2 `GET /api/cameras/{cam}/benchmarks`, DECISIONS.md D36).

Stored as received in state.db (schema 2, table `benchmarks`), newest `BENCH_KEEP_PER_CAM` per camera.
"""

import json

from duoware import PROTOCOL_VERSION
from duoware.clock import Clock
from duoware.protocol.phone import Bench
from duoware.store.db import StateDb

BENCH_KEEP_PER_CAM = 50        # runs kept per camera (a full run is ~15 minutes; 50 covers many sessions)


class BenchStore:
    def __init__(self, db: StateDb, clock: Clock) -> None:
        self._db, self._clock = db, clock

    def add(self, b: Bench) -> dict:
        doc = {"v": PROTOCOL_VERSION, "t": "bench", "cam": b.cam, "run_id": b.run_id,
               "results": [dict(r.data) for r in b.results], "received_wall_ms": self._clock.wall_ms()}
        with self._db.tx() as c:
            c.execute("INSERT INTO benchmarks (cam, run_id, received_wall_ms, doc) VALUES (?, ?, ?, ?)",
                      (b.cam, b.run_id, doc["received_wall_ms"], json.dumps(doc)))
            c.execute("DELETE FROM benchmarks WHERE cam = ? AND id NOT IN "
                      "(SELECT id FROM benchmarks WHERE cam = ? ORDER BY id DESC LIMIT ?)",
                      (b.cam, b.cam, BENCH_KEEP_PER_CAM))
        return doc

    def runs(self, cam: int) -> list[dict]:
        """Newest first."""
        return [json.loads(r["doc"]) for r in
                self._db.query("SELECT doc FROM benchmarks WHERE cam = ? ORDER BY id DESC", (cam,))]
