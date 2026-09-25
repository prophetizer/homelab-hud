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
from hud.api import deps
from hud.providers import ProviderHealth

router = APIRouter(tags=["health"])


class QuarantinedFile(BaseModel):
    """One invalid Provider/Board file whose failure is contained to itself."""

    file: str  # relative to /config
    kind: str
    name: str | None
    serving_last_good: bool  # an earlier valid version of the file is still in use
    issues: list[str]  # file:line:col: message


class ConfigHealth(BaseModel):
    version: str
    loaded_at: datetime
    warnings: int
    error: str | None  # set when the last reload failed and the previous version is served
    quarantined: list[QuarantinedFile]


class DbHealth(BaseModel):
    size_bytes: int
    revisions: dict[str, str]


class PublicHealth(BaseModel):
    """What the container healthcheck and an anonymous caller get: liveness, nothing else."""

    status: Literal["ok", "degraded"]
    app_version: str


class Health(PublicHealth):
    started_at: datetime
    uptime_seconds: float
    config: ConfigHealth
    db: DbHealth
    providers: list[ProviderHealth]


@router.get("/health", response_model=Health | PublicHealth)
async def health(request: Request) -> Health | PublicHealth:
    state = request.app.state
    snap = state.config.snapshot
    error = state.config.last_error
    status: Literal["ok", "degraded"] = "degraded" if error or snap.quarantined else "ok"
    caller = await deps.optional_principal(request)
    if caller is None or not caller.has("providers:view"):
        return PublicHealth(status=status, app_version=__version__)
    return Health(
        status=status,
        app_version=__version__,
        started_at=state.started_at,
        uptime_seconds=round(time.monotonic() - state.started_monotonic, 3),
        config=ConfigHealth(
            version=snap.version,
            loaded_at=snap.loaded_at,
            warnings=len(snap.warnings),
            error=str(error) if error else None,
            quarantined=[
                QuarantinedFile(
                    file=str(q.path.relative_to(state.config.config_dir)),
                    kind=q.kind,
                    name=q.name,
                    serving_last_good=q.serving_last_good,
                    issues=[str(i) for i in q.issues],
                )
                for q in snap.quarantined
            ],
        ),
        db=DbHealth(size_bytes=state.store_paths.size_bytes(), revisions=state.db_revisions),
        providers=deps.collector(request).health(),
    )
