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
    from hud.config import ConfigManager, LoadedDocument
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


def visible_board_files(request: Request, p: Principal) -> list[LoadedDocument]:
    """Board documents the caller may view, with their file path and revision."""
    loaded = [d for d in config(request).snapshot.documents if isinstance(d.model, BoardDocument)]
    loaded.sort(key=lambda d: cast("BoardDocument", d.model).metadata.name)
    authz = auth(request).authorizer
    return [d for d in loaded if authz.can_view_board(p, cast("BoardDocument", d.model))]


def visible_boards(request: Request, p: Principal) -> list[BoardDocument]:
    return [cast("BoardDocument", d.model) for d in visible_board_files(request, p)]


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


def page_origin(request: Request) -> str:
    """The origin the browser is on — ``https://hud.example`` — which is what an embedded
    app's ``frame-ancestors`` is checked against. Behind a TLS-terminating proxy the scheme
    comes from X-Forwarded-Proto; the host is the one the browser asked for. A spoofed Host
    only changes the verdicts its sender sees."""
    from hud.auth.service import is_https  # noqa: PLC0415 — avoids an import cycle

    scheme = "https" if is_https(request) else request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"
