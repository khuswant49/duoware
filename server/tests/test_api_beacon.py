"""M2 step 3 / B5: GET /api/beacon (PROTOCOL.md §4.2, §7.1, §7.2)."""

import json

import httpx2
import pytest
from conftest import make_app
from fastapi.testclient import TestClient
from harness import ServerHarness

from duoware.protocol.phone import parse_beacon


async def test_beacon_over_http_names_the_arrival_address_and_bound_ports(tmp_path):
    async with ServerHarness(tmp_path / "data") as h:
        async with httpx2.AsyncClient(base_url=h.url, timeout=5) as http:
            r = await http.get("/api/beacon")
        server_id, frames, tcp = h.sv.db.server_id, h.sv.ports["frames"], h.sv.ports["tcp"]
    assert r.status_code == 200
    b = parse_beacon(json.dumps(r.json()).encode())            # exactly the §4.2 message
    assert b.host == "127.0.0.1"                                # the address the request arrived on
    assert b.server_id == server_id
    assert (b.http_port, b.frames_port, b.frames_tcp_port) == (h.http_port, frames, tcp)
    assert b.frames_port != 0 and b.frames_tcp_port != 0        # the bound ports, not the configured 0


def test_beacon_is_reachable_from_a_non_local_address(tmp_path):
    app = make_app(tmp_path, local=False)                       # TestClient's host "testclient" counts as remote
    with TestClient(app) as c:
        assert c.get("/api/tags").status_code == 403            # the guard is active ...
        r = c.get("/api/beacon")                                # ... but the beacon is exempt (§7.1)
        assert r.status_code == 200 and r.json()["t"] == "beacon" and r.json()["v"] == 1


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_beacon_is_read_only(tmp_path, method):
    with TestClient(make_app(tmp_path)) as c:
        assert getattr(c, method)("/api/beacon").status_code == 405
