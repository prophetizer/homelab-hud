# SPDX-License-Identifier: Apache-2.0
"""``GET /api/v1/events`` — recent events from the live ring (PLAN.md Appendix A).

Range queries over persisted history belong to the Phase 2 query API; this endpoint
answers "what just happened" from memory, filtered by severity and provider.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from hud.api import deps
from hud.models import Event, Severity

router = APIRouter(tags=["events"])

MAX_LIMIT = 500


class EventList(BaseModel):
    events: list[Event]


@router.get("/events", response_model=EventList)
async def list_events(
    request: Request,
    severity: Annotated[list[Severity] | None, Query()] = None,
    provider: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
) -> EventList:
    cache = deps.cache(request)
    out: list[Event] = []
    prefixes = tuple(f"{p}:" for p in provider) if provider else None
    # Over-fetch from the ring, then filter: filters are cheap and the ring is bounded.
    for e in cache.events(limit=MAX_LIMIT * 4):
        if severity is not None and e.severity not in severity:
            continue
        if prefixes is not None and not e.resource_uid.startswith(prefixes):
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return EventList(events=out)
