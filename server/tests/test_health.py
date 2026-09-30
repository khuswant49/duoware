from fastapi.testclient import TestClient

from duoware import PROTOCOL_VERSION
from duoware.app import create_app


def test_health_reports_ok_and_protocol_version():
    client = TestClient(create_app(mode="sim"))
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["proto"] == PROTOCOL_VERSION
    assert body["mode"] == "sim"
