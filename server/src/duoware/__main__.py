"""Run the server: python -m duoware [--sim] [--host 0.0.0.0] [--port 8000]"""

import argparse

import uvicorn

from duoware.app import create_app


def main() -> None:
    p = argparse.ArgumentParser(prog="duoware", description="DUO-WARE 2 server")
    p.add_argument("--host", default="0.0.0.0", help="bind address (phones must reach it; see DECISIONS.md D17)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--sim", action="store_true", help="simulation mode: never opens Bluetooth or serial ports")
    args = p.parse_args()
    uvicorn.run(create_app(mode="sim" if args.sim else "hardware"), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
