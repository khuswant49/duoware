"""Small strict JSON field readers shared by the phone codecs. Every failure is `ProtocolError("bad")`."""

from __future__ import annotations

import math
from typing import Any

from duoware.protocol import ProtocolError


def bad(msg: str) -> ProtocolError:
    return ProtocolError("bad", msg)


def is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class Obj:
    """Read-only view of a JSON object with typed getters. `path` only makes error messages useful."""

    def __init__(self, data: Any, path: str = "") -> None:
        if not isinstance(data, dict):
            raise bad(f"{path or 'message'}: expected an object")
        self.d = data
        self.path = path

    def _err(self, key: str, what: str) -> ProtocolError:
        return bad(f"{self.path + '.' if self.path else ''}{key}: expected {what}")

    def has(self, key: str) -> bool:
        return key in self.d

    def int(self, key: str) -> int:
        v = self.d.get(key)
        if not is_int(v):
            raise self._err(key, "int")
        return v

    def num(self, key: str) -> float:
        v = self.d.get(key)
        if not is_num(v):
            raise self._err(key, "number")
        return float(v)

    def str(self, key: str) -> str:
        v = self.d.get(key)
        if not isinstance(v, str):
            raise self._err(key, "string")
        return v

    def bool(self, key: str) -> bool:
        v = self.d.get(key)
        if not isinstance(v, bool):
            raise self._err(key, "bool")
        return v

    def opt_int(self, key: str) -> int | None:
        v = self.d.get(key)
        if v is None:
            return None
        if not is_int(v):
            raise self._err(key, "int or null")
        return v

    def opt_num(self, key: str) -> float | None:
        v = self.d.get(key)
        if v is None:
            return None
        if not is_num(v):
            raise self._err(key, "number or null")
        return float(v)

    def opt_str(self, key: str) -> str | None:
        v = self.d.get(key)
        if v is None:
            return None
        if not isinstance(v, str):
            raise self._err(key, "string or null")
        return v

    def opt_bool(self, key: str) -> bool | None:
        v = self.d.get(key)
        if v is None:
            return None
        if not isinstance(v, bool):
            raise self._err(key, "bool or null")
        return v

    def obj(self, key: str) -> "Obj":
        return Obj(self.d.get(key), f"{self.path + '.' if self.path else ''}{key}")

    def opt_obj(self, key: str) -> "Obj | None":
        v = self.d.get(key)
        return None if v is None else self.obj(key)

    def list(self, key: str) -> list:
        v = self.d.get(key)
        if not isinstance(v, list):
            raise self._err(key, "array")
        return v

    def opt_list(self, key: str) -> list | None:
        v = self.d.get(key)
        if v is None:
            return None
        return self.list(key)

    def int_list(self, key: str) -> list[int]:
        v = self.list(key)
        if not all(is_int(x) for x in v):
            raise self._err(key, "array of int")
        return v

    def num_list(self, key: str, n: int | None = None) -> list[float]:
        v = self.list(key)
        if not all(is_num(x) for x in v) or (n is not None and len(v) != n):
            raise self._err(key, f"array of {n if n is not None else 'any number of'} numbers")
        return [float(x) for x in v]

    def opt_num_list(self, key: str, n: int | None = None) -> list[float] | None:
        return None if self.d.get(key) is None else self.num_list(key, n)
