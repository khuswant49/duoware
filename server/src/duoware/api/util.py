from typing import Any

from fastapi import Request

from duoware.errors import DuoError


async def json_body(request: Request, allow_empty: bool = False) -> Any:
    raw = await request.body()
    if not raw.strip():
        if allow_empty:
            return {}
        raise DuoError("validation", 400, "The request needs a JSON body.")
    try:
        import json
        return json.loads(raw)
    except ValueError as e:
        raise DuoError("validation", 400, "The request body is not valid JSON.") from e


def require_int(body: Any, key: str) -> int:
    v = body.get(key) if isinstance(body, dict) else None
    if isinstance(v, bool) or not isinstance(v, int):
        raise DuoError("validation", 400, f"{key} (an integer) is required.", {"fields": [key]})
    return v
