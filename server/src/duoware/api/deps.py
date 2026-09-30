"""Shared request dependencies: the services, and the PROTOCOL.md §7.1 access guards.

Tests: Starlette's TestClient reports the client host "testclient", which counts as a REMOTE address here. The
guard tests rely on that; every other API test overrides `client_host` (see tests/test_api_*.py).
"""

from fastapi import Depends, Request

from duoware.errors import DuoError
from duoware.services import Services

LOCAL_HOSTS = {"127.0.0.1", "::1"}                 # PROTOCOL.md §7.1
MUTATING = {"POST", "PUT", "DELETE"}               # PROTOCOL.md §7.1 origin check


def get_services(request: Request) -> Services:
    return request.app.state.get_services()


def client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def local_only(host: str = Depends(client_host), sv: Services = Depends(get_services)) -> None:
    if host not in LOCAL_HOSTS and not sv.settings.server.access.allow_remote_dashboard:
        raise DuoError("remote_forbidden", 403, "The dashboard is only available on this computer.", {"host": host})


def origin_ok(request: Request, sv: Services = Depends(get_services)) -> None:
    origin = request.headers.get("origin")
    if request.method in MUTATING and origin is not None and origin not in sv.settings.server.access.allowed_origins:
        raise DuoError("bad_origin", 403, "This request comes from a page that is not allowed to control the server.",
                       {"origin": origin})


def dashboard_guard(_: None = Depends(local_only), __: None = Depends(origin_ok)) -> None:
    """Local-only plus the Origin check. Not applied to /api/health and POST /api/control/stop_all."""
