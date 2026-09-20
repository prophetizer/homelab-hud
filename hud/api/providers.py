# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/providers`` and ``POST /api/v1/providers/{name}/reload`` (Appendix A).

Reload re-reads the provider's document from the current config snapshot, rebuilds the
instance, resets its circuit breakers and polls every group once before answering, so
the response already reflects the new state.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from hud.api import deps
from hud.providers import ProviderHealth

router = APIRouter(tags=["providers"])


class ProviderList(BaseModel):
    providers: list[ProviderHealth]


class ReloadResult(BaseModel):
    provider: ProviderHealth
    polled: dict[str, bool]  # group → succeeded


@router.get("/providers", response_model=ProviderList)
async def list_providers(request: Request) -> ProviderList:
    await deps.require(request, "providers:view")
    return ProviderList(providers=deps.collector(request).health())


@router.get("/providers/{name}", response_model=ProviderHealth)
async def get_provider(request: Request, name: str) -> ProviderHealth:
    await deps.require(request, "providers:view")
    health = deps.collector(request).provider_health(name)
    if health is None:
        raise HTTPException(status_code=404, detail=f"no provider {name!r}")
    return health


@router.post("/providers/{name}/reload", response_model=ReloadResult)
async def reload_provider(request: Request, name: str) -> ReloadResult:
    actor = await deps.require(request, "providers:reload")
    await asyncio.to_thread(
        deps.auth(request).store.audit, actor.subject, "providers.reload", name, "ok"
    )
    coll = deps.collector(request)
    if not await deps.registry(request).rebuild(name, deps.config(request).snapshot):
        failed = coll.provider_health(name)
        if failed is None:
            raise HTTPException(status_code=404, detail=f"no provider {name!r} in config")
        return ReloadResult(provider=failed, polled={})  # rebuilt but failed to start
    polled = await coll.poll_provider(name)
    health = coll.provider_health(name)
    assert health is not None  # just rebuilt
    return ReloadResult(provider=health, polled=polled)
