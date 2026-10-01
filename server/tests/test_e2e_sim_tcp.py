"""M2 step 3 / B2: the simulator phone in `transport = "tcp"` mode (connection mode `wired_adb`, PROTOCOL.md §2.1,
§4.1) against the real server app: discovery by GET /api/beacon, pairing, frames and clock sync on one TCP connection.
Simulation only: nothing here touches hardware or adb."""

import statistics
import time

import httpx2
import pytest
from harness import ServerHarness, SimRig, make_scenario
from test_e2e_sim import MS, until

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_sim_phone_over_tcp_end_to_end(tmp_path):
    t_start = time.monotonic()
    async with ServerHarness(tmp_path / "data") as h:
        sv = h.sv
        rig = await SimRig(make_scenario(h.http_port, parked=True, transport="tcp"), script=False).start()
        try:
            async with httpx2.AsyncClient(base_url=h.url, timeout=10) as http:
                await rig.phone.wait_connected()
                await until(lambda: sv.sessions.by_cam(1) is not None
                            and sv.sessions.by_cam(1).clock_sync.ok(sv.clock.mono_ns()), 15, what="sync over TCP")
                sess = sv.sessions.by_cam(1)
                assert sess.hello.link.mode == "wired_adb"
                assert sess.transport == "tcp"
                assert sv.endpoint.ingest is sv.ingest                       # one ingest for both transports
                assert sess.frame_addr is None                              # nothing arrived over UDP

                r = await http.post("/api/venue-presets/sim_3x3/apply", json={})
                assert r.status_code == 200, r.text
                await until(lambda: sv.world.snapshot().cameras.get(1) is not None
                            and sv.world.snapshot().cameras[1].calib["status"] == "OK", 20, what="camera OK")

                # pose age: the server's p50 against the simulator's true delay over the same window
                await until(lambda: sess.stats.pose_age().n >= 90, 10, what="enough frames for the pose-age window")
                now = sv.clock.mono_ns()
                window_ns = int(sv.settings.tuning.pose.stats_window_s * 1e9)
                recent = [r for r in rig.phone.truth_log.values()
                          if r.arrival_ns and r.arrival_ns > now - window_ns + 500 * MS]
                true_delay = statistics.median((r.arrival_ns - r.t_mid_ns) / 1e6 for r in recent)
                server_p50 = sess.stats.pose_age().p50
                print(f"E2E_TCP pose_age_p50_ms_server={server_p50:.2f} true={true_delay:.2f}")
                assert abs(server_p50 - true_delay) < 5.0, (server_p50, true_delay)

                cams = (await http.get("/api/cameras")).json()
                link = cams[0]["link"]
                assert (link["transport"], link["mode"]) == ("tcp", "wired_adb")
                assert link["loss_pct"] is not None and link["loss_pct"] <= 1.0    # a TCP link loses only phone-side drops
                assert rig.phone.sessions_opened == 1
        finally:
            await rig.stop()
    elapsed = time.monotonic() - t_start
    print(f"E2E_TCP test_seconds={elapsed:.1f}")
    assert elapsed < 25
