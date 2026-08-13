"""Cognito admin operations.

Dealer staff are provisioned out of band by an administrator. Customers are
created here during lead conversion: we `AdminCreateUser` them into the
`CUSTOMER` group so they can subsequently sign in with an email/SMS OTP.

Like every other AWS integration, failures are logged and reported rather than
raised — a customer row must still be created even if the pool is unreachable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

from app.core import aws
from app.core.config import settings
from app.models.enums import Role

logger = logging.getLogger(__name__)


@dataclass
class ProvisionResult:
    ok: bool
    cognito_sub: str | None = None
    error: str | None = None


async def provision_customer(
    *, email: str | None, phone: str | None, name: str
) -> ProvisionResult:
    """Create a Cognito user in the CUSTOMER group and send the OTP invite."""
    if not settings.COGNITO_USER_POOL_ID:
        return ProvisionResult(False, error="Cognito user pool is not configured")

    username = (email or phone or "").strip()
    if not username:
        return ProvisionResult(False, error="An email or phone number is required to invite")

    attributes: list[dict[str, str]] = [{"Name": "name", "Value": name}]
    if email:
        attributes += [
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ]
    if phone:
        attributes += [
            {"Name": "phone_number", "Value": phone},
            {"Name": "phone_number_verified", "Value": "true"},
        ]

    client = aws.cognito_client()
    try:
        response = await aws.call(
            client.admin_create_user,
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=username,
            UserAttributes=attributes,
            DesiredDeliveryMediums=["EMAIL"] if email else ["SMS"],
        )
        sub = _extract_sub(response.get("User", {}))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "UsernameExistsException":
            logger.info("Cognito user already exists for %s; reusing", _mask(username))
            sub = await _lookup_sub(username)
            if sub:
                await _add_to_group(username)
                return ProvisionResult(True, cognito_sub=sub)
            return ProvisionResult(False, error="User exists but could not be read")
        logger.warning("AdminCreateUser failed for %s: %s", _mask(username), exc)
        return ProvisionResult(False, error=f"Cognito error: {code}")
    except BotoCoreError as exc:
        logger.warning("AdminCreateUser transport error: %s", exc)
        return ProvisionResult(False, error="Cognito unreachable")

    await _add_to_group(username)
    return ProvisionResult(True, cognito_sub=sub)


async def _add_to_group(username: str, group: str = Role.CUSTOMER) -> None:
    client = aws.cognito_client()
    try:
        await aws.call(
            client.admin_add_user_to_group,
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=username,
            GroupName=group,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("Could not add %s to group %s: %s", _mask(username), group, exc)


async def _lookup_sub(username: str) -> str | None:
    client = aws.cognito_client()
    try:
        response = await aws.call(
            client.admin_get_user,
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=username,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("AdminGetUser failed for %s: %s", _mask(username), exc)
        return None
    return _extract_sub({"Attributes": response.get("UserAttributes", [])})


def _extract_sub(user: dict) -> str | None:
    for attr in user.get("Attributes", []):
        if attr.get("Name") == "sub":
            return attr.get("Value")
    return None


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"
