"""boto3 client factory.

Clients are created once per process and cached. Creating a boto3 client is
relatively expensive (it parses service JSON models), so we never build them
per-request. Clients are thread-safe for calls; because boto3 is blocking, all
call sites wrap invocations in `run_in_threadpool` (see `app.core.aws.call`).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger(__name__)


def _config_for(region: str) -> Config:
    return Config(
        region_name=region,
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=5,
        read_timeout=30,
    )


_BOTO_CONFIG = _config_for(settings.AWS_REGION)


@lru_cache
def _client(service: str, region: str | None = None) -> Any:
    """Cached boto3 client. `region` is only passed when a service needs to talk
    to a region other than AWS_REGION (Bedrock does — see `bedrock_client`)."""
    logger.debug("Creating boto3 client for %s in %s", service, region or settings.AWS_REGION)
    config = _BOTO_CONFIG if region is None else _config_for(region)
    return boto3.client(service, config=config)


def cognito_client() -> Any:
    return _client("cognito-idp")


def s3_client() -> Any:
    return _client("s3")


def ses_client() -> Any:
    return _client("ses")


def sns_client() -> Any:
    return _client("sns")


def transcribe_client() -> Any:
    return _client("transcribe")


def bedrock_client() -> Any:
    """Bedrock runtime, in its own region and optionally key-authenticated.

    A Bedrock API key is a bearer token that botocore picks up from
    `AWS_BEARER_TOKEN_BEDROCK`; there is no client kwarg for it, so the value is
    exported here before the client is built. Note it authenticates as the IAM
    principal that minted it, so it grants no permissions that principal lacks.
    """
    if settings.BEDROCK_API_KEY and not os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.BEDROCK_API_KEY
    return _client("bedrock-runtime", settings.bedrock_region)


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


def get_transcribe() -> Any:
    return transcribe_client()
