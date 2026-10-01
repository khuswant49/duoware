"""M2 step 5 / B6, B7: previews (PROTOCOL.md §4.6, §7.2 preview.jpg) and benchmark storage (§4.7, §7.2)."""

import json
import time

from conftest import make_app
from fastapi.testclient import TestClient
from test_ws_phone import app_client, pair, stack  # noqa: F401  (fixtures)

from duoware.protocol.phone import Bench, BenchResult
from duoware.store.benchmarks import BENCH_KEEP_PER_CAM, BenchStore

JPEG = b"\xff\xd8\xff\xe0preview-bytes\xff\xd9"
MS = 1_000_000


def preview_msg(cap_ns: int, jpeg: bytes = JPEG, w: int = 480, h: int = 270) -> bytes:
    return b"DWP1" + cap_ns.to_bytes(8, "big") + w.to_bytes(2, "big") + h.to_bytes(2, "big") + jpeg


def wait(cond, timeout=2.0):
    t0 = time.time()
    while not cond():
        if time.time() - t0 > timeout:
            raise AssertionError("timed out")
        time.sleep(0.02)


def test_preview_round_trip_with_seq_and_capture_age(app_client, stack):
    c = app_client
    assert c.get("/api/cameras/1/preview.jpg").status_code == 404
    ws, w = pair(c, stack)
    sess = c.sv.sessions.by_sid(w["sid"])
    ws.send_bytes(preview_msg(5))
    wait(lambda: 1 in c.sv.previews)
    r = c.get("/api/cameras/1/preview.jpg")
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-preview-seq"] == "1"
    assert "x-capture-age-ms" not in r.headers                   # unsynced: no age (PROTOCOL.md §7.2)
    assert [cam for cam in (c.get("/api/cameras").json()) if cam["cam"] == 1][0]["preview_seq"] == 1

    # synced: the age is capture (phone clock -> server clock) to now
    now = c.sv.clock.mono_ns()
    theta = 7_000_000_000                                         # phone clock = server clock + 7 s
    for k in range(4):
        t = now + k * MS
        sess.clock_sync.add(t, t + theta, t + theta, t + 2 * MS)
    ws.send_bytes(preview_msg(now + theta - 40 * MS))
    wait(lambda: c.sv.previews[1].seq == 2)
    r = c.get("/api/cameras/1/preview.jpg")
    assert r.headers["x-preview-seq"] == "2"
    age = float(r.headers["x-capture-age-ms"])
    assert 35 < age < 2000, age


def test_bad_or_oversized_previews_are_dropped(app_client, stack):
    c = app_client
    ws, _ = pair(c, stack)
    ws.send_bytes(preview_msg(1))
    wait(lambda: 1 in c.sv.previews)
    ws.send_bytes(b"DWP2" + preview_msg(2)[4:])                  # bad magic
    ws.send_bytes(preview_msg(3, jpeg=b"\xff" * (512 * 1024 + 1)))  # over 512 KB
    ws.send_bytes(preview_msg(4, jpeg=b"ok"))                     # the next good one still arrives
    wait(lambda: c.sv.previews[1].cap_ns == 4)
    assert c.sv.previews[1].seq == 2


def bench(cam: int, run_id: str, n: int = 1) -> Bench:
    return Bench(cam, run_id, tuple(BenchResult({"pipeline": "native_roi", "fps": 59.0 + i}) for i in range(n)))


def test_benchmark_runs_are_stored_newest_first_and_pruned(env):
    store = BenchStore(env.db, env.clock)
    for i in range(BENCH_KEEP_PER_CAM + 3):
        store.add(bench(1, f"run{i}"))
        env.clock.advance_s(1)
    store.add(bench(2, "other"))
    runs = store.runs(1)
    assert len(runs) == BENCH_KEEP_PER_CAM
    assert runs[0]["run_id"] == f"run{BENCH_KEEP_PER_CAM + 2}" and runs[-1]["run_id"] == "run3"
    assert runs[0]["t"] == "bench" and runs[0]["v"] == 1 and "received_wall_ms" in runs[0]
    assert [r["run_id"] for r in store.runs(2)] == ["other"]


def test_bench_message_is_stored_logged_and_survives_a_restart(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as c:
        c.sv = app.state.get_services()
        import contextlib
        with contextlib.ExitStack() as st:
            ws, w = pair(c, st)
            ws.send_text(json.dumps({"v": 1, "t": "bench", "cam": w["cam"], "run_id": "20261001-140200",
                                     "results": [{"pipeline": "java_full", "fps": 21.5},
                                                 {"pipeline": "native_roi", "fps": 59.6}]}))
            ws.send_text(json.dumps({"v": 1, "t": "bench", "cam": 9, "run_id": "not-mine", "results": []}))
            wait(lambda: c.get(f"/api/cameras/{w['cam']}/benchmarks").json()["runs"])
            ws.close()
        ev = [e for e in c.sv.events.query(type="camera") if e.key == "benchmark"]
        assert len(ev) == 1 and ev[0].facts["results"] == 2
    app2 = make_app(tmp_path)
    with TestClient(app2) as c2:
        runs = c2.get("/api/cameras/1/benchmarks").json()["runs"]
        assert [r["run_id"] for r in runs] == ["20261001-140200"]
        assert [x["pipeline"] for x in runs[0]["results"]] == ["java_full", "native_roi"]
        assert c2.get("/api/cameras/9/benchmarks").json() == {"runs": []}    # another camera's id is ignored


def test_schema_is_version_2(env):
    assert env.db.schema_version == 2
    assert env.db.query("SELECT name FROM sqlite_master WHERE name = 'benchmarks'")
