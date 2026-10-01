"""M2 step 5 / B8: frame recordings (DECISIONS.md D42) and the blur report (duoware.tools.blur_report)."""

import asyncio
import dataclasses
import json
from pathlib import Path

import httpx2
import pytest
from harness import ServerHarness, SimRig, make_scenario
from test_e2e_sim import until

from duoware.clock import FakeClock
from duoware.settings import load_settings
from duoware.sim.detector_model import DetectorModel
from duoware.store import recorder as recorder_module
from duoware.store.recorder import FrameRecorder, load
from duoware.tools import blur_report
from duoware.tools.blur_report import BIN_NAMES, Looked, bin_index, collect, report

MS = 1_000_000


def frame_bytes(seq: int, cap_ns: int, markers=(), scan="full", searched=()) -> bytes:
    return json.dumps({"v": 1, "t": "frame", "cam": 1, "sid": "ab" * 8, "seq": seq, "cap_ns": cap_ns,
                       "exp_ns": 3 * MS, "skew_ns": 0, "avail_ns": cap_ns, "sent_ns": cap_ns, "w": 1280, "h": 720,
                       "scan": scan, "searched": list(searched), "m": [list(m) for m in markers]}).encode()


def tag_at(tid: int, x: float, y: float = 100.0, half: float = 20.0) -> tuple:
    return (tid, x - half, y - half, x + half, y - half, x + half, y + half, x - half, y + half)


# ------------------------------------------------------------------------------ recorder

def test_recorder_writes_a_header_and_one_line_per_frame(tmp_path):
    st = load_settings(data_dir=tmp_path / "data")
    rec = FrameRecorder(tmp_path / "rec", st, "ab" * 8, FakeClock(start_ns=1))
    path = rec.start()
    rec.record(10, 1, "udp", True, 1234, frame_bytes(0, 100))
    rec.record(20, 1, "tcp", False, None, frame_bytes(1, 200))
    rec.stop()
    header, lines = load(path)
    lines = list(lines)
    assert header["t"] == "recording" and header["server_id"] == "ab" * 8 and "pose" in header["settings"]
    assert [(ln["recv_ns"], ln["transport"], ln["synced"], ln["theta_ns"], ln["frame"]["seq"]) for ln in lines] == \
        [(10, "udp", True, 1234, 0), (20, "tcp", False, None, 1)]


def test_recorder_never_blocks_and_counts_drops(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module, "RECORDER_QUEUE_MAX", 5)
    st = load_settings(data_dir=tmp_path / "data")
    rec = FrameRecorder(tmp_path / "rec", st, "ab" * 8, FakeClock(start_ns=1))
    for i in range(8):                                       # no writer thread yet: the queue fills up
        rec.record(i, 1, "udp", True, 0, frame_bytes(i, i))
    assert rec.dropped == 3
    path = rec.start()
    rec.stop()
    assert len(list(load(path)[1])) == 5


def test_load_rejects_a_file_without_a_header(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"recv_ns": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load(p)


# ------------------------------------------------------------------------------ blur report on a known file

def write_recording(path: Path, frames: list[bytes]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"t": "recording", "v": 1}) + "\n")
        for fb in frames:
            f.write(json.dumps({"recv_ns": 0, "wall_ms": 0, "cam": 1, "transport": "udp", "synced": True,
                                "theta_ns": 0, "frame": json.loads(fb)}) + "\n")
    return path


def test_speed_from_neighbours_and_only_frames_that_looked(tmp_path):
    # tag 1 moves 4 px per 20 ms frame = 200 px/s; frame 3 is a miss; frame 4 is a roi frame that did not search it
    frames = []
    for k in range(8):
        t = k * 20 * MS
        if k == 4:
            frames.append(frame_bytes(k, t, scan="roi", searched=(5,)))
        elif k == 3:
            frames.append(frame_bytes(k, t))
        else:
            frames.append(frame_bytes(k, t, [tag_at(1, 100 + 4 * k)]))
    looked, excluded = collect(write_recording(tmp_path / "r.jsonl", frames), tag=1)
    assert [lk.cap_ns // (20 * MS) for lk in looked] == [1, 2, 3, 5, 6]     # 0 and 7 lack a neighbour; 4 not looked
    assert excluded == 2
    assert all(abs(lk.speed_px_s - 200.0) < 1e-6 for lk in looked)
    rows = report(looked)
    assert (rows[1].looked, rows[1].detected) == (5, 4) and rows[0].looked == rows[2].looked == 0


def test_bins_and_cli_json(tmp_path, capsys):
    assert [bin_index(v) for v in (0, 149.9, 150, 299.9, 300, 1000)] == [0, 0, 1, 1, 2, 2]
    frames = [frame_bytes(k, k * 20 * MS, [tag_at(1, 100 + k)]) for k in range(5)]
    path = write_recording(tmp_path / "r.jsonl", frames)
    assert blur_report.main([str(path), "--tag", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [b["speed_px_s"] for b in out["bins"]] == list(BIN_NAMES)
    assert out["bins"][0]["looked"] == 3 and out["bins"][0]["clean_read_ratio"] == 1.0
    assert blur_report.main([str(path), "--tag", "1"]) == 0         # the table form runs too
    assert "clean reads" in capsys.readouterr().out


# ------------------------------------------------------------------------------ B8: a simulated run at 26 ms

async def drive_circles(rig: SimRig, plan: dict[str, tuple[float, float]], stop: asyncio.Event) -> None:
    """Steady circles: per car (centre speed mm/s, radius mm), turning right, commands refreshed like a controller."""
    from duoware.sim.script import TTL_MS, _pwm_for
    cars = {c.name: c for c in rig.sc.car}
    while not stop.is_set():
        now = rig.clock.mono_ns()
        for name, (v, radius) in plan.items():
            car = cars[name]
            k = car.wheel_base_mm / (2 * radius)
            rig.world.physics[name].command(_pwm_for(v * (1 + k), car, 1.0), _pwm_for(v * (1 - k), car, car.right_gain),
                                            TTL_MS, now)
        await asyncio.sleep(0.1)


async def test_blur_report_matches_the_simulator_detection_model(tmp_path):
    """Cars driven in steady circles at ~200 and ~350 px/s with a 26 ms exposure; per speed bin the report's
    clean-read ratio must be within 10 percentage points of what the simulator's detection model predicts for the
    same frames (B8). Circles keep the speed constant, so each bin gets a hundred frames or more."""
    data = tmp_path / "data"
    # 400 mm circles side by side (centres (-300, 450) and (900, 450)), inside the camera view. A wide radius keeps the
    # tag corners' speed (what the simulator blurs with) within ~1 % of the centre speed (what the report measures).
    starts = {"DUO-A": (-300.0, 50.0, 0.0), "DUO-B": (900.0, 850.0, 180.0)}
    plan = {"DUO-A": (400.0, 400.0), "DUO-B": (700.0, 400.0)}
    async with ServerHarness(data, record_frames=True) as h:
        sc = make_scenario(h.http_port)
        sc = dataclasses.replace(sc, car=tuple(dataclasses.replace(c, max_speed_mm_s=1500.0, start=starts[c.name])
                                               for c in sc.car))
        rig = await SimRig(sc, script=False).start()
        stop = asyncio.Event()
        driver = asyncio.get_running_loop().create_task(drive_circles(rig, plan, stop))
        try:
            async with httpx2.AsyncClient(base_url=h.url, timeout=10) as http:
                await rig.phone.wait_connected()
                assert (await http.put("/api/camera-settings", json={"exposure_ms": 26.0})).status_code == 200
                assert (await http.post("/api/venue-presets/sim_3x3/apply", json={})).status_code == 200
                await until(lambda: rig.phone.settings is not None
                            and rig.phone.settings.exposure_ns == 26 * MS
                            and rig.phone.settings.tracking.track_ids == (1, 5), 5, what="the settings")
                await asyncio.sleep(12)
        finally:
            stop.set()
            await driver
            await rig.stop()
        rec = h.sv.recorder
        path = rec.path
    assert rec.dropped == 0 and rec.written > 300
    model = DetectorModel(sc.phone_model, rng=None)
    looked: list[Looked] = []
    for tag in (1, 5):
        lk, _ = collect(path, tag)
        looked += [x for x in lk if x.exposure_ns == 26 * MS]
    rows = report(looked)
    checked = 0
    for i, row in enumerate(rows):
        in_bin = [x for x in looked if bin_index(x.speed_px_s) == i]
        expected = sum(model.probability(model.blur_px(x.speed_px_s, x.exposure_ns)) for x in in_bin) / max(1, len(in_bin))
        print(f"B8 bin {row.name}: looked {row.looked}, report {row.ratio}, model {expected:.3f}")
        if row.looked >= 100:                                   # sampling sd <= ~4.5 points at 100 frames
            checked += 1
            assert abs(row.ratio - expected) <= 0.10, (row.name, row.ratio, expected)
    assert checked >= 2                                         # the 150-300 and > 300 bins were really exercised
