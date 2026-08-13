"""boto3 client factory.

Clients are created once per process and cached. Creating a boto3 client is
relatively expensive (it parses service JSON models), so we never build them
per-request. Clients are thread-safe for calls; because boto3 is blocking, all
call sites wrap invocations in `run_in_threadpool` (see `app.core.aws.call`).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger(__name__)

_BOTO_CONFIG = Config(
    region_name=settings.AWS_REGION,
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=30,
)


@lru_cache
def _client(service: str) -> Any:
    logger.debug("Creating boto3 client for %s", service)
    return boto3.client(service, config=_BOTO_CONFIG)


def cognito_client() -> Any:
    return _client("cognito-idp")


def s3_client() -> Any:
    return _client("s3")


def ses_client() -> Any:
    return _client("ses")


def sns_client() -> Any:
    return _client("sns")


def bedrock_client() -> Any:
    return _client("bedrock-runtime")


async def call(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking boto3 call in a worker thread.

    Keeps the event loop free. Callers are responsible for catching
    `botocore.exceptions.ClientError` — every AWS integration in this service
    degrades gracefully rather than failing the core flow.
    """
    return await run_in_threadpool(lambda: fn(*args, **kwargs))


# --- FastAPI dependencies -------------------------------------------------
# Injected with Depends(...) so routers/services never build clients inline
# and tests can override them.


def get_cognito() -> Any:
    return cognito_client()


def get_s3() -> Any:
    return s3_client()


def get_ses() -> Any:
    return ses_client()


def get_sns() -> Any:
    return sns_client()


def get_bedrock() -> Any:
    return bedrock_client()
