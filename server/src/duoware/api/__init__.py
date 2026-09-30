"""HTTP and WebSocket routes. `install` wires every router and the error handlers into an app."""

from fastapi import FastAPI

from duoware.api import (
    cameras, config, control, dashboard_ws, events, health, layout, pairing, phone_ws, presets, tags,
)
from duoware.api.errors import install_error_handlers


def install(app: FastAPI) -> None:
    install_error_handlers(app)
    for module in (health, config, tags, layout, presets, cameras, pairing, events):
        app.include_router(module.router)
    app.include_router(control.open_router)
    app.include_router(control.router)
    app.include_router(phone_ws.router)
    app.include_router(dashboard_ws.router)
