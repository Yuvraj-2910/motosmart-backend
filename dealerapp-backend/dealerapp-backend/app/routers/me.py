"""`GET /me` — the profile call the app makes right after login."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AnyUserDep
from app.schemas.org import CustomerOut, DealerOut, EmployeeOut, MeOut

router = APIRouter(tags=["profile"])


@router.get("/me", response_model=MeOut, summary="Current user's profile and role")
async def read_me(user: AnyUserDep) -> MeOut:
    """Confirms the backend profile behind the Cognito token.

    The app already knows its role from `cognito:groups`, but this call proves
    the local profile row exists before it commits to a shell.
    """
    return MeOut(
        role=user.role,
        cognito_sub=user.sub,
        employee=EmployeeOut.model_validate(user.employee) if user.employee else None,
        customer=CustomerOut.model_validate(user.customer) if user.customer else None,
        dealer=DealerOut.model_validate(user.dealer) if user.dealer else None,
    )
