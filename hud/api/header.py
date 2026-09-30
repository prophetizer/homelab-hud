# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/header`` — the strip above every board — and its optional backdrop image.

What the header shows is named in settings.yaml by the admin and is the same for every
signed-in user (see HeaderSettings). Only resolved readings go out, never whole resources.
A weather provider that fails shows its error and last success (invariant 6).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from hud.api import deps
from hud.providers.images import sniff

router = APIRouter(tags=["header"])

MAX_BACKGROUND_BYTES = 8 * 1024 * 1024
BACKGROUND_DIR = "backgrounds"
WEATHER_ATTRS = (
    "condition",
    "icon",
    "is_day",
    "units",
    "temperature",
    "high",
    "low",
    "wind_kmh",
    "sunrise",
    "sunset",
    "forecast",
)
WEATHER_METRICS = ("feels_like", "humidity", "precipitation_chance")


class Weather(BaseModel):
    uid: str
    name: str | None = None
    state: str
    stale: bool = False
    error: str | None = None
    last_success: datetime | None = None
    attrs: dict[str, Any] = {}
    metrics: dict[str, float] = {}


@router.get("/theme.css", include_in_schema=False)
async def theme_css(request: Request) -> Response:
    """The instance's theme.park palette as HUD tokens (PLAN §8.6), or an empty stylesheet.
    Unauthenticated on purpose: the sign-in page wears the theme too, and it holds only
    colours."""
    config = deps.config(request)
    css = await request.app.state.theme_park.css(config.snapshot.settings.spec.theme_park)
    return Response(
        css,
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


class AppearanceOut(BaseModel):
    background: str | None  # the URL to fetch it from, versioned by file mtime
    dim: int
    blur: int
    translucent: bool


class Header(BaseModel):
    enabled: bool
    title: str
    timezone: str
    greeting: bool
    clock: bool
    search_url: str | None
    weather: Weather | None
    stats: list[dict[str, Any]]
    appearance: AppearanceOut


@router.get("/header")
async def get_header(request: Request) -> Header:
    await deps.principal(request)
    spec = deps.config(request).snapshot.settings.spec
    h = spec.header
    engine = deps.widgets(request)
    look = spec.appearance
    background = _background_version(deps.config(request).config_dir, look.background)
    return Header(
        enabled=h.enabled,
        title=spec.title,
        timezone=spec.timezone,
        greeting=h.greeting,
        clock=h.clock,
        search_url=h.search_url,
        weather=_weather(request, h.weather) if h.weather else None,
        stats=[engine.stat(s) for s in h.stats],
        appearance=AppearanceOut(
            background=(
                f"/api/v1/header/background?v={background}" if background is not None else None
            ),
            dim=look.dim,
            blur=look.blur,
            translucent=look.translucent,
        ),
    )


def _weather(request: Request, uid: str) -> Weather:
    r = deps.cache(request).resource(uid)
    health = deps.collector(request).provider_health(uid.split(":", 1)[0])
    error = health.last_error if health is not None else None
    last_success = health.last_success if health is not None else None
    if r is None:
        return Weather(
            uid=uid,
            state="unknown",
            error=error or f"no resource {uid!r} (is its provider polling?)",
            last_success=last_success,
        )
    metrics = {}
    for name in WEATHER_METRICS:
        m = deps.cache(request).metric(uid, name)
        if m is not None:
            metrics[name] = m.value
    return Weather(
        uid=uid,
        name=r.name,
        state=r.state.value,
        stale=r.stale,
        error=error,
        last_success=last_success,
        attrs={k: r.attrs[k] for k in WEATHER_ATTRS if k in r.attrs},
        metrics=metrics,
    )


@router.get("/header/background")
async def get_background(request: Request) -> Response:
    """The appearance.background file. The name was validated as a bare file name when
    settings loaded; it is resolved inside /config/backgrounds/ and checked again here."""
    await deps.principal(request)
    config = deps.config(request)
    name = config.snapshot.settings.spec.appearance.background
    if name is None:
        raise HTTPException(status_code=404, detail="no background")
    folder = (config.config_dir / BACKGROUND_DIR).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="no background")
    body = _read_capped(path)
    kind = sniff(body) if body is not None else None
    if body is None or kind is None:
        raise HTTPException(status_code=404, detail="no background")
    return Response(
        content=body,
        media_type=kind,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


def _background_version(config_dir: Path, name: str | None) -> int | None:
    """The file's mtime, so a replaced image is fetched again; None when it is missing."""
    if name is None:
        return None
    try:
        return int((config_dir / BACKGROUND_DIR / name).stat().st_mtime)
    except OSError:
        return None


def _read_capped(path: Path) -> bytes | None:
    if path.stat().st_size > MAX_BACKGROUND_BYTES:
        return None
    return path.read_bytes()
