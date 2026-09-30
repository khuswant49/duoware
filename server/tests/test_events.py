"""Step 3: the event log."""

import asyncio
import threading
import time

from duoware.clock import FakeClock
from duoware.store.events import EventLog, clean


def test_events_persist_across_reopen_and_ids_continue(tmp_path):
    path = tmp_path / "events.db"
    log = EventLog(path, FakeClock())
    a = log.log("registry", key="tag:5", value="car", prev="unassigned", facts={"old": None, "new": {"id": 5}},
                reason="operator assigned a role", operator="local")
    b = log.log("system", reason="start")
    assert (a.id, b.id) == (1, 2)
    log.close()
    again = EventLog(path, FakeClock())
    assert again.last_id == 2
    c = again.log("system", reason="stop")
    assert c.id == 3
    got = again.query()
    assert [e.id for e in got] == [3, 2, 1]
    assert got[2].facts == {"old": None, "new": {"id": 5}} and got[2].operator == "local"
    again.close()


def test_query_filters_and_limits(tmp_path):
    log = EventLog(tmp_path / "events.db", FakeClock())
    for i in range(10):
        log.log("camera" if i % 2 else "layout", car="DUO-A" if i % 3 == 0 else None, key=str(i))
    assert [e.key for e in log.query(type="camera")] == ["9", "7", "5", "3", "1"]
    assert [e.key for e in log.query(car="DUO-A")] == ["9", "6", "3", "0"]
    assert [e.id for e in log.query(since_id=7)] == [10, 9, 8]
    assert [e.id for e in log.query(limit=2)] == [10, 9]
    assert [e.key for e in log.query(type="camera", car="DUO-A")] == ["9", "3"]
    log.close()


def test_value_and_facts_are_json_safe(tmp_path):
    log = EventLog(tmp_path / "events.db", FakeClock())
    r = log.log("camera", value=True, prev=3.14159, facts={"x": 1.23456, "bad": float("nan"), "s": {1, 2}, "o": object})
    assert (r.value, r.prev) == ("true", "3.14")
    assert r.facts["x"] == 1.23 and r.facts["bad"] is None and sorted(r.facts["s"]) == [1, 2]
    assert clean((1, 2.555)) == [1, 2.56] or clean((1, 2.555)) == [1, 2.55]
    log.close()


def test_threads_log_and_query_without_loss(tmp_path):
    log = EventLog(tmp_path / "events.db", FakeClock())
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer():
        try:
            for i in range(1000):
                log.log("system", key=str(i))
        except BaseException as e:               # pragma: no cover
            errors.append(e)

    def reader():
        try:
            while not stop.is_set():
                log.query(limit=50)
        except BaseException as e:               # pragma: no cover
            errors.append(e)

    tw, tr = threading.Thread(target=writer), threading.Thread(target=reader)
    tr.start()
    tw.start()
    tw.join()
    stop.set()
    tr.join()
    assert not errors
    got = log.query(limit=1000)
    assert len(got) == 1000 and sorted(e.id for e in got) == list(range(1, 1001))
    log.close()


def test_log_never_blocks_on_disk(tmp_path):
    log = EventLog(tmp_path / "events.db", FakeClock())
    log.pause_writer()
    t0 = time.perf_counter()
    for i in range(2000):
        log.log("system", key=str(i))
    assert time.perf_counter() - t0 < 1.0
    log.resume_writer()
    assert len(log.query(limit=5000)) == 2000
    log.close()


def test_subscribe_receives_events_from_other_threads(tmp_path):
    async def run():
        log = EventLog(tmp_path / "events.db", FakeClock())
        q = log.subscribe()
        threading.Thread(target=lambda: log.log("operator", operator="local", reason="E-stop")).start()
        rec = await asyncio.wait_for(q.get(), 2)
        assert rec.type == "operator" and rec.reason == "E-stop"
        log.unsubscribe(q)
        log.log("system")
        await asyncio.sleep(0.05)
        assert q.empty()
        log.close()

    asyncio.run(run())


def test_fake_clock_times_are_recorded(tmp_path):
    clock = FakeClock(start_ns=5_000_000_000)
    log = EventLog(tmp_path / "events.db", clock)
    a = log.log("system")
    clock.advance_s(2)
    b = log.log("system")
    assert b.mono_ms - a.mono_ms == 2000 and b.wall_ms - a.wall_ms == 2000
    log.close()
