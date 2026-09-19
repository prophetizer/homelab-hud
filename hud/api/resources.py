# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/resources`` and ``GET /api/v1/resources/{uid}`` (PLAN.md Appendix A).

Everything is served from the live cache: current state, ``stale`` flag and the group's
last error alongside, so a caller can always tell fresh from last-known-good.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from hud.api import deps
from hud.collector import ResourceFilter
from hud.models import Event, Metric, Resource, State

router = APIRouter(tags=["resources"])


class ResourceList(BaseModel):
    generation: int
    resources: list[Resource]


class ResourceDetail(BaseModel):
    resource: Resource
    metrics: list[Metric]
    events: list[Event]
    error: str | None  # the owning poll group's last error, if it is currently failing


def parse_labels(pairs: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition(":")
        if not sep or not key:
            raise HTTPException(status_code=422, detail=f"label must be key:value, got {pair!r}")
        labels[key] = value
    return labels


@router.get("/resources", response_model=ResourceList)
async def list_resources(
    request: Request,
    provider: Annotated[list[str] | None, Query()] = None,
    kind: Annotated[list[str] | None, Query()] = None,
    state: Annotated[list[State] | None, Query()] = None,
    label: Annotated[list[str] | None, Query(description="key:value, all must match")] = None,
) -> ResourceList:
    cache = deps.cache(request)
    flt = ResourceFilter(
        provider=provider,
        kind=kind,
        state=state,
        labels=parse_labels(label) if label else None,
    )
    items = sorted(cache.resources(flt), key=lambda r: (r.provider, r.kind, r.name))
    return ResourceList(generation=cache.generation, resources=items)


@router.get("/resources/{uid:path}", response_model=ResourceDetail)
async def get_resource(request: Request, uid: str) -> ResourceDetail:
    cache = deps.cache(request)
    resource = cache.resource(uid)
    if resource is None:
        raise HTTPException(status_code=404, detail=f"no resource {uid!r}")
    error = None
    if resource.stale:
        groups = deps.collector(request).groups_of(resource.provider)
        errors = [e for e in (cache.group_error(resource.provider, g) for g in groups) if e]
        error = errors[0] if errors else None
    return ResourceDetail(
        resource=resource,
        metrics=sorted(cache.metrics_for(uid), key=lambda m: m.name),
        events=cache.events(limit=50, uid=uid),
        error=error,
    )
