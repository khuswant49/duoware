"""Error responses in the PROTOCOL.md §7.1 shape: {"error": {"code", "message", "details"}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from duoware.errors import DuoError


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DuoError)
    async def duo_error(_: Request, e: DuoError) -> JSONResponse:
        return JSONResponse(error_body(e.code, e.message, e.details), status_code=e.http)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, e: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in err["loc"][1:]) for err in e.errors()]
        return JSONResponse(error_body("validation", "The request is not valid.", {"fields": fields}), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return JSONResponse(error_body(code, str(e.detail)), status_code=e.status_code)
