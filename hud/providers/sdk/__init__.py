# SPDX-License-Identifier: Apache-2.0
"""Plugin SDK — the public surface a Tier 2 provider imports (PLAN.md §7.2).

A plugin is a class decorated with :func:`register`, subclassing :class:`PluginProvider`
and implementing ``discover()`` (expensive, rare — default every 5 minutes) and
``collect()`` (cheap, at interval). It is configured by a ``kind: Provider`` document
with ``spec.plugin`` and ``spec.config``; ``config`` is validated by the plugin's
``config_model`` at build time, with ``${secret:name}`` and ``${VAR}`` references already
resolved. Plugins never write to the DB, never touch the cache, never log secrets.

Everything a plugin should need is re-exported here so a drop-in file has one import.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from hud.models import Event, Metric, Resource, Severity, SourceUnit, State, Unit, make_uid
from hud.providers.base import (
    HttpOptions,
    PollGroup,
    PollResult,
    Provider,
    ProviderContext,
    ProviderTier,
)
from hud.providers.errors import ProviderBuildError, ProviderPollError

# Bumped on any incompatible change to PluginProvider, ProviderContext or the canonical
# model. The loader refuses a plugin whose declared major differs (PLAN.md §7.2).
SDK_VERSION = "1.0"
SUPPORTED_SDK_MAJORS: frozenset[int] = frozenset({1})

DEFAULT_DISCOVER_INTERVAL = 300.0
DISCOVER_GROUP = "discover"
COLLECT_GROUP = "collect"


class PluginConfig(BaseModel):
    """Base for a plugin's ``config_model``. Unknown keys are refused: a typo in a plugin's
    config is a config error, not something to warn about and ignore."""

    model_config = ConfigDict(extra="forbid")


class Schedule(BaseModel):
    """Timing the registry derives from ``spec.defaults`` and the plugin's own defaults."""

    model_config = ConfigDict(frozen=True)

    interval: float
    jitter: float
    timeout: float
    discover_interval: float = DEFAULT_DISCOVER_INTERVAL


class PluginProvider(Provider):
    """Base class for Tier 2 providers.

    Two poll groups: ``discover`` refreshes the resource set, ``collect`` refreshes state
    and metrics for the last discovered set. ``collect`` before any ``discover`` runs a
    discover first, so the first tile never waits five minutes.
    """

    tier = ProviderTier.PLUGIN

    # Set by @register.
    plugin_name: ClassVar[str]
    config_model: ClassVar[type[PluginConfig]]
    sdk_version: ClassVar[str]
    # A plugin may override to poll discover more or less often than the default.
    discover_interval: ClassVar[float] = DEFAULT_DISCOVER_INTERVAL

    def __init__(self, ctx: ProviderContext, config: PluginConfig, schedule: Schedule) -> None:
        super().__init__(ctx)
        self.config = config
        self.schedule = schedule
        self._resources: list[Resource] = []
        self._discovered = False

    # ---------------------------------------------------------------- to implement

    async def discover(self) -> list[Resource]:
        """Enumerate resources. Expensive; runs every ``discover_interval`` seconds."""
        raise NotImplementedError

    async def collect(self, resources: list[Resource]) -> PollResult:
        """Refresh state and metrics for ``resources``. Default: state only, no metrics.
        Return the refreshed resources; anything absent from the result is dropped."""
        return PollResult(resources=list(resources))

    # ---------------------------------------------------------------- contract glue

    def groups(self) -> Sequence[PollGroup]:
        s = self.schedule
        return [
            PollGroup(DISCOVER_GROUP, s.discover_interval, s.jitter, s.timeout),
            PollGroup(COLLECT_GROUP, s.interval, s.jitter, s.timeout),
        ]

    async def poll(self, group: str) -> PollResult:
        if group == DISCOVER_GROUP or not self._discovered:
            self._resources = await self.discover()
            self._discovered = True
            if group == DISCOVER_GROUP:
                return PollResult(resources=list(self._resources))
        result = await self.collect(list(self._resources))
        self._resources = list(result.resources)
        return result


__all__ = [
    "COLLECT_GROUP",
    "DISCOVER_GROUP",
    "SDK_VERSION",
    "SUPPORTED_SDK_MAJORS",
    "Event",
    "HttpOptions",
    "Metric",
    "PluginConfig",
    "PluginProvider",
    "PollGroup",
    "PollResult",
    "Provider",
    "ProviderBuildError",
    "ProviderContext",
    "ProviderPollError",
    "Resource",
    "Schedule",
    "Severity",
    "SourceUnit",
    "State",
    "Unit",
    "make_uid",
    "register",
]


def register(
    name: str, *, config_model: type[PluginConfig], sdk_version: str = SDK_VERSION
) -> _Register:
    """Class decorator declaring a plugin: ``@register("docker", config_model=DockerConfig)``.

    Registration is recorded on the class; the loader (``hud.providers.sdk.loader``)
    collects classes from entry points and drop-in files, so importing a plugin module
    never has global side effects beyond defining the class."""
    return _Register(name, config_model, sdk_version)


class _Register:
    def __init__(self, name: str, config_model: type[PluginConfig], sdk_version: str) -> None:
        self.name = name
        self.config_model = config_model
        self.sdk_version = sdk_version

    def __call__[P: PluginProvider](self, cls: type[P]) -> type[P]:
        # A drop-in author is not bound by our annotations; check at runtime too.
        _require_plugin_subclass(cls)
        cls.plugin_name = self.name
        cls.config_model = self.config_model
        cls.sdk_version = self.sdk_version
        return cls


def _require_plugin_subclass(cls: object) -> None:
    if not (isinstance(cls, type) and issubclass(cls, PluginProvider)):
        msg = f"@register: {cls!r} must subclass PluginProvider"
        raise TypeError(msg)
