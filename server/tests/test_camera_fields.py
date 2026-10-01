"""M2 step 4: new phone settings keys (tracking.demote_after_scans / corner_refine / aruco3, overrides for aruco3 and
threads), and the new camera fields (`frame_age_ms`, `sync.rejected`, `processing_on`, richer status samples)."""

import dataclasses
import json
import time

import pytest
from conftest import make_hello
from test_sessions import open_ok, status
from test_udp import Rig, frame

from duoware.ingest.camera_settings import build_camera_settings
from duoware.ingest.clocksync import ClockSync
from duoware.ingest.sessions import PhoneSessions
from duoware.protocol import ProtocolError
from duoware.protocol.phone_session import CameraSettings, encode_settings, encode_status, parse_ws_message
from duoware.protocol.phone import SyncReply, encode_sync_r
from duoware.settings import SettingsError, load_settings
from duoware.state_builder import camera_state

MS = 1_000_000


# ------------------------------------------------------------------------------ settings keys

def test_settings_carry_the_new_tracking_keys_from_tuning(env):
    s = build_camera_settings(env.settings, {}, env.registry)
    ph = env.settings.tuning.phone
    assert (s.tracking.demote_after_scans, s.tracking.corner_refine, s.tracking.aruco3, s.tracking.threads) == \
        (ph.demote_after_scans, ph.corner_refine, ph.aruco3, ph.threads)
    parsed = parse_settings_roundtrip(s)
    assert parsed == s


def parse_settings_roundtrip(s: CameraSettings) -> CameraSettings:
    from duoware.protocol._read import Obj
    return CameraSettings.parse(Obj(json.loads(encode_settings(s))))


def test_overrides_for_aruco3_and_threads_reach_the_settings(env):
    env.overrides.update({"aruco3": True, "threads": 2}, "local")
    s = build_camera_settings(env.settings, env.overrides.get(), env.registry)
    assert s.tracking.aruco3 is True and s.tracking.threads == 2


@pytest.mark.parametrize("patch", [{"aruco3": 1}, {"aruco3": "yes"}, {"threads": -1}, {"threads": True}, {"threads": 1.5}])
def test_bad_aruco3_or_threads_overrides_are_rejected(api, patch):
    r = api.put("/api/camera-settings", json=patch)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation"


def test_put_camera_settings_accepts_aruco3_and_threads(api):
    r = api.put("/api/camera-settings", json={"aruco3": True, "threads": 0})
    assert r.status_code == 200 and r.json()["overrides"] == {"aruco3": True, "threads": 0}


def test_an_unknown_corner_refine_value_is_a_settings_error(tmp_path):
    cfg = tmp_path / "config"
    src = load_settings().config_dir
    cfg.mkdir()
    for f in src.glob("*.toml"):
        text = f.read_text(encoding="utf-8")
        if f.name == "tuning.toml":
            text = text.replace('corner_refine = "subpix"', 'corner_refine = "edges"')
        (cfg / f.name).write_text(text, encoding="utf-8")
    with pytest.raises(SettingsError, match="corner_refine"):
        load_settings(config_dir=cfg, data_dir=tmp_path / "data")


# ------------------------------------------------------------------------------ status.processing_on

def test_processing_on_is_optional_and_round_trips():
    st = status()
    assert parse_ws_message(encode_status(st)).processing_on is None          # absent: not reported
    st2 = dataclasses.replace(st, processing_on=("ois", "af"))
    assert parse_ws_message(encode_status(st2)).processing_on == ("ois", "af")


def test_processing_on_must_be_a_list_of_strings():
    obj = json.loads(encode_status(status()))
    obj["processing_on"] = ["ois", 3]
    with pytest.raises(ProtocolError):
        parse_ws_message(json.dumps(obj))


# ------------------------------------------------------------------------------ sync.rejected

def test_clock_sync_counts_rejected_replies_over_its_window(env):
    cs = ClockSync(env.settings.tuning.clock_sync)
    t = 10_000 * MS
    assert cs.add(t, 0, 0, t + 50 * MS) is False                             # rtt 50 ms > max_rtt_ms
    cs.note_rejected(t + 60 * MS)                                           # e.g. an unknown n
    assert cs.rejected(t + 100 * MS) == 2
    window_ns = int(env.settings.tuning.clock_sync.window_s * 1e9)
    assert cs.rejected(t + 61 * MS + window_ns) == 0                        # aged out


async def test_unknown_or_stale_sync_replies_count_as_rejected(env):
    r = await Rig().start(env, answer_sync=False)
    try:
        r.send(frame(r.s.sid, 0))
        await r.settle(0.05)
        r.send(encode_sync_r(SyncReply(r.s.cam, r.s.sid, 9999, 1, 2, 3)))  # never requested
        await r.settle(0.05)
        assert r.s.clock_sync.rejected(time.monotonic_ns()) == 1
    finally:
        r.stop()


# ------------------------------------------------------------------------------ camera state fields

def test_camera_state_has_frame_age_rejected_and_processing_on(api):
    sv = api.sv
    o = sv.sessions.open(make_hello(pair_code=sv.sessions.pair_code), "127.0.0.1")
    s = o.session
    now = sv.clock.mono_ns()
    cam = camera_state(sv, s.cam, now, sv.world.snapshot(now))
    assert cam["frame_age_ms"] is None and cam["processing_on"] is None and cam["sync"]["rejected"] == 0
    s.last_frame_ns = now - 2500 * MS
    s.clock_sync.note_rejected(now)
    sv.sessions.on_status(s, dataclasses.replace(status(), processing_on=("distortion_correction",)))
    cam = camera_state(sv, s.cam, now, sv.world.snapshot(now))
    assert cam["frame_age_ms"] == 2500.0
    assert cam["sync"]["rejected"] == 1
    assert cam["processing_on"] == ["distortion_correction"]
    sv.sessions.on_status(s, dataclasses.replace(status(), processing_on=()))
    assert camera_state(sv, s.cam, now, sv.world.snapshot(now))["processing_on"] == []
    from duoware.protocol.dashboard import CameraState
    CameraState.model_validate({k: v for k, v in cam.items() if k != "caps"})


# ------------------------------------------------------------------------------ 60 s status sample

def test_status_sample_carries_the_server_measurements(env):
    sessions = PhoneSessions(env.db, env.settings, env.events, env.clock)
    o = open_ok(sessions, pair_code=sessions.pair_code)
    s = o.session
    sessions.on_status(s, status())
    t = env.clock.mono_ns()
    s.clock_sync.add(t, t, t, t + 1 * MS)
    for i in range(10):
        s.stats.add_pose_age(40.0 + i, t)
        s.link_stats.add(i, t, 2.0)
    env.clock.advance_s(1)
    sessions.tick()
    env.clock.advance_s(60)
    s.clock_sync.add(env.clock.mono_ns(), 0, 0, env.clock.mono_ns() + 1 * MS)
    sessions.tick()
    sample = next(e for e in env.events.query(type="camera") if e.key == "status_sample")
    srv = sample.facts["server"]
    assert set(srv) == {"fps", "pose_age_ms", "latency_ms", "jitter_ms", "loss_pct"}
    assert set(srv["latency_ms"]) == {"p50", "p95"}
