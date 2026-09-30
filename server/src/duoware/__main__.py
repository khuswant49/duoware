"""Run the server: python -m duoware [--sim] [--config-dir D] [--data-dir D] [--host H] [--port P]"""

import argparse
import logging
from pathlib import Path

import uvicorn

from duoware.app import create_app
from duoware.settings import load_settings

WS_PING_INTERVAL_S = 5       # PROTOCOL.md §4.3: the server pings every 5 s ...
WS_PING_TIMEOUT_S = 10       # ... and closes a session with no pong for 10 s


def main() -> None:
    p = argparse.ArgumentParser(prog="duoware", description="DUO-WARE 2 server")
    p.add_argument("--sim", action="store_true", help="simulation mode: never opens Bluetooth or serial ports")
    p.add_argument("--config-dir", type=Path, default=None, help="folder with the config/*.toml files")
    p.add_argument("--data-dir", type=Path, default=None, help="folder for state.db and events.db")
    p.add_argument("--host", default=None, help="bind address (default: server.toml; phones must reach it, D17)")
    p.add_argument("--port", type=int, default=None, help="HTTP port (default: server.toml)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.config_dir, args.data_dir)
    host = args.host or settings.server.http.host
    port = args.port or settings.server.http.port
    app = create_app(settings, mode="sim" if args.sim else "hardware")
    sv = app.state.get_services()
    sv.ports["http"] = port
    print(f"DUO-WARE 2 server ({'simulation' if args.sim else 'hardware'} mode)")
    print(f"  Pairing code for phones: {sv.sessions.pair_code}")
    print(f"  Dashboard:               http://127.0.0.1:{port}/")
    print(f"  Phones connect to:       ws://<this computer>:{port}/ws/phone (UDP {settings.server.udp.frames_port})")
    uvicorn.run(app, host=host, port=port, ws_ping_interval=WS_PING_INTERVAL_S, ws_ping_timeout=WS_PING_TIMEOUT_S)


if __name__ == "__main__":
    main()
