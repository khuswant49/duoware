"""
DUO-WARE access control (R5).

  Dashboard (Flask, port 5000)
    - Requests from this laptop (127.0.0.1 / ::1) are allowed without a login: whoever sits at the
      laptop already controls it. Set DUO_AUTH_LOCAL=true to require the login there too.
    - Requests from other devices (hotspot / LAN) must log in with the ACCESS CODE first
      (session cookie). API calls without a session get HTTP 401; pages redirect to /login.
    - The E-stop (POST /api/control/stop) always works without a login: stopping cannot harm, and a
      person next to the car must always be able to stop it. Resume needs a login.
    - 5 wrong codes from one address lock that address out for 60 s.
  Phone camera page (HTTPS/WebSocket, port 5443)
    - The frame WebSocket needs the CAMERA KEY (link: https://<laptop>:5443/?cam=1&key=...), so a
      random device on the network cannot take over a camera slot. The dashboard shows full links.

Codes come from .env (DUO_ACCESS_CODE, DUO_CAMERA_KEY); if empty, fresh ones are generated at
every start and printed in the console. DUO_AUTH=off disables all of this (development only).
"""

import hmac
import os
import secrets
import threading
import time
from typing import Dict, Tuple

_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"          # No 0/O, 1/I: easy to read aloud and type


def generate_code(n: int = 8) -> str:
    code = "".join(secrets.choice(_ALPHABET) for _ in range(n))
    return f"{code[:4]}-{code[4:]}" if n == 8 else code


def normalize(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def codes_match(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(normalize(given).encode(), normalize(expected).encode())


LOCAL_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


class LoginThrottle:
    """Locks an address out after `max_failures` wrong codes, for `lock_sec`."""

    def __init__(self, max_failures: int = 5, lock_sec: float = 60.0):
        self.max_failures, self.lock_sec = max_failures, lock_sec
        self._fails: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, addr: str, now: float = None) -> float:
        now = time.time() if now is None else now
        with self._lock:
            n, until = self._fails.get(addr, (0, 0.0))
            return max(0.0, until - now) if n >= self.max_failures else 0.0

    def failure(self, addr: str, now: float = None):
        now = time.time() if now is None else now
        with self._lock:
            n, _ = self._fails.get(addr, (0, 0.0))
            n += 1
            self._fails[addr] = (n, now + self.lock_sec if n >= self.max_failures else 0.0)

    def success(self, addr: str):
        with self._lock:
            self._fails.pop(addr, None)


def env_flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None or v == "" else v.lower() in ("1", "true", "yes", "on")
