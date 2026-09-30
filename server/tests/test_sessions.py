"""Step 5: phone sessions and pairing (PROTOCOL.md §4.3)."""

import pytest
from conftest import make_hello

from duoware.ingest.auth import LoginThrottle, codes_match, generate_code, normalize
from duoware.ingest.sessions import PhoneError, PhoneSessions
from duoware.protocol.phone_session import PerfState, StageStats, Status, ThermalState


@pytest.fixture
def sessions(env):
    return PhoneSessions(env.db, env.settings, env.events, env.clock)


def open_ok(sessions, **kw):
    r = sessions.open(make_hello(**kw), "10.0.0.5")
    assert not isinstance(r, PhoneError), r
    return r


def test_code_helpers():
    c = generate_code()
    assert len(c) == 7 and c[4] == "-" and normalize(c) == c.replace("-", "")
    assert codes_match("k7qx m2", "K7QX-M2") and not codes_match("K7QX-M3", "K7QX-M2") and not codes_match("x", "")


def test_throttle_locks_and_unlocks(env):
    t = LoginThrottle(3, 60, env.clock)
    for _ in range(2):
        t.failure("1.2.3.4")
    assert t.locked_for_s("1.2.3.4") == 0
    t.failure("1.2.3.4")
    assert t.locked_for_s("1.2.3.4") == pytest.approx(60)
    assert t.locked_for_s("9.9.9.9") == 0
    env.clock.advance_s(61)
    assert t.locked_for_s("1.2.3.4") == 0
    t.failure("1.2.3.4")                          # counting starts again after the lock ran out
    assert t.locked_for_s("1.2.3.4") == 0
    t.success("1.2.3.4")


def test_pairing_with_the_code_then_with_the_token(env, sessions):
    r = open_ok(sessions, pair_code=sessions.pair_code)
    assert r.token and len(r.token) == 32 and r.session.cam == 1 and len(r.session.sid) == 16
    assert r.replaced is None and sessions.by_sid(r.session.sid) is r.session
    sessions.close(r.session.sid)
    r2 = open_ok(sessions, token=r.token)           # token, no code: no new token is issued
    assert r2.token is None and r2.session.cam == 1 and r2.session.sid != r.session.sid
    assert sessions.devices()[0]["device_id"] == "dev-1"
    row = env.db.query("SELECT token_sha256 FROM pairings")[0][0]
    assert row != r.token and len(row) == 64        # only the SHA-256 is stored


def test_bad_code_bad_token_and_lock(env, sessions):
    e = sessions.open(make_hello(pair_code="WRONG-1"), "10.0.0.5")
    assert isinstance(e, PhoneError) and e.code == "bad_pair_code"
    e = sessions.open(make_hello(), "10.0.0.5")
    assert e.code == "bad_token"
    ok = open_ok(sessions, pair_code=sessions.pair_code)
    e = sessions.open(make_hello(token="0" * 32), "10.0.0.5")
    assert e.code == "bad_token"
    assert sessions.open(make_hello(token=ok.token, device_id="other"), "10.0.0.5").code == "bad_token"
    for _ in range(env.settings.server.access.pair_max_failures):
        sessions.open(make_hello(pair_code="WRONG-1"), "10.0.0.9")
    locked = sessions.open(make_hello(pair_code=sessions.pair_code), "10.0.0.9")     # even the right code is refused
    assert locked.code == "locked"
    env.clock.advance_s(env.settings.server.access.pair_lock_s + 1)
    assert not isinstance(sessions.open(make_hello(pair_code=sessions.pair_code, device_id="x"), "10.0.0.9"), PhoneError)


def test_a_new_session_of_the_same_device_replaces_the_old_one(sessions):
    a = open_ok(sessions, pair_code=sessions.pair_code)
    b = open_ok(sessions, token=a.token)
    assert b.replaced is a.session and b.session.cam == a.session.cam
    assert sessions.by_sid(a.session.sid) is None and sessions.by_sid(b.session.sid) is b.session
    assert len(sessions.live()) == 1


def test_camera_ids_are_the_lowest_free_and_reused_per_device(sessions):
    opened = [open_ok(sessions, device_id=f"d{i}", pair_code=sessions.pair_code) for i in range(3)]
    assert [o.session.cam for o in opened] == [1, 2, 3]
    sessions.revoke("d1")
    assert open_ok(sessions, device_id="d3", pair_code=sessions.pair_code).session.cam == 2      # the slot d1 freed
    again = open_ok(sessions, device_id="d0", token=opened[0].token)
    assert again.session.cam == opened[0].session.cam


def test_camera_slots_run_out_after_fifteen(sessions):
    for i in range(15):
        open_ok(sessions, device_id=f"d{i}", pair_code=sessions.pair_code)
    assert sessions.open(make_hello(device_id="d15", pair_code=sessions.pair_code), "10.0.0.5").code == "bad_message"


def test_revoke_deletes_the_pairing_and_returns_the_live_session(sessions):
    o = open_ok(sessions, pair_code=sessions.pair_code)
    assert sessions.revoke("dev-1") is o.session
    assert sessions.devices() == []
    assert sessions.open(make_hello(token=o.token), "10.0.0.5").code == "bad_token"


def test_camera_stats_are_per_camera_and_the_pairing_survives_a_restart(env, sessions):
    a = open_ok(sessions, pair_code=sessions.pair_code)
    a.session.stats.dropped = 7
    sessions.close(a.session.sid)
    b = open_ok(sessions, token=a.token)
    assert b.session.stats is a.session.stats and b.session.stats.dropped == 7
    assert b.session.clock_sync is not a.session.clock_sync and b.session.link_stats is not a.session.link_stats
    env.reopen()
    s2 = PhoneSessions(env.db, env.settings, env.events, env.clock)
    assert open_ok(s2, token=a.token).session.cam == 1


def test_pair_code_comes_from_the_environment(env):
    import dataclasses
    st = dataclasses.replace(env.settings, env={"DUO_PAIR_CODE": "ABCD-EF"})
    assert PhoneSessions(env.db, st, env.events, env.clock).pair_code == "ABCD-EF"


def test_hello_capabilities_are_stored_and_events_logged(env, sessions):
    o = open_ok(sessions, pair_code=sessions.pair_code, mode="wired_tether")
    caps = sessions.caps(o.session.cam)
    assert caps["sdk"] == 33 and caps["camera"]["hw_level"] == "FULL" and caps["cpu"]["cores"] == 8
    sessions.close(o.session.sid)
    o2 = open_ok(sessions, token=o.token, mode="wireless")
    keys = [(e.key, e.value) for e in reversed(env.events.query(type="camera"))]
    assert ("online", "true") in keys and ("online", "false") in keys and ("link_mode", "wireless") in keys
    assert sessions.by_cam(o2.session.cam) is o2.session


def status(mode="wireless", app="tracking", level=0) -> Status:
    from conftest import make_hello as mh
    return Status(1, app, "boottime", mh(mode=mode).link, 30.0, 30.0, (1280, 720), {"pipeline": StageStats(1.0, 2.0)},
                  {}, 0, 0, 3000000, 800, "locked", 10.0, None, ThermalState(0, 0.4, level, None if level == 0 else "hot"),
                  PerfState(*(None,) * 6), None, None, 3)


def test_status_changes_are_logged(env, sessions):
    o = open_ok(sessions, pair_code=sessions.pair_code)
    s = o.session
    sessions.on_status(s, status())
    n = env.events.last_id
    sessions.on_status(s, status())
    assert env.events.last_id == n                    # nothing changed
    sessions.on_status(s, status(app="benchmark"))
    sessions.on_status(s, status(app="benchmark", level=1))
    sessions.on_status(s, status(mode="wired_adb", app="benchmark", level=1))
    got = [(e.key, e.value, e.prev) for e in reversed(env.events.query(type="camera", since_id=n))]
    assert got == [("app_mode", "benchmark", "tracking"), ("thermal_level", "1", "0"), ("link_mode", "wired_adb", "wireless")]
    assert s.app_mode == "benchmark" and s.link_mode == "wired_adb"


def test_tick_logs_sync_changes_and_a_status_sample_every_minute(env, sessions):
    o = open_ok(sessions, pair_code=sessions.pair_code)
    s = o.session
    sessions.on_status(s, status())
    t = env.clock.mono_ns()
    s.clock_sync.add(t, t, t, t + 1_000_000)
    n = env.events.last_id
    sessions.tick()
    assert [e.key for e in env.events.query(since_id=n)] == ["sync"]
    env.clock.advance_s(61)
    sessions.tick()
    keys = [e.key for e in reversed(env.events.query(since_id=n))]
    assert keys == ["sync", "sync", "status_sample"]
    env.clock.advance_s(10)
    n2 = env.events.last_id
    sessions.tick()
    assert env.events.last_id == n2
