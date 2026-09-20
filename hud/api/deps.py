# SPDX-License-Identifier: Apache-2.0
"""Typed accessors for what the lifespan puts on ``app.state``, plus the request-scoped
identity helpers every router uses (PLAN.md §10.2: enforcement lives at the data layer, so
each router asks for a principal and filters by it)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import HTTPException, Request

from hud.config.schemas import BoardDocument

if TYPE_CHECKING:
    from hud.auth import AuthService, Principal
    from hud.collector import LiveCache
    from hud.collector.scheduler import Collector
    from hud.config import ConfigManager
    from hud.providers import ProviderRegistry
    from hud.widgets import WidgetEngine


def cache(request: Request) -> LiveCache:
    return cast("LiveCache", request.app.state.cache)


def collector(request: Request) -> Collector:
    return cast("Collector", request.app.state.collector)


def registry(request: Request) -> ProviderRegistry:
    return cast("ProviderRegistry", request.app.state.registry)


def config(request: Request) -> ConfigManager:
    return cast("ConfigManager", request.app.state.config)


def widgets(request: Request) -> WidgetEngine:
    return cast("WidgetEngine", request.app.state.widgets)


def auth(request: Request) -> AuthService:
    return cast("AuthService", request.app.state.auth)


# ---------------------------------------------------------------------------- identity


async def optional_principal(request: Request) -> Principal | None:
    return await auth(request).resolve(request)


async def principal(request: Request) -> Principal:
    """The caller, or 401. Anonymous is never a valid state for a data route."""
    p = await auth(request).resolve(request)
    if p is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return p


async def require(request: Request, *permissions: str) -> Principal:
    """Principal holding any of ``permissions``, else 403."""
    p = await principal(request)
    if not p.has_any(*permissions):
        raise HTTPException(status_code=403, detail=f"requires {' or '.join(permissions)}")
    return p


def visible_boards(request: Request, p: Principal) -> list[BoardDocument]:
    docs = [d.model for d in config(request).snapshot.documents]
    boards = [d for d in docs if isinstance(d, BoardDocument)]
    boards.sort(key=lambda d: d.metadata.name)
    authz = auth(request).authorizer
    return [b for b in boards if authz.can_view_board(p, b)]


def visible_uids(request: Request, p: Principal) -> set[str] | None:
    """``None`` means unrestricted (``resources:view``); otherwise the uids the caller's
    boards expose right now."""
    if p.has("resources:view"):
        return None
    engine = widgets(request)
    uids: set[str] = set()
    for board in visible_boards(request, p):
        uids |= engine.referenced_uids(board)
    return uids
