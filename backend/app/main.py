from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.orchestrator import Orchestrator
from app.api import chat, health, metadata
from app.api.rate_limit import RateLimiter
from app.config import Settings, settings as default_settings

logger = logging.getLogger("uzex.api")

API_PREFIX = "/api/v1"
_REQUEST_ID = re.compile(r"[^A-Za-z0-9._:-]")


def _clean_request_id(value: str | None) -> str:
    if value:
        cleaned = _REQUEST_ID.sub("", value.strip())[:128]
        if cleaned:
            return cleaned
    return f"req_{uuid4().hex}"


def create_app(
    settings: Settings | None = None,
    orchestrator: Orchestrator | None = None,
    rate_limit_per_minute: int | None = None,
) -> FastAPI:
    active_settings = settings or default_settings
    app = FastAPI(
        title="UZEX Data Agent",
        version=health.VERSION,
        description=(
            "Evidence-based analytics over the provided UZEX spot dataset. "
            "No free-form SQL and no client-side LLM credentials."
        ),
    )

    app.state.settings = active_settings
    app.state.orchestrator = orchestrator or Orchestrator(settings=active_settings)
    app.state.rate_limiter = RateLimiter(
        rate_limit_per_minute or active_settings.rate_limit_per_minute
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[object]],
    ) -> object:
        request_id = _clean_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or _clean_request_id(
            None
        )
        logger.warning(
            "unhandled_api_failure request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "UNVERIFIED",
                "answer": (
                    "Не могу достоверно подтвердить ответ по загруженным данным. "
                    "Попробуйте повторить запрос позже."
                ),
                "data": None,
                "evidence": None,
                "clarification": None,
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(metadata.router, prefix=API_PREFIX)
    return app


app = create_app()
