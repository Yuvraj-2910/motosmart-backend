"""Cognito JWT verification.

The backend does not issue tokens. The Flutter app authenticates against the
Cognito User Pool (USER_AUTH / OTP flow) and sends the resulting JWT as
`Authorization: Bearer <token>`. Here we:

  1. fetch and cache the pool's JWKS,
  2. verify the signature, `iss`, `exp`, and audience,
  3. extract `sub` and `cognito:groups`.

Both ID tokens and access tokens are accepted: ID tokens carry `aud`, access
tokens carry `client_id`, so the audience check adapts to `token_use`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

from app.core.config import settings

logger = logging.getLogger(__name__)


class TokenError(Exception):
    """Raised when a bearer token is missing, malformed, or invalid."""


@dataclass
class CognitoClaims:
    """Verified claims we care about."""

    sub: str
    groups: list[str] = field(default_factory=list)
    email: str | None = None
    phone_number: str | None = None
    username: str | None = None
    token_use: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def has_group(self, group: str) -> bool:
        return group in self.groups


class _JwksCache:
    """Process-local JWKS cache with a TTL."""

    def __init__(self) -> None:
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0

    def _expired(self) -> bool:
        return (time.monotonic() - self._fetched_at) > settings.JWKS_CACHE_SECONDS

    async def get_key(self, kid: str) -> dict[str, Any]:
        if kid in self._keys and not self._expired():
            return self._keys[kid]
        await self.refresh()
        if kid not in self._keys:
            # Key rotation happened mid-flight, or the token is not ours.
            raise TokenError("Signing key not found in JWKS")
        return self._keys[kid]

    async def refresh(self) -> None:
        if not settings.COGNITO_USER_POOL_ID:
            raise TokenError("COGNITO_USER_POOL_ID is not configured")
        url = settings.cognito_jwks_url
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TokenError(f"Unable to fetch JWKS: {exc}") from exc

        keys = {k["kid"]: k for k in data.get("keys", []) if "kid" in k}
        if not keys:
            raise TokenError("JWKS response contained no keys")
        self._keys = keys
        self._fetched_at = time.monotonic()
        logger.info("Refreshed Cognito JWKS (%d keys)", len(keys))


jwks_cache = _JwksCache()


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise TokenError("Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise TokenError("Authorization header must be 'Bearer <token>'")
    return parts[1].strip()


async def verify_token(token: str) -> CognitoClaims:
    """Verify a Cognito JWT and return its claims."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise TokenError(f"Malformed token header: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise TokenError("Token header missing 'kid'")

    key = await jwks_cache.get_key(kid)

    # Access tokens carry `client_id`; ID tokens carry `aud`. Decode without
    # the audience check first so we can branch on `token_use`, then assert the
    # right claim ourselves.
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise TokenError(f"Token verification failed: {exc}") from exc

    token_use = claims.get("token_use")
    if settings.VERIFY_TOKEN_AUDIENCE and settings.COGNITO_APP_CLIENT_ID:
        expected = settings.COGNITO_APP_CLIENT_ID
        actual = claims.get("aud") if token_use == "id" else claims.get("client_id")
        if actual != expected:
            raise TokenError("Token audience does not match the app client")

    sub = claims.get("sub")
    if not sub:
        raise TokenError("Token missing 'sub' claim")

    return CognitoClaims(
        sub=sub,
        groups=list(claims.get("cognito:groups") or []),
        email=claims.get("email"),
        phone_number=claims.get("phone_number"),
        username=claims.get("cognito:username") or claims.get("username"),
        token_use=token_use,
        raw=claims,
    )


def dev_claims_from_header(raw: str) -> CognitoClaims:
    """Local-only shortcut: `X-Dev-User: <sub>:<GROUP>[,<GROUP>]`.

    Only reachable when ENVIRONMENT=development and AUTH_DEV_MODE=true. Lets
    the team exercise endpoints before the Cognito pool exists.
    """
    sub, _, groups = raw.partition(":")
    if not sub:
        raise TokenError("X-Dev-User must be '<sub>:<GROUP>'")
    return CognitoClaims(
        sub=sub.strip(),
        groups=[g.strip() for g in groups.split(",") if g.strip()],
        username=sub.strip(),
        token_use="dev",
    )
