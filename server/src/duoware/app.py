"""FastAPI application factory.

M0 skeleton: only the health endpoint. Later milestones add routers under duoware/api/ and
start background services (phone ingest, car links, state broadcaster) in the lifespan.
"""

import time

from fastapi import FastAPI

from duoware import PROTOCOL_VERSION, __version__


def create_app(mode: str = "hardware") -> FastAPI:
    """mode: "hardware" or "sim" (PROTOCOL.md §7.1 /api/health)."""
    app = FastAPI(title="DUO-WARE 2", version=__version__)
    started = time.monotonic()

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "proto": PROTOCOL_VERSION,
            "mode": mode,
            "uptime_s": round(time.monotonic() - started, 1),
        }

    return app
