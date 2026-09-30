"""Step 9: the simulated phone against the real server app (pairing, frames, sync, ROI, loss, status)."""

import asyncio
import time

import httpx2
import pytest
from harness import PAIR_CODE, ServerHarness, SimRig, make_scenario

from duoware.sim.phone import PairingError

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def harness(tmp_path):
    async with ServerHarness(tmp_path / "data") as h:
        yield h


def missing_between_accepted(phone, last_seq: int) -> int:
    """Seqs the server could see were lost: gaps between the first and the last frame it received."""
    got = sorted(k for k, r in phone.truth_log.items() if r.arrival_ns is not None and k <= last_seq)
    return got[-1] - got[0] + 1 - len(got)


async def until(cond, timeout=10.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


async def test_the_sim_phone_pairs_and_its_frames_are_accepted(harness):
    rig = await SimRig(make_scenario(harness.http_port)).start()
    try:
        await rig.phone.wait_connected()
        sv = harness.sv
        assert await until(lambda: sv.sessions.by_cam(1) is not None and sv.sessions.by_cam(1).last_seq is not None)
        sess = sv.sessions.by_cam(1)
        assert sess.hello.model == "DUO-WARE simulator" and sess.link_mode == "wired_tether"
        assert await until(lambda: sess.clock_sync.ok(sv.clock.mono_ns()) and sess.clock_sync.samples >= 5)
        await asyncio.sleep(1.0)
        assert sess.last_seq > 20
        st = sess.stats
        assert (st.rx_bad, st.rx_unauth, st.rx_late, st.rx_version, st.rx_bad_marker) == (0, 0, 0, 0, 0)
        assert st.dropped == len(rig.phone.dropped_seqs) - sum(1 for s in rig.phone.dropped_seqs if s > sess.last_seq) \
            or abs(st.dropped - len(rig.phone.dropped_seqs)) <= 2
        assert sess.app_mode == "tracking" and sess.last_status.clock == "boottime"
        s = sess.last_status
        assert s.cpu_app_pct is None and s.thermal is None and s.perf is None and s.battery_pct is None   # not modelled: null
        assert s.fps_target == 30 and s.resolution == (1280, 720) and s.stages_ms["pipeline"].p50 > 30
        async with httpx2.AsyncClient(base_url=harness.url) as c:
            cams = (await c.get("/api/cameras")).json()
        assert cams[0]["online"] and cams[0]["link"]["mode"] == "wired_tether" and cams[0]["caps"]["camera"]["yuv_sizes"][0][:2] == [1280, 720]
    finally:
        await rig.stop()


async def test_pairing_with_a_wrong_code_fails_and_the_token_is_reused(harness):
    sc = make_scenario(harness.http_port)
    rig = SimRig(sc, script=False)
    rig.phone.pair_code = "WRONG-1"
    with pytest.raises(PairingError) as e:
        await rig.phone.connect()
    assert e.value.code == "bad_pair_code"
    rig.phone.pair_code = PAIR_CODE
    await rig.phone.connect()
    token = rig.phone.token
    assert token and len(token) == 32
    rig.phone.pair_code = None                                    # from now on only the stored token can get in
    await rig.phone.switch_session("wireless")
    assert rig.phone.token == token and rig.phone.sessions_opened == 2
    await rig.stop()


async def test_the_sim_phone_follows_roi_settings_from_the_server(harness):
    sv = harness.sv
    async with httpx2.AsyncClient(base_url=harness.url) as c:
        assert (await c.post("/api/venue-presets/sim_3x3/apply", json={})).status_code == 200
        assert (await c.put("/api/camera-settings", json={"full_scan_every": 3})).status_code == 200
    rig = await SimRig(make_scenario(harness.http_port, parked=True), script=False).start()
    try:
        await rig.phone.wait_connected()
        assert rig.phone.settings.tracking.track_ids == (1, 5) and rig.phone.settings.tracking.full_scan_every == 3
        assert await until(lambda: sv.world.snapshot().cameras.get(1) and sv.world.snapshot().cameras[1].calib["status"] == "OK", 20)
        log = [rig.phone.truth_log[k] for k in sorted(rig.phone.truth_log)]
        assert len(log) > 25
        assert all(r.scan == "full" for r in log if r.seq % 3 == 0)               # a full scan at least every 3rd frame
        roi = [r for r in log if r.scan == "roi"]
        assert len(roi) > 15 and all(r.n_markers <= 2 for r in roi)              # only the two tracked car tags
        full = [r for r in log if r.scan == "full"]
        assert all(r.n_markers >= 9 for r in full[1:])
        sess = sv.sessions.by_cam(1)
        assert sv.world.full_frames_seen >= len(full) - 3
        # the camera learns the floor from full frames only and still tracks the cars from the roi frames
        snap = sv.world.snapshot()
        assert snap.cameras[1].calib["status"] in ("OK", "WEAK") and snap.tags[10].seen and snap.tags[1].seen
        assert sess.stats.rx_bad_marker == 0
        await c_put(harness, {"full_scan_every": 2})
        assert await until(lambda: rig.phone.settings.tracking.full_scan_every == 2)       # settings pushed live
    finally:
        await rig.stop()


async def c_put(harness, body):
    async with httpx2.AsyncClient(base_url=harness.url) as c:
        assert (await c.put("/api/camera-settings", json=body)).status_code == 200


async def test_drops_and_delays_are_recorded_in_the_truth_log(harness):
    import dataclasses
    sc = make_scenario(harness.http_port)
    sc = dataclasses.replace(sc, network=dataclasses.replace(sc.network, drop_fraction=0.2, burst_drop_every_s=0))
    rig = await SimRig(sc, script=False).start()
    try:
        await rig.phone.wait_connected()
        await asyncio.sleep(2.0)
        sess = harness.sv.sessions.by_cam(1)
        log = rig.phone.truth_log
        last = sess.last_seq
        assert last > 40
        mine = [r for k, r in log.items() if k <= last]
        true_loss = sum(r.dropped for r in mine) / len(mine)
        assert 0.05 < true_loss < 0.4
        assert sess.stats.dropped == missing_between_accepted(rig.phone, last)
        for r in mine:
            if r.arrival_ns is not None:
                assert r.arrival_ns - r.t_mid_ns > 20 * 1_000_000           # pipeline 45 + detect 15 - exposure/2, in ns
    finally:
        await rig.stop()


async def test_benchmark_mode_is_reported_in_status(harness):
    rig = await SimRig(make_scenario(harness.http_port), script=False).start()
    try:
        await rig.phone.wait_connected()
        rig.phone.set_app_mode("benchmark")
        assert await until(lambda: harness.sv.sessions.by_cam(1).app_mode == "benchmark", 4)
    finally:
        await rig.stop()


async def test_a_phone_started_before_the_server_keeps_retrying():
    from harness import free_tcp_port
    sc = make_scenario(free_tcp_port())
    rig = SimRig(sc, script=False)
    rig.phone.start()
    await asyncio.sleep(0.3)
    assert rig.phone.session is None
    await rig.stop()

