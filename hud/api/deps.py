# SPDX-License-Identifier: Apache-2.0
"""Typed accessors for what the lifespan puts on ``app.state``."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request

if TYPE_CHECKING:
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
