# SPDX-License-Identifier: Apache-2.0
"""FastAPI routers under ``/api/v1`` plus SPA static serving."""

from fastapi import APIRouter

from hud.api.events import router as events_router
from hud.api.health import router as health_router
from hud.api.providers import router as providers_router
from hud.api.resources import router as resources_router

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(health_router)
api_v1.include_router(resources_router)
api_v1.include_router(events_router)
api_v1.include_router(providers_router)

__all__ = ["api_v1"]
