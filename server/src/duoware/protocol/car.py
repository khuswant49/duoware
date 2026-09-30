"""Car line protocol (PROTOCOL.md §5): ASCII lines, at most 31 characters before `\\n`.

`encode_*` build what the server sends; `parse_car_line` reads what a car sends; `parse_command_line` is
the car side (used by the simulated cars) and raises the exact error codes of §5.2-5.4.
"""

import re
from dataclasses import dataclass

from duoware.protocol import ProtocolError

MAX_LINE = 31                 # PROTOCOL.md §5.1: the Uno's buffer is 32 bytes
MAX_REPLY_LINE = 64           # PROTOCOL.md §5.5: a car line over 64 characters is counted and ignored
PWM_MAX = 255                 # PROTOCOL.md §5.2: M l r in -255..255
TTL_MAX_MS = 500              # PROTOCOL.md §5.2: larger ttl values are clamped to 500
PING_MAX = 65535              # PROTOCOL.md §5.2: P n in 0..65535
PROTO_VERSION = 1             # PROTOCOL.md §5.3: `proto` in ID
RESET_REASONS = ("power", "brownout", "watchdog", "software", "external", "unknown")   # PROTOCOL.md §5.3
ERROR_CODES = ("parse", "range", "long", "unknown")                                     # PROTOCOL.md §5.3

_INT = re.compile(r"-?[0-9]+")
_UINT = re.compile(r"[0-9]+")


# ----------------------------------------------------------------------------------------- server -> car


def _line(text: str) -> bytes:
    if len(text) > MAX_LINE:
        raise ProtocolError("long", text)
    return text.encode("ascii") + b"\n"


def encode_move(left: int, right: int, ttl_ms: int) -> bytes:
    if not (-PWM_MAX <= left <= PWM_MAX and -PWM_MAX <= right <= PWM_MAX and 1 <= ttl_ms <= TTL_MAX_MS):
        raise ProtocolError("range", f"M {left} {right} {ttl_ms}")
    return _line(f"M {left} {right} {ttl_ms}")


def encode_stop() -> bytes:
    return _line("S")


def encode_ping(n: int) -> bytes:
    if not 0 <= n <= PING_MAX:
        raise ProtocolError("range", f"P {n}")
    return _line(f"P {n}")


def encode_identify() -> bytes:
    return _line("?")


def encode_led(on: bool) -> bytes:
    return _line(f"L {1 if on else 0}")


@dataclass(frozen=True)
class Move:
    left: int
    right: int
    ttl_ms: int


@dataclass(frozen=True)
class Stop:
    pass


@dataclass(frozen=True)
class Ping:
    n: int


@dataclass(frozen=True)
class Identify:
    pass


@dataclass(frozen=True)
class Led:
    on: bool


def _int(token: str, unsigned: bool = False) -> int:
    if not (_UINT if unsigned else _INT).fullmatch(token):
        raise ProtocolError("parse", token)
    return int(token)


def parse_command_line(line: str | bytes) -> Move | Stop | Ping | Identify | Led:
    """A command as a car receives it (without the `\\n`; a trailing `\\r` is ignored).

    Raises `ProtocolError` with code `long`, `parse`, `range` or `unknown` (PROTOCOL.md §5.4)."""
    text = line.decode("ascii", "replace") if isinstance(line, bytes) else line
    text = text.rstrip("\r\n")
    if len(text) > MAX_LINE:
        raise ProtocolError("long", f"{len(text)} characters")
    tokens = text.split(" ")
    cmd, args = tokens[0], tokens[1:]
    if cmd == "M":
        if len(args) != 3:
            raise ProtocolError("parse", text)
        left, right, ttl = (_int(a) for a in args)
        if not (-PWM_MAX <= left <= PWM_MAX and -PWM_MAX <= right <= PWM_MAX) or ttl <= 0:
            raise ProtocolError("range", text)
        return Move(left, right, min(ttl, TTL_MAX_MS))
    if cmd == "S":
        if args:
            raise ProtocolError("parse", text)
        return Stop()
    if cmd == "?":
        if args:
            raise ProtocolError("parse", text)
        return Identify()
    if cmd == "P":
        if len(args) != 1:
            raise ProtocolError("parse", text)
        n = _int(args[0], unsigned=True)
        if n > PING_MAX:
            raise ProtocolError("range", text)
        return Ping(n)
    if cmd == "L":
        if len(args) != 1:
            raise ProtocolError("parse", text)
        s = _int(args[0])
        if s not in (0, 1):
            raise ProtocolError("range", text)
        return Led(bool(s))
    raise ProtocolError("unknown", text)


# ----------------------------------------------------------------------------------------- car -> server


@dataclass(frozen=True)
class IdReply:
    name: str
    fw: str
    proto: int
    caps: tuple[str, ...]
    uptime_s: int


@dataclass(frozen=True)
class PingReply:
    n: int


@dataclass(frozen=True)
class StopAck:
    pass


@dataclass(frozen=True)
class TtlExpired:
    count: int


@dataclass(frozen=True)
class Boot:
    name: str
    fw: str
    reset: str


@dataclass(frozen=True)
class CarError:
    code: str


def parse_car_line(line: str | bytes) -> IdReply | PingReply | StopAck | TtlExpired | Boot | CarError:
    """A line from a car (PROTOCOL.md §5.3). Raises `ProtocolError("bad")` for anything else."""
    text = (line.decode("ascii", "replace") if isinstance(line, bytes) else line).rstrip("\r\n")
    if len(text) > MAX_REPLY_LINE:
        raise ProtocolError("bad", "line too long")
    t = text.split(" ")
    try:
        if t[0] == "ID" and len(t) == 6:
            return IdReply(t[1], t[2], _int(t[3], True), tuple(c for c in t[4].split(",") if c), _int(t[5], True))
        if t[0] == "P" and len(t) == 2:
            return PingReply(_int(t[1], True))
        if text == "OK S":
            return StopAck()
        if t[0] == "X" and len(t) == 2:
            return TtlExpired(_int(t[1], True))
        if t[0] == "BOOT" and len(t) == 4 and t[3] in RESET_REASONS:
            return Boot(t[1], t[2], t[3])
        if t[0] == "E" and len(t) == 2 and t[1] in ERROR_CODES:
            return CarError(t[1])
    except ProtocolError:
        pass
    raise ProtocolError("bad", text)


def _reply(text: str) -> bytes:
    if len(text) > MAX_REPLY_LINE:
        raise ProtocolError("long", text)
    return text.encode("ascii") + b"\n"


def encode_id(name: str, fw: str, proto: int, caps: tuple[str, ...], uptime_s: int) -> bytes:
    return _reply(f"ID {name} {fw} {proto} {','.join(caps)} {uptime_s}")


def encode_ping_reply(n: int) -> bytes:
    return _reply(f"P {n}")


def encode_stop_ack() -> bytes:
    return _reply("OK S")


def encode_ttl_expired(count: int) -> bytes:
    return _reply(f"X {count % (PING_MAX + 1)}")


def encode_boot(name: str, fw: str, reset: str) -> bytes:
    return _reply(f"BOOT {name} {fw} {reset}")


def encode_car_error(code: str) -> bytes:
    return _reply(f"E {code}")
