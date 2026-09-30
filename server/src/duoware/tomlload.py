"""Strict TOML -> frozen dataclass loader used by settings.py and the simulator scenario: an unknown key, a missing key
or a value of the wrong type raises `SettingsError` naming the file, the key path and the expected type."""

from __future__ import annotations

import tomllib
import types
import typing
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any


class SettingsError(Exception):
    """A config file is wrong. The message names the file, the key path and what was expected."""


# ---------------------------------------------------------------------------------------- strict builder


def _type_name(tp: Any) -> str:
    return getattr(tp, "__name__", None) if isinstance(tp, type) else str(tp).replace("typing.", "")


def _convert(tp: Any, value: Any, path: str) -> Any:
    origin = typing.get_origin(tp)
    if is_dataclass(tp):
        if not isinstance(value, dict):
            raise SettingsError(f"{path}: expected a table, got {type(value).__name__}")
        return _build(tp, value, path)
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(value, list):
            raise SettingsError(f"{path}: expected an array, got {type(value).__name__}")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_convert(args[0], v, f"{path}[{i}]") for i, v in enumerate(value))
        if len(value) != len(args):
            raise SettingsError(f"{path}: expected an array of {len(args)} values, got {len(value)}")
        return tuple(_convert(a, v, f"{path}[{i}]") for i, (a, v) in enumerate(zip(args, value)))
    if origin is dict:
        _, vt = typing.get_args(tp)
        if not isinstance(value, dict):
            raise SettingsError(f"{path}: expected a table, got {type(value).__name__}")
        return {str(k): _convert(vt, v, f"{path}.{k}") for k, v in value.items()}
    if origin in (typing.Union, types.UnionType):
        raise SettingsError(f"{path}: unsupported union type in settings.py")
    if tp is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"{path}: expected bool, got {value!r}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{path}: expected int, got {value!r}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{path}: expected a number, got {value!r}")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise SettingsError(f"{path}: expected str, got {value!r}")
        return value
    raise SettingsError(f"{path}: unsupported type {_type_name(tp)} in settings.py")


def _build(cls: type, data: dict, path: str) -> Any:
    hints = typing.get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    for key in data:
        if key not in names:
            raise SettingsError(f"{path}.{key}: unknown key (expected one of {sorted(names)})")
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            raise SettingsError(f"{path}.{f.name}: missing key (expected {_type_name(hints[f.name])})")
        kwargs[f.name] = _convert(hints[f.name], data[f.name], f"{path}.{f.name}")
    return cls(**kwargs)


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as e:
        raise SettingsError(f"{path}: file not found") from e
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path}: invalid TOML ({e})") from e


def load_toml_as(cls: type, path: Path) -> Any:
    """Strictly loads one TOML file into the frozen dataclass `cls` (used by the simulator scenario too)."""
    return _load(cls, path)


def _load(cls: type, path: Path) -> Any:
    try:
        return _build(cls, _read_toml(path), path.name)
    except SettingsError as e:
        if str(e).startswith(path.name):
            raise SettingsError(f"{path}: {str(e)[len(path.name):].lstrip('.')}") from None
        raise


