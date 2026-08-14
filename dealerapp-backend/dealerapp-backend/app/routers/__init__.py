"""API routers, mounted under `/api/v1` by `app.main`."""

from fastapi import APIRouter

from app.routers import (
    ai,
    chatbot,
    customers,
    dashboard,
    dealer_chatbot,
    incentives,
    internal,
    leads,
    me,
    notifications,
    public,
    service_requests,
    test_rides,
    vehicles,
)

api_router = APIRouter()

# Phase 1 - core dealer flow + mandatory AI
api_router.include_router(me.router)
api_router.include_router(leads.router)
api_router.include_router(dashboard.router)
api_router.include_router(ai.router)

# Phase 2 - public funnel, auto-assignment, notifications
api_router.include_router(public.router)
api_router.include_router(test_rides.router)
api_router.include_router(notifications.router)

# Phase 3 - customer side
api_router.include_router(customers.router)
api_router.include_router(vehicles.router)
api_router.include_router(service_requests.router)
api_router.include_router(chatbot.router)

# Phase 4 - incentives
api_router.include_router(incentives.router)

# Phase 7 - dealer chatbot (leads + tickets context)
api_router.include_router(dealer_chatbot.router)

# Ops / demo hooks (X-Internal-Key guarded)
api_router.include_router(internal.router)

__all__ = ["api_router"]
