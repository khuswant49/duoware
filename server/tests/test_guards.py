"""Step 8 / A10: remote-address and Origin guards (PROTOCOL.md §7.1)."""

import pytest
from conftest import make_app
from fastapi.testclient import TestClient

GOOD = "http://localhost:8000"
BAD = "http://evil.example"

PROTECTED_GET = ["/api/config", "/api/tags", "/api/layout", "/api/venue-presets", "/api/cameras", "/api/pairing",
                 "/api/events"]


@pytest.fixture
def remote(tmp_path):
    """TestClient's host is "testclient": a remote address (no `client_host` override)."""
    with TestClient(make_app(tmp_path, local=False)) as c:
        yield c


@pytest.fixture
def local(tmp_path):
    with TestClient(make_app(tmp_path, local=True)) as c:
        yield c


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_remote_addresses_are_refused(remote, path):
    r = remote.get(path)
    assert r.status_code == 403 and r.json()["error"]["code"] == "remote_forbidden"
    assert r.json()["error"]["details"]["host"] == "testclient"


def test_remote_mutations_are_refused_too(remote):
    for method, path, body in [("post", "/api/control/resume", {"confirm": True}),
                               ("put", "/api/tags/5", {"role": "ignore", "expected_version": 0}),
                               ("delete", "/api/tags/5?expected_version=0", None),
                               ("post", "/api/layout/suggest", {}), ("post", "/api/venue-presets/sim_3x3/apply", {}),
                               ("put", "/api/camera-settings", {"iso": 100}), ("delete", "/api/pairing/x", None)]:
        r = getattr(remote, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["error"]["code"] == "remote_forbidden", path


def test_health_and_stop_all_are_exempt_for_remote_clients(remote):
    assert remote.get("/api/health").status_code == 200
    r = remote.post("/api/control/stop_all", json={})
    assert r.status_code == 200 and r.json() == {"estop": True}
    # stop_all also ignores the Origin check
    assert remote.post("/api/control/stop_all", json={}, headers={"Origin": BAD}).status_code == 200


def test_the_remote_dashboard_flag_lifts_the_local_only_rule(tmp_path):
    with TestClient(make_app(tmp_path, local=False, allow_remote=True)) as c:
        assert c.get("/api/tags").status_code == 200


def test_foreign_origin_is_refused_on_mutating_requests(local):
    for method, path, body in [("post", "/api/control/resume", {"confirm": True}),
                               ("put", "/api/tags/5", {"role": "ignore", "expected_version": 0}),
                               ("delete", "/api/tags/5?expected_version=0", None),
                               ("post", "/api/tags/batch", {"changes": []}),
                               ("post", "/api/venue-presets/sim_3x3/apply", {}),
                               ("put", "/api/layout/blocked", {"nodes": [], "edges": [], "expected_layout_version": 0})]:
        r = getattr(local, method)(path, headers={"Origin": BAD}, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin", path
        assert r.json()["error"]["details"]["origin"] == BAD
    # nothing happened
    assert local.get("/api/tags").json()["tags"] == []


def test_allowed_and_missing_origins_pass_and_reads_ignore_origin(local):
    body = {"role": "ignore", "expected_version": 0}
    assert local.put("/api/tags/5", json=body, headers={"Origin": GOOD}).status_code == 200
    assert local.put("/api/tags/6", json=body).status_code == 200                              # no Origin header at all
    assert local.put("/api/tags/7", json=body, headers={"Origin": "http://127.0.0.1:5173"}).status_code == 200
    assert local.get("/api/tags", headers={"Origin": BAD}).status_code == 200                  # GET is not checked


def test_bad_origin_is_checked_after_the_remote_rule(remote):
    r = remote.put("/api/tags/5", json={"role": "ignore", "expected_version": 0}, headers={"Origin": BAD})
    assert r.json()["error"]["code"] == "remote_forbidden"
