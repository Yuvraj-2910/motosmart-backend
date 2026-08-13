"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.db import SessionLocal, dispose_engine
from app.routers import api_router
from app.schemas.common import HealthResponse

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "Starting %s (env=%s, region=%s)",
        settings.APP_NAME,
        settings.ENVIRONMENT,
        settings.AWS_REGION,
    )
    if settings.dev_auth_enabled:
        logger.warning(
            "AUTH_DEV_MODE is on - the X-Dev-User header bypasses Cognito verification. "
            "Never enable this outside local development."
        )
    yield
    await dispose_engine()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "REST API for the YMSLI Smart Dealer Enquiry App (Hackathon PS-06).\n\n"
        "Authentication: the Flutter client signs in against Cognito (USER_AUTH / OTP) "
        "and sends the resulting JWT as `Authorization: Bearer <token>`. This service "
        "verifies tokens but never issues them.\n\n"
        "`/public/*` endpoints are intentionally unauthenticated - they power the guest "
        "browsing funnel."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Tag every request with an ID and log its latency.

    The ID comes back in `X-Request-ID` so a mobile bug report can be tied to a
    specific log line.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %s (%.1fms) [%s]",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Never leak SQL or connection strings to a client."""
    logger.exception("Database error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "A database error occurred. Please retry."},
    )


@app.get("/health", response_model=HealthResponse, tags=["health"], summary="Liveness probe")
async def health() -> HealthResponse:
    """Checks the database round-trip as well as process liveness.

    Returns 200 with `database: "down"` rather than failing, so a load balancer
    sees the process is up while monitoring still catches the DB problem.
    """
    db_state = "down"
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_state = "up"
    except Exception as exc:  # noqa: BLE001 - deliberately broad: a health
        # check must never 500 because the DB driver raised something that
        # isn't (yet) wrapped as SQLAlchemyError, e.g. a raw ConnectionRefusedError
        # from asyncpg during initial connect.
        logger.warning("Health check database probe failed: %s", exc)

    return HealthResponse(
        status="ok", environment=settings.ENVIRONMENT, database=db_state
    )


app.include_router(api_router, prefix=settings.API_V1_PREFIX)
