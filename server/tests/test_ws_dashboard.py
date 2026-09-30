"""Step 8: /ws/dashboard (PROTOCOL.md §6): hello, state snapshots, events; local clients only."""

import json
import time

import pytest
from conftest import make_app, make_hello
from fastapi.testclient import TestClient
from scene import Scene, feed_frames, status
from starlette.websockets import WebSocketDisconnect

from duoware.protocol.dashboard import EventMsg, HelloMsg, StateMsg


@pytest.fixture
def dash(tmp_path):
    """allow_remote_dashboard so that TestClient ("testclient") is accepted on the WebSocket."""
    app = make_app(tmp_path, allow_remote=True)
    with TestClient(app) as c:
        c.sv = app.state.get_services()
        yield c


def next_state(ws, skip_events=True):
    while True:
        m = json.loads(ws.receive_text())
        if m["t"] == "state" or not skip_events:
            return m


def test_hello_then_valid_state_snapshots(dash):
    with dash.websocket_connect("/ws/dashboard") as ws:
        hello = json.loads(ws.receive_text())
        assert HelloMsg.model_validate(hello).state_hz == 20 and hello["mode"] == "sim"
        assert hello["server_id"] == dash.sv.db.server_id
        s1, s2 = next_state(ws), next_state(ws)
        for s in (s1, s2):
            m = StateMsg.model_validate(s)
            assert m.system.mode == "sim" and m.system.estop is False and m.cameras == [] and m.tags == []
            assert [c.name for c in m.cars] == ["DUO-A", "DUO-B"]
            assert m.cars[0].link.state == "disconnected" and m.cars[0].stopped_reason == "no_link"
            assert m.cars[0].battery == "not_measured" and m.cars[0].pose is None and m.cars[0].tag is None
        assert s2["seq"] > s1["seq"]


def test_state_with_a_live_camera_validates_and_reflects_the_world(dash):
    sv = dash.sv
    dash.post("/api/venue-presets/sim_3x3/apply", json={})
    o = sv.sessions.open(make_hello(pair_code=sv.sessions.pair_code), "127.0.0.1")
    sv.sessions.on_status(o.session, status())
    scene = Scene(noise_px=0.07)
    feed_frames(sv, o.session, scene, 70)
    with dash.websocket_connect("/ws/dashboard") as ws:
        ws.receive_text()
        time.sleep(0.15)
        s = next_state(ws)
        m = StateMsg.model_validate(s)
        cam = m.cameras[0]
        assert cam.online and cam.calib.status == "OK" and cam.sync.ok and cam.app_mode == "tracking"
        assert cam.link.mode == "wireless" and cam.link.transport == "udp" and cam.fps == 30.0
        assert sorted(cam.calib.floor_tags_seen) == [2, 3, 4, 6, 7, 10, 11, 12, 13]
        tags = {t.id: t for t in m.tags}
        assert tags[10].role == "node" and tags[10].seen and tags[10].placed is True and abs(tags[10].x_mm) < 3
        assert {c.name: c.pose is not None for c in m.cars} == {"DUO-A": True, "DUO-B": True}
        assert m.cars[0].tag == 1 and m.cars[0].pose.usable_for_control is True
        assert m.system.registry_version == 1 and m.system.events_last_id >= 2


def test_events_are_pushed_as_they_are_logged(dash):
    with dash.websocket_connect("/ws/dashboard") as ws:
        ws.receive_text()
        dash.put("/api/tags/5", json={"role": "car", "size_mm": 80, "car": "DUO-B", "expected_version": 0})
        for _ in range(100):
            m = json.loads(ws.receive_text())
            if m["t"] == "event":
                ev = EventMsg.model_validate(m).event
                assert ev.type == "registry" and ev.key == "tag:5" and ev.operator == "local"
                break
        else:
            pytest.fail("no event message")
        dash.post("/api/control/stop_all")
        for _ in range(100):
            m = json.loads(ws.receive_text())
            if m["t"] == "event":
                assert EventMsg.model_validate(m).event.key == "estop"
                break
        s = next_state(ws)
        assert s["system"]["estop"] is True and s["cars"][0]["stopped_reason"] == "estop"
        assert s["system"]["registry_version"] == 1


def test_anything_the_dashboard_sends_is_ignored(dash):
    with dash.websocket_connect("/ws/dashboard") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"v": 1, "t": "stop_all"}))
        ws.send_text("junk")
        time.sleep(0.1)
        assert next_state(ws)["system"]["estop"] is False
        assert not dash.sv.safety.estop


def test_remote_clients_are_refused_by_default(tmp_path):
    with TestClient(make_app(tmp_path)) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/dashboard") as ws:
                ws.receive_text()
        assert e.value.code == 1008


async def test_a_slow_client_is_skipped_not_queued(tmp_path):
    import asyncio

    from conftest import patched_settings
    from duoware.broadcaster import DashboardClient
    from duoware.services import Services
    from duoware.settings import load_settings

    class Slow:
        sent = 0

        async def send_text(self, text):
            Slow.sent += 1
            await asyncio.sleep(0.5)

    class Fast:
        sent = 0

        async def send_text(self, text):
            Fast.sent += 1

    sv = Services.build(patched_settings(load_settings(data_dir=tmp_path / "d"), frames_port=0), mode="sim",
                        interfaces_fn=lambda: [])
    try:
        slow, fast = DashboardClient(Slow()), DashboardClient(Fast())      # type: ignore[arg-type]
        sv.broadcaster.clients.update({slow, fast})
        sv.broadcaster.start()
        await asyncio.sleep(0.7)
        await sv.broadcaster.stop()
        assert Slow.sent <= 2 and slow.skipped >= 5                         # skipped while still sending, never queued
        assert Fast.sent >= 10 and fast.skipped == 0
    finally:
        sv.events.close()
        sv.db.close()
