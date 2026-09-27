# SPDX-License-Identifier: Apache-2.0
"""FastAPI routers under ``/api/v1`` plus SPA static serving."""

from fastapi import APIRouter, Depends

from hud.api.apps import router as apps_router
from hud.api.auth import router as auth_router
from hud.api.boards import router as boards_router
from hud.api.events import router as events_router
from hud.api.guard import csrf_guard
from hud.api.health import router as health_router
from hud.api.icons import router as icons_router
from hud.api.images import router as images_router
from hud.api.providers import router as providers_router
from hud.api.resources import router as resources_router

api_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(csrf_guard)])
api_v1.include_router(health_router)
api_v1.include_router(auth_router)
api_v1.include_router(resources_router)
api_v1.include_router(events_router)
api_v1.include_router(providers_router)
api_v1.include_router(boards_router)
api_v1.include_router(apps_router)
api_v1.include_router(icons_router)
api_v1.include_router(images_router)

__all__ = ["api_v1"]
