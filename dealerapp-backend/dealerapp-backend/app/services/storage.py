"""S3 object storage: presigned PUT for uploads, presigned GET for reads."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.core import aws
from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when S3 is unconfigured or a presign call fails."""


# Prefixes callers may write to. Anything else is rejected so a client can't
# choose an arbitrary key and scribble over unrelated objects.
ALLOWED_CATEGORIES = {
    "bike-images": "bike-images",
    "brochures": "brochures",
    "service-attachments": "service-attachments",
    "profile": "profile",
    # Short dictation clips for the voice-note transcription feature. Objects
    # here are transient - `services.voice` deletes them once transcribed.
    "voice-notes": "voice-notes",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_LEN = 100


def sanitize_filename(filename: str) -> str:
    """Strip path traversal and unsafe characters from a client filename."""
    base = filename.replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_NAME.sub("-", base).strip("-._") or "file"
    return cleaned[:MAX_FILENAME_LEN]


def build_key(category: str, filename: str) -> str:
    prefix = ALLOWED_CATEGORIES.get(category)
    if prefix is None:
        raise StorageError(
            f"Unknown category '{category}'. Allowed: {sorted(ALLOWED_CATEGORIES)}"
        )
    stamp = datetime.now(UTC).strftime("%Y/%m")
    return f"{prefix}/{stamp}/{uuid.uuid4().hex}-{sanitize_filename(filename)}"


def public_url(key: str) -> str:
    return f"https://{settings.S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"


async def presign_upload(
    *, filename: str, content_type: str, category: str = "bike-images"
) -> tuple[str, str, int]:
    """Return `(upload_url, key, expires_in)` for a direct browser/app PUT."""
    if not settings.S3_BUCKET:
        raise StorageError("S3_BUCKET is not configured")

    key = build_key(category, filename)
    expires = settings.S3_PRESIGN_EXPIRY_SECONDS
    try:
        client = aws.s3_client()
        url = await aws.call(
            client.generate_presigned_url,
            "put_object",
            Params={
                "Bucket": settings.S3_BUCKET,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("S3 presign upload failed: %s", exc)
        raise StorageError("Could not generate an upload URL") from exc

    return url, key, expires


async def put_object(
    *, key: str, data: bytes, content_type: str, bucket: str | None = None
) -> None:
    """Upload bytes the server already holds (as opposed to a presigned client PUT)."""
    bucket = bucket or settings.S3_BUCKET
    if not bucket:
        raise StorageError("S3_BUCKET is not configured")

    try:
        client = aws.s3_client()
        await aws.call(
            client.put_object,
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("S3 put_object failed: %s", exc)
        raise StorageError("Could not upload the object") from exc


async def delete_object(key: str, *, bucket: str | None = None) -> None:
    """Best-effort delete. Callers should not fail their request over this."""
    bucket = bucket or settings.S3_BUCKET
    if not bucket:
        return
    try:
        client = aws.s3_client()
        await aws.call(client.delete_object, Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("S3 delete_object failed for %s: %s", key, exc)


def _get_object_bytes_sync(client: Any, bucket: str, key: str) -> bytes:
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


async def get_object_bytes(key: str, *, bucket: str | None = None) -> bytes:
    bucket = bucket or settings.S3_BUCKET
    if not bucket:
        raise StorageError("S3_BUCKET is not configured")
    try:
        client = aws.s3_client()
        return await aws.call(_get_object_bytes_sync, client, bucket, key)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("S3 get_object failed for %s: %s", key, exc)
        raise StorageError("Could not read the object") from exc


async def presign_download(key: str) -> tuple[str, int]:
    """Return `(download_url, expires_in)` for a private object."""
    if not settings.S3_BUCKET:
        raise StorageError("S3_BUCKET is not configured")

    expires = settings.S3_PRESIGN_EXPIRY_SECONDS
    try:
        client = aws.s3_client()
        url = await aws.call(
            client.generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("S3 presign download failed: %s", exc)
        raise StorageError("Could not generate a download URL") from exc

    return url, expires
