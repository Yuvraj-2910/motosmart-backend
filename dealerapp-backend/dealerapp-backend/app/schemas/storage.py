"""S3 presigned URL schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PresignUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=120)
    # Logical bucket prefix; constrained server-side to a known allowlist.
    category: str = Field(default="bike-images")


class PresignUploadResponse(BaseModel):
    upload_url: str
    key: str
    public_url: str
    expires_in: int


class PresignDownloadResponse(BaseModel):
    download_url: str
    key: str
    expires_in: int
