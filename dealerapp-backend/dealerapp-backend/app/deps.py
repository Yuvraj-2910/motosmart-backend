"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import (
    CognitoClaims,
    TokenError,
    dev_claims_from_header,
    extract_bearer_token,
    verify_token,
)
from app.models.enums import Role
from app.models.org import Customer, Dealer, Employee

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class CurrentUser:
    """The authenticated principal, resolved to a local profile row."""

    claims: CognitoClaims
    role: Role
    employee: Employee | None = None
    customer: Customer | None = None
    dealer: Dealer | None = None

    @property
    def sub(self) -> str:
        return self.claims.sub

    @property
    def dealer_id(self) -> uuid.UUID | None:
        if self.dealer is not None:
            return self.dealer.id
        if self.employee is not None:
            return self.employee.dealer_id
        if self.customer is not None:
            return self.customer.onboarding_dealer_id
        return None

    @property
    def employee_id(self) -> uuid.UUID | None:
        return self.employee.id if self.employee else None

    @property
    def customer_id(self) -> uuid.UUID | None:
        return self.customer.id if self.customer else None

    def require_dealer_id(self) -> uuid.UUID:
        dealer_id = self.dealer_id
        if dealer_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Your profile is not linked to a dealer.",
            )
        return dealer_id

    def require_employee(self) -> Employee:
        if self.employee is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This action requires a dealer staff profile.",
            )
        return self.employee

    def require_customer(self) -> Customer:
        if self.customer is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This action requires a customer profile.",
            )
        return self.customer


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_claims(
    authorization: Annotated[str | None, Header()] = None,
    x_dev_user: Annotated[str | None, Header()] = None,
) -> CognitoClaims:
    """Verify the bearer token (or the dev header in local development)."""
    if settings.dev_auth_enabled and x_dev_user:
        try:
            return dev_claims_from_header(x_dev_user)
        except TokenError as exc:
            raise _unauthorized(str(exc)) from exc
    try:
        token = extract_bearer_token(authorization)
        return await verify_token(token)
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc


async def get_current_user(
    session: SessionDep,
    claims: Annotated[CognitoClaims, Depends(get_claims)],
) -> CurrentUser:
    """Map a verified Cognito `sub` to an `employees` or `customers` row.

    Group membership decides which table we look in; the row must exist, since
    dealer staff are admin-provisioned and customers are created during lead
    conversion. When groups are absent we fall back to probing both tables so a
    misconfigured pool doesn't lock everyone out.
    """
    groups = claims.groups

    employee: Employee | None = None
    customer: Customer | None = None

    if Role.DEALER_STAFF in groups or not groups:
        employee = (
            await session.execute(
                select(Employee).where(Employee.cognito_sub == claims.sub)
            )
        ).scalar_one_or_none()

    if employee is None and (Role.CUSTOMER in groups or not groups):
        customer = (
            await session.execute(
                select(Customer).where(Customer.cognito_sub == claims.sub)
            )
        ).scalar_one_or_none()

    if employee is None and customer is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No profile is linked to this account. Dealer staff are provisioned by an "
                "administrator; customers are onboarded by a dealer."
            ),
        )

    if employee is not None:
        if not employee.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This staff account is deactivated.",
            )
        dealer = await session.get(Dealer, employee.dealer_id)
        return CurrentUser(
            claims=claims, role=Role.DEALER_STAFF, employee=employee, dealer=dealer
        )

    assert customer is not None
    dealer = (
        await session.get(Dealer, customer.onboarding_dealer_id)
        if customer.onboarding_dealer_id
        else None
    )
    return CurrentUser(claims=claims, role=Role.CUSTOMER, customer=customer, dealer=dealer)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_role(*roles: Role) -> Callable[[CurrentUser], CurrentUser]:
    """Dependency factory that 403s when the caller lacks the role."""

    allowed = set(roles)

    async def _guard(user: CurrentUserDep) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(sorted(allowed))}",
            )
        return user

    return _guard  # type: ignore[return-value]


DealerUserDep = Annotated[CurrentUser, Depends(require_role(Role.DEALER_STAFF))]
CustomerUserDep = Annotated[CurrentUser, Depends(require_role(Role.CUSTOMER))]
AnyUserDep = Annotated[CurrentUser, Depends(require_role(Role.DEALER_STAFF, Role.CUSTOMER))]


async def require_internal_key(
    x_internal_key: Annotated[str | None, Header()] = None,
) -> None:
    """Guards `/internal/*` demo and ops hooks with a shared secret."""
    if not settings.INTERNAL_API_KEY or x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key"
        )
