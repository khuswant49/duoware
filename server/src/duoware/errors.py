"""The error shape shared by the registry, layout and API (PROTOCOL.md §7.1)."""

from typing import Any


class DuoError(Exception):
    """`code`, HTTP status, message and `details`: rendered by api/errors.py as {"error": {...}}."""

    def __init__(self, code: str, http: int, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.http, self.message, self.details = code, http, message, details or {}
