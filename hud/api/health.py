# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/health`` — config version, provider health, DB size, uptime (App. A).

``status`` describes HUD itself (config and database). A provider that is down shows in
``providers[]`` and on its tiles; it does not make the container unhealthy.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from hud import __version__
from hud.providers import ProviderHealth

router = APIRouter(tags=["health"])


class ConfigHealth(BaseModel):
    version: str
    loaded_at: datetime
    warnings: int
    error: str | None  # set when the last reload failed and the previous version is served


class DbHealth(BaseModel):
    size_bytes: int
    revisions: dict[str, str]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    app_version: str
    started_at: datetime
    uptime_seconds: float
    config: ConfigHealth
    db: DbHealth
    providers: list[ProviderHealth]


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    state = request.app.state
    snap = state.config.snapshot
    error = state.config.last_error
    return Health(
        status="degraded" if error else "ok",
        app_version=__version__,
        started_at=state.started_at,
        uptime_seconds=round(time.monotonic() - state.started_monotonic, 3),
        config=ConfigHealth(
            version=snap.version,
            loaded_at=snap.loaded_at,
            warnings=len(snap.warnings),
            error=str(error) if error else None,
        ),
        db=DbHealth(size_bytes=state.store_paths.size_bytes(), revisions=state.db_revisions),
        providers=state.collector.health(),
    )
