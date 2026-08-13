"""Cognito admin operations.

Dealer staff are provisioned out of band by an administrator. Customers are
created here during lead conversion: we `AdminCreateUser` them into the
`CUSTOMER` group so they can subsequently sign in with an email/SMS OTP.

Like every other AWS integration, failures are logged and reported rather than
raised — a customer row must still be created even if the pool is unreachable.
"""

from __future__ import annotations

import logging
import re
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


def to_e164(phone: str | None) -> str | None:
    """Coerce a number to the E.164 form Cognito's schema demands.

    Leads are typed in by hand, so a mobile arrives as `9464674949` as often as
    `+919464674949`, and Cognito rejects the bare form outright. A ten-digit
    number is assumed Indian, which is what this dealer network is. Anything
    that is not recognisably a phone number returns None rather than a guess —
    a wrong number silently attached to a login is worse than a refused invite.
    """
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("+"):
        return digits if 11 <= len(digits) <= 16 else None
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    return None


async def provision_customer(
    *, email: str | None, phone: str | None, name: str
) -> ProvisionResult:
    """Create a Cognito user in the CUSTOMER group so they can sign in by OTP.

    Idempotent: an existing user is reused and its `sub` returned, so converting
    the same person twice never fails and never orphans a login.
    """
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

    # The pool marks phone_number required, so a customer cannot be created
    # without one — report that plainly instead of letting Cognito reject the
    # whole call with a schema error the dealer cannot act on.
    e164 = to_e164(phone)
    if e164 is None:
        return ProvisionResult(
            False,
            error=(
                f"{phone!r} is not a usable mobile number. Fix it on the lead and "
                "convert again to give this customer a login."
                if phone
                else "A mobile number is required to create a customer login."
            ),
        )
    attributes += [
        {"Name": "phone_number", "Value": e164},
        {"Name": "phone_number_verified", "Value": "true"},
    ]

    client = aws.cognito_client()
    try:
        response = await aws.call(
            client.admin_create_user,
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=username,
            UserAttributes=attributes,
            # No invite mail: sign-in is passwordless, so the temporary password
            # Cognito would email is noise the customer cannot use. They get a
            # code when they actually try to sign in.
            MessageAction="SUPPRESS",
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
        return ProvisionResult(
            False, error=f"Cognito rejected the login ({code}). Convert again to retry."
        )
    except BotoCoreError as exc:
        # Name the failure. "Cognito unreachable" covers expired credentials, a
        # DNS failure and a read timeout alike, and the dealer reporting it
        # cannot tell which — nor could anyone reading the log afterwards.
        logger.warning(
            "AdminCreateUser transport error for %s: %s: %s",
            _mask(username), type(exc).__name__, exc,
        )
        return ProvisionResult(
            False, error=f"Could not reach Cognito ({type(exc).__name__}). Convert again to retry."
        )

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
