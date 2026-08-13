"""S3 object storage: presigned PUT for uploads, presigned GET for reads."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

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
