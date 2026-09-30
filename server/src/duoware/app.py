"""FastAPI application factory. Services (registry, ingest, world model, layout, broadcaster) are built on first
use and started in the lifespan; `GET /api/health` works without them (the M0 health test relies on that)."""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from duoware import __version__
from duoware.api import install
from duoware.clock import Clock
from duoware.services import Services
from duoware.settings import REPO_ROOT, Settings, load_settings

DEFAULT_DASHBOARD_DIR = REPO_ROOT / "dashboard" / "dist"     # DECISIONS.md D16: one port in production


def create_app(settings: Settings | None = None, mode: str = "hardware", clock: Clock | None = None,
               interfaces_fn: Callable[[], list[tuple[str, str]]] | None = None,
               dashboard_dir: Path | None = None) -> FastAPI:
    """mode: "hardware" or "sim" (PROTOCOL.md §7.1 /api/health)."""
    holder: dict[str, Services] = {}

    def get_services() -> Services:
        if "sv" not in holder:
            holder["sv"] = Services.build(settings or load_settings(), clock, mode, interfaces_fn)
        return holder["sv"]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        sv = get_services()
        await sv.start()
        try:
            yield
        finally:
            await sv.stop()
            holder.pop("sv", None)

    app = FastAPI(title="DUO-WARE 2", version=__version__, lifespan=lifespan)
    app.state.started = time.monotonic()
    app.state.mode = mode
    app.state.get_services = get_services
    install(app)
    dist = dashboard_dir if dashboard_dir is not None else DEFAULT_DASHBOARD_DIR
    if dist.is_dir() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")     # last: the API routes match first
    return app
