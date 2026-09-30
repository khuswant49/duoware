"""Step 4 / A4: E-stop latch and the motion-change rule."""

import pytest

from duoware.errors import DuoError
from duoware.safety import NoCarsMoving


def test_estop_is_latched_and_idempotent(env):
    s = env.safety
    assert not s.estop and s.estop_since_wall_ms is None
    assert s.stop_all("local") is True
    assert s.estop and s.estop_by == "local" and s.estop_since_wall_ms == env.clock.wall_ms()
    n = env.events.last_id
    assert s.stop_all("local") is False and env.events.last_id == n
    assert s.resume("local") is True and not s.estop and s.estop_by is None
    assert s.resume("local") is False
    keys = [(e.key, e.value) for e in reversed(env.events.query(type="operator"))]
    assert keys == [("estop", "latched"), ("estop", "released")]


def test_motion_change_rule(env):
    s = env.safety
    assert s.motion_change_allowed() == (True, [])
    env.mover.moving = ["DUO-B"]
    assert s.motion_change_allowed() == (False, ["DUO-B"])
    with pytest.raises(DuoError) as e:
        s.require_motion_allowed()
    assert e.value.code == "requires_stopped" and e.value.http == 409 and e.value.details["moving"] == ["DUO-B"]
    s.stop_all("local")
    assert s.motion_change_allowed() == (True, ["DUO-B"])
    s.require_motion_allowed()


def test_default_provider_reports_no_moving_cars():
    assert NoCarsMoving().moving_cars() == []
