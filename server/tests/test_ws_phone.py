"""Step 8: /ws/phone (PROTOCOL.md §4.3) and the UDP path of a paired session."""

import contextlib
import json
import socket
import time

import pytest
from conftest import make_app, make_hello
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from duoware.protocol.phone import Status, encode_frame, parse_datagram
from duoware.protocol.phone_session import encode_hello, encode_status
from scene import Scene, status


@pytest.fixture
def app_client(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as c:
        c.sv = app.state.get_services()
        yield c


def hello_text(**kw) -> str:
    return encode_hello(make_hello(**kw))


def expect_close(ws, code):
    with pytest.raises(WebSocketDisconnect) as e:
        ws.receive_text()
    assert e.value.code == code


@pytest.fixture
def stack(app_client):
    """Closes the sockets opened by `pair` before the TestClient (and its event loop) shuts down."""
    with contextlib.ExitStack() as st:
        yield st


def pair(c, stack, device="dev-1", **kw):
    ws = stack.enter_context(c.websocket_connect("/ws/phone"))
    ws.send_text(hello_text(device_id=device, pair_code=c.sv.sessions.pair_code, **kw))
    return ws, json.loads(ws.receive_text())


def test_pairing_with_the_code_then_reconnect_with_the_token(app_client, stack):
    c = app_client
    ws, w = pair(c, stack)
    assert w["t"] == "welcome" and w["cam"] == 1 and len(w["sid"]) == 16 and len(w["token"]) == 32
    assert w["frames_port"] == c.sv.ports["frames"] and w["frames_tcp_port"] == c.sv.ports["tcp"] and w["server_id"] == c.sv.db.server_id
    s = w["settings"]
    assert s["resolution"] == [1280, 720] and s["exposure_ns"] == 3_000_000 and s["iso"] == 800
    assert s["tracking"]["track_ids"] == [] and s["tracking"]["full_scan_every"] == 10
    assert s["thermal"]["fps_steps"] == [1.0, 0.75, 0.5] and s["preview"] == {"fps": 3, "width": 480, "quality": 60}
    assert c.sv.sessions.by_sid(w["sid"]) is not None and c.sv.sessions.by_cam(1).hello.model == "test phone"
    ws.close()
    time.sleep(0.1)
    assert c.sv.sessions.by_sid(w["sid"]) is None                 # closing the socket ends the session
    with c.websocket_connect("/ws/phone") as ws2:
        ws2.send_text(hello_text(token=w["token"]))
        w2 = json.loads(ws2.receive_text())
        assert w2["t"] == "welcome" and w2["token"] is None and w2["cam"] == 1 and w2["sid"] != w["sid"]


def test_bad_code_and_bad_token_close_with_4001(app_client):
    with app_client.websocket_connect("/ws/phone") as ws:
        ws.send_text(hello_text(pair_code="WRONG-1"))
        assert json.loads(ws.receive_text())["code"] == "bad_pair_code"
        expect_close(ws, 4001)
    with app_client.websocket_connect("/ws/phone") as ws:
        ws.send_text(hello_text(token="0" * 32))
        assert json.loads(ws.receive_text())["code"] == "bad_token"
        expect_close(ws, 4001)


def test_too_many_failures_lock_the_address(app_client):
    n = app_client.sv.settings.server.access.pair_max_failures
    for _ in range(n):
        with app_client.websocket_connect("/ws/phone") as ws:
            ws.send_text(hello_text(pair_code="WRONG-1"))
            ws.receive_text()
    with app_client.websocket_connect("/ws/phone") as ws:
        ws.send_text(hello_text(pair_code=app_client.sv.sessions.pair_code))
        assert json.loads(ws.receive_text())["code"] == "locked"
        expect_close(ws, 4003)


def test_version_mismatch_and_garbage_hello(app_client):
    with app_client.websocket_connect("/ws/phone") as ws:
        ws.send_text(json.dumps({"v": 2, "t": "hello"}))
        assert json.loads(ws.receive_text())["code"] == "version"
        expect_close(ws, 4002)
    for text in ("not json", json.dumps({"v": 1, "t": "status"}), json.dumps({"v": 1, "t": "hello", "device_id": "x"})):
        with app_client.websocket_connect("/ws/phone") as ws:
            ws.send_text(text)
            assert json.loads(ws.receive_text())["code"] == "bad_message"
            expect_close(ws, 4000)


def test_no_hello_within_the_timeout_closes_with_4000(tmp_path, monkeypatch):
    import duoware.api.phone_ws as pw
    monkeypatch.setattr(pw, "HELLO_TIMEOUT_S", 0.2)
    with TestClient(make_app(tmp_path)) as c, c.websocket_connect("/ws/phone") as ws:
        assert json.loads(ws.receive_text())["code"] == "bad_message"
        expect_close(ws, 4000)


def test_a_newer_session_of_the_same_device_closes_the_old_one_with_4004(app_client, stack):
    c = app_client
    ws1, w1 = pair(c, stack)
    with c.websocket_connect("/ws/phone") as ws2:
        ws2.send_text(hello_text(token=w1["token"]))
        w2 = json.loads(ws2.receive_text())
        assert w2["cam"] == w1["cam"] and w2["sid"] != w1["sid"]
        assert json.loads(ws1.receive_text())["code"] == "replaced"
        expect_close(ws1, 4004)
        assert c.sv.sessions.by_sid(w1["sid"]) is None and c.sv.sessions.by_sid(w2["sid"]) is not None
        time.sleep(0.1)
        assert c.sv.sessions.by_sid(w2["sid"]) is not None     # the old handler's cleanup did not end the new session


def test_status_bench_and_preview_messages(app_client, stack):
    c = app_client
    ws, w = pair(c, stack)
    st = status("setup", cam=w["cam"])
    ws.send_text(encode_status(st))
    ws.send_text(json.dumps({"v": 1, "t": "bench", "cam": 1, "run_id": "r", "results": []}))
    ws.send_bytes(b"DWP1" + (123).to_bytes(8, "big") + (480).to_bytes(2, "big") + (270).to_bytes(2, "big") + b"\xff\xd8x")
    ws.send_bytes(b"garbage")
    ws.send_text("not json")
    ws.send_text(encode_status(status("tracking", cam=w["cam"])))
    deadline = time.time() + 2
    sess = c.sv.sessions.by_sid(w["sid"])
    while sess.app_mode != "tracking" and time.time() < deadline:
        time.sleep(0.02)
    assert sess.app_mode == "tracking"                           # the bad messages did not end the session
    assert c.sv.previews[1].w == 480 and c.sv.previews[1].cap_ns == 123 and c.sv.previews[1].jpeg == b"\xff\xd8x"
    keys = [(e.key, e.value) for e in reversed(c.sv.events.query(type="camera"))]
    assert ("app_mode", "setup") in keys and ("app_mode", "tracking") in keys
    ws.close()


def test_udp_frames_of_a_paired_session_are_accepted(app_client, stack):
    c = app_client
    ws, w = pair(c, stack)
    sess = c.sv.sessions.by_sid(w["sid"])
    sess.peer_ip = "127.0.0.1"                                   # TestClient's WebSocket peer is "testclient"
    scene = Scene()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(1)
    try:
        for seq in range(3):
            f = scene.frame(sess, time.monotonic_ns())
            s.sendto(encode_frame(f), ("127.0.0.1", c.sv.ports["frames"]))
        deadline = time.time() + 2
        while sess.last_seq != 2 and time.time() < deadline:
            time.sleep(0.02)
        assert sess.last_seq == 2 and sess.frame_addr[1] == s.getsockname()[1]
        req = json.loads(s.recv(2048))                           # the server starts clock sync towards the frame source
        assert req["t"] == "sync" and req["sid"] == w["sid"]
    finally:
        s.close()
        ws.close()


def test_settings_are_pushed_when_overrides_or_car_tags_change(app_client, stack):
    c = app_client
    ws, w = pair(c, stack)
    c.put("/api/camera-settings", json={"exposure_ms": 5.0, "fps": 30})
    m = json.loads(ws.receive_text())
    assert m["t"] == "settings" and m["exposure_ns"] == 5_000_000 and m["fps"] == 30
    c.put("/api/tags/1", json={"role": "car", "size_mm": 80, "car": "DUO-A", "expected_version": 0})
    m = json.loads(ws.receive_text())
    assert m["t"] == "settings" and m["tracking"]["track_ids"] == [1]
    c.put("/api/tags/5", json={"role": "car", "size_mm": 80, "car": "DUO-B", "expected_version": 0})
    assert json.loads(ws.receive_text())["tracking"]["track_ids"] == [1, 5]
    c.put("/api/tags/9", json={"role": "ignore", "expected_version": 0})             # no car change: no push
    c.put("/api/camera-settings", json={"full_scan_every": 4})
    assert json.loads(ws.receive_text())["tracking"]["full_scan_every"] == 4
    ws.close()


def test_revoking_a_pairing_ends_the_session(app_client, stack):
    c = app_client
    ws, w = pair(c, stack)
    assert c.delete("/api/pairing/dev-1").status_code == 200
    assert json.loads(ws.receive_text())["code"] == "bad_token"
    expect_close(ws, 4001)
    assert c.sv.sessions.by_sid(w["sid"]) is None


__all__ = ["Status", "parse_datagram"]
