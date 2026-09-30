"""Pairing codes and the failure lock-out, ported from DUO-WARE 1 control/auth.py (DECISIONS.md D6)."""

import hmac
import secrets
import threading

from duoware.clock import Clock

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"     # no 0/O or 1/I: easy to read aloud and type
CODE_GROUP = 4                                         # "K7QX-M2": a dash after the first four characters
PAIR_CODE_LENGTH = 6                                   # PROTOCOL.md §4.3 example "K7QX-M2"


def generate_code(n: int = PAIR_CODE_LENGTH) -> str:
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))
    return f"{code[:CODE_GROUP]}-{code[CODE_GROUP:]}" if n > CODE_GROUP else code


def normalize(code: str) -> str:
    """PROTOCOL.md §4.3: upper-case, letters and digits only."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def codes_match(given: str, expected: str) -> bool:
    return bool(normalize(expected)) and hmac.compare_digest(normalize(given).encode(), normalize(expected).encode())


class LoginThrottle:
    """Locks an address out after `max_failures` wrong codes, for `lock_s` seconds."""

    def __init__(self, max_failures: int, lock_s: float, clock: Clock) -> None:
        self.max_failures, self.lock_s, self._clock = max_failures, lock_s, clock
        self._fails: dict[str, tuple[int, int]] = {}          # addr -> (failures, locked_until_ns)
        self._lock = threading.Lock()

    def locked_for_s(self, addr: str) -> float:
        now = self._clock.mono_ns()
        with self._lock:
            n, until = self._fails.get(addr, (0, 0))
            return max(0.0, (until - now) / 1e9) if n >= self.max_failures else 0.0

    def failure(self, addr: str) -> None:
        with self._lock:
            n, until = self._fails.get(addr, (0, 0))
            if n >= self.max_failures and self._clock.mono_ns() >= until:
                n = 0                                          # the lock ran out: start counting again
            n += 1
            self._fails[addr] = (n, self._clock.mono_ns() + int(self.lock_s * 1e9) if n >= self.max_failures else 0)

    def success(self, addr: str) -> None:
        with self._lock:
            self._fails.pop(addr, None)
