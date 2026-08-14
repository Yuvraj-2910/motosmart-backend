"""Voice-note transcription via Amazon Transcribe.

Backs the mic/dictation button on lead creation and follow-up notes. Short
clips only - not a general media pipeline. Audio is uploaded to S3, a batch
transcription job is started and polled to completion, and both the audio and
the job's output are deleted afterwards so no recording is retained past the
request.

Unlike `app.services.ai`, there is no deterministic fallback here - "what did
the dealer say" has no heuristic answer - so failures are raised as
`TranscribeError` for the router to turn into a clear error response. The
frontend already has a plain text field to fall back to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError

from app.core import aws
from app.core.config import settings
from app.services import storage

logger = logging.getLogger(__name__)

Source = Literal["transcribe"]


class TranscribeError(Exception):
    """Raised when transcription is unconfigured, times out, or fails."""


@dataclass
class TranscriptionResult:
    text: str
    language_code: str | None
    source: Source


_CONTENT_TYPE_TO_FORMAT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mp4": "mp4",
    "audio/x-m4a": "mp4",
    "audio/m4a": "mp4",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/flac": "flac",
    "audio/amr": "amr",
}

_EXTENSION_TO_FORMAT = {
    "webm": "webm",
    "ogg": "ogg",
    "oga": "ogg",
    "wav": "wav",
    "mp4": "mp4",
    "m4a": "mp4",
    "mp3": "mp3",
    "flac": "flac",
    "amr": "amr",
}


def _media_format(content_type: str, filename: str) -> str:
    fmt = _CONTENT_TYPE_TO_FORMAT.get((content_type or "").split(";")[0].strip().lower())
    if fmt:
        return fmt
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    fmt = _EXTENSION_TO_FORMAT.get(ext)
    if fmt:
        return fmt
    raise TranscribeError(f"Unsupported audio format: {content_type or ext or 'unknown'}")


async def transcribe_audio(
    *,
    data: bytes,
    content_type: str,
    filename: str,
    language_code: str | None = None,
) -> TranscriptionResult:
    """Upload a short clip to S3, run a batch Transcribe job, and return the text."""
    if not settings.TRANSCRIBE_ENABLED:
        raise TranscribeError("Voice transcription is not enabled")
    bucket = settings.voice_bucket
    if not bucket:
        raise TranscribeError("S3_VOICE_BUCKET/S3_BUCKET is not configured")

    media_format = _media_format(content_type, filename)
    key = storage.build_key("voice-notes", filename or f"note.{media_format}")
    await storage.put_object(key=key, data=data, content_type=content_type, bucket=bucket)

    job_name = f"voice-{uuid.uuid4().hex}"
    output_key = f"voice-notes-transcripts/{job_name}.json"
    media_uri = f"s3://{bucket}/{key}"

    client = aws.transcribe_client()
    job_kwargs: dict[str, Any] = {
        "TranscriptionJobName": job_name,
        "Media": {"MediaFileUri": media_uri},
        "MediaFormat": media_format,
        "OutputBucketName": bucket,
        "OutputKey": output_key,
    }
    fixed_language = language_code or settings.TRANSCRIBE_LANGUAGE_CODE
    if fixed_language:
        job_kwargs["LanguageCode"] = fixed_language
    else:
        options = [
            c.strip() for c in settings.TRANSCRIBE_LANGUAGE_OPTIONS.split(",") if c.strip()
        ]
        job_kwargs["IdentifyLanguage"] = True
        if options:
            job_kwargs["LanguageOptions"] = options

    try:
        await aws.call(client.start_transcription_job, **job_kwargs)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("Transcribe start_transcription_job failed: %s", exc)
        await storage.delete_object(key, bucket=bucket)
        raise TranscribeError("Could not start transcription") from exc

    try:
        result_language, transcript = await _poll_and_fetch(client, job_name, output_key, bucket)
        return TranscriptionResult(
            text=transcript, language_code=result_language, source="transcribe"
        )
    finally:
        # Best-effort cleanup: never leave the dealer's voice recording sitting
        # in S3 longer than the request that produced it.
        await storage.delete_object(key, bucket=bucket)
        await storage.delete_object(output_key, bucket=bucket)
        try:
            await aws.call(client.delete_transcription_job, TranscriptionJobName=job_name)
        except (ClientError, BotoCoreError) as exc:
            logger.warning("Transcribe delete_transcription_job failed: %s", exc)


async def _poll_and_fetch(
    client: Any, job_name: str, output_key: str, bucket: str
) -> tuple[str | None, str]:
    deadline = time.monotonic() + settings.TRANSCRIBE_MAX_POLL_SECONDS
    delay = 1.5
    while True:
        try:
            job = await aws.call(client.get_transcription_job, TranscriptionJobName=job_name)
        except (ClientError, BotoCoreError) as exc:
            raise TranscribeError("Could not check transcription status") from exc

        job_status = job["TranscriptionJob"]["TranscriptionJobStatus"]
        if job_status == "COMPLETED":
            language_code = job["TranscriptionJob"].get("LanguageCode")
            raw = await storage.get_object_bytes(output_key, bucket=bucket)
            payload = json.loads(raw)
            transcripts = payload.get("results", {}).get("transcripts", [])
            text = transcripts[0]["transcript"] if transcripts else ""
            return language_code, text.strip()
        if job_status == "FAILED":
            reason = job["TranscriptionJob"].get("FailureReason", "unknown reason")
            raise TranscribeError(f"Transcription failed: {reason}")

        if time.monotonic() >= deadline:
            raise TranscribeError("Transcription is taking longer than expected - try again")
        await asyncio.sleep(delay)
        delay = min(delay * 1.3, 4.0)
