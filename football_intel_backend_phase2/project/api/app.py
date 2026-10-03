"""Application factory (composition root). Run: uvicorn api.app:create_app --factory"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import API_BASE_PATH, CONTRACT_VERSION
from api.config import ApiConfig
from api.domain.enums import ProblemCode
from api.errors import ApiError
from api.routers import freshness, health, player, search
from api.schemas.common import Problem
from api.services.container import start_services

log = logging.getLogger("api")
PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem(request: Request, code: ProblemCode, status: int, title: str, detail: str | None, errors=None,
             redact_instance: bool = False) -> JSONResponse:
    body = Problem(type="about:blank", title=title, status=status, code=code.value, detail=detail,
                   instance=None if redact_instance else request.url.path, errors=errors or None).model_dump(mode="json")
    if not errors:
        body.pop("errors")                                  # omitted, not null (contract: array when present)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE)


def _reason(error: dict) -> str:
    """Stable, framework-independent wording for the `errors[].reason` field."""
    ctx = error.get("ctx") or {}
    kind = error.get("type", "")
    if kind == "missing":
        return "is required"
    if kind == "string_too_short":
        return f"length must be at least {ctx.get('min_length')} characters"
    if kind == "string_too_long":
        return f"length must be at most {ctx.get('max_length')} characters"
    if kind == "greater_than_equal":
        return f"must be at least {ctx.get('ge')}"
    if kind == "less_than_equal":
        return f"must be at most {ctx.get('le')}"
    if kind == "enum":
        return f"must be one of {ctx.get('expected')}"
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return "must be an integer"
    return str(error.get("msg", "invalid value"))


def create_app(config: ApiConfig | None = None) -> FastAPI:
    config = config or ApiConfig.from_env()
    logging.basicConfig(level=config.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = start_services(config.db_path)       # opens the DB READ-ONLY, verifies invariants
        try:
            yield
        finally:
            app.state.services.db.close()

    app = FastAPI(
        title="Football Player Intelligence API", version=CONTRACT_VERSION, lifespan=lifespan,
        docs_url="/docs" if config.enable_docs else None, redoc_url=None,
        openapi_url="/openapi.json" if config.enable_docs else None,
    )
    app.state.config = config
    if config.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins), allow_methods=["GET"], allow_headers=[])

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _problem(request, exc.code, exc.status, exc.title, exc.detail, exc.errors, exc.redact_instance)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errs = [{"parameter": ".".join(str(p) for p in e["loc"][1:]) or "request", "reason": _reason(e)} for e in exc.errors()]
        return _problem(request, ProblemCode.INVALID_PARAMETER, 400, "Invalid parameter", "One or more parameters are invalid.", errs)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error (request_id=%s)", getattr(request.state, "request_id", "?"))
        return _problem(request, ProblemCode.INTERNAL_ERROR, 500, "Internal error", "An unexpected error occurred.")

    app.include_router(health.router, prefix=API_BASE_PATH)
    app.include_router(freshness.router, prefix=API_BASE_PATH)
    app.include_router(search.router, prefix=API_BASE_PATH)    # before /players/{player_id}: "search" is not an id
    app.include_router(player.router, prefix=API_BASE_PATH)
    return app
