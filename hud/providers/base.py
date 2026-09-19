# SPDX-License-Identifier: Apache-2.0
"""The provider contract both tiers implement (PLAN.md §7).

A provider is a set of *poll groups*. Each group has its own interval, jitter and timeout,
and ``poll(group)`` returns canonical objects only. The declarative engine maps every
``resources[]`` entry onto one group; the plugin SDK maps ``discover``/``collect`` onto two.
Downstream — scheduler, cache, widgets — nothing knows which tier a provider came from.

Providers never write to the DB, never touch the cache, never log secrets. The context
gives them a scoped logger, secret resolution and an HTTP client factory with sane limits.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

import httpx
from pydantic import BaseModel

from hud.config.secrets import SecretResolver
from hud.models import Event, Metric, Resource


class ProviderTier(StrEnum):
    DECLARATIVE = "declarative"
    PLUGIN = "plugin"


class ProviderStatus(StrEnum):
    STARTING = "starting"  # built, no poll completed yet
    OK = "ok"
    DEGRADED = "degraded"  # recent failures; circuit may be open; last-known-good served
    ERROR = "error"  # could not be built at all (missing env/secret, plugin refused)
    STOPPED = "stopped"


@dataclass(frozen=True)
class PollGroup:
    name: str
    interval: float  # seconds
    jitter: float = 0.0
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if self.interval <= 0 or self.timeout <= 0 or self.jitter < 0:
            msg = f"poll group {self.name!r}: interval/timeout must be > 0 and jitter >= 0"
            raise ValueError(msg)


@dataclass
class PollResult:
    """What one poll produced. Empty lists are valid (nothing there is not an error)."""

    resources: list[Resource] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    def extend(self, other: PollResult) -> None:
        self.resources.extend(other.resources)
        self.metrics.extend(other.metrics)
        self.events.extend(other.events)


class ProviderHealth(BaseModel):
    """Per-provider state as reported in ``/api/v1/health`` and ``/api/v1/providers``."""

    name: str
    tier: ProviderTier
    status: ProviderStatus
    labels: dict[str, str] = {}
    groups: list[str] = []
    last_poll: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None
    resource_count: int = 0


@dataclass(frozen=True)
class HttpOptions:
    """Everything an outbound client needs. Conservative by default (PLAN.md §7.2:
    "timeouts and retries already configured"); the breaker handles the rest."""

    base_url: str
    timeout: float
    verify_tls: bool = True
    headers: Mapping[str, str] = field(default_factory=dict)
    auth: httpx.Auth | None = None
    max_connections: int = 4


@dataclass(frozen=True)
class ProviderContext:
    name: str
    secrets: SecretResolver
    env: Mapping[str, str]
    log: logging.Logger

    @classmethod
    def create(cls, name: str, secrets: SecretResolver, env: Mapping[str, str]) -> ProviderContext:
        return cls(
            name=name, secrets=secrets, env=env, log=logging.getLogger(f"hud.provider.{name}")
        )

    def new_http_client(self, opts: HttpOptions) -> httpx.AsyncClient:
        """An ``AsyncClient`` pinned to ``opts.base_url``. Redirects are off so a provider
        can never be bounced to a host outside its declared one (R5)."""
        return httpx.AsyncClient(
            base_url=opts.base_url,
            timeout=httpx.Timeout(opts.timeout),
            verify=opts.verify_tls,
            headers=dict(opts.headers),
            auth=opts.auth,
            limits=httpx.Limits(
                max_connections=opts.max_connections,
                max_keepalive_connections=min(2, opts.max_connections),
            ),
            follow_redirects=False,
        )


class Provider(ABC):
    """Base for both tiers. Subclasses set ``tier`` and implement the four methods."""

    tier: ProviderTier

    def __init__(self, ctx: ProviderContext, labels: Mapping[str, str] | None = None) -> None:
        self.ctx = ctx
        self.labels: dict[str, str] = dict(labels or {})

    @property
    def name(self) -> str:
        return self.ctx.name

    @property
    def log(self) -> logging.Logger:
        return self.ctx.log

    @abstractmethod
    def groups(self) -> Sequence[PollGroup]:
        """Poll groups, fixed for the provider's lifetime."""

    async def startup(self) -> None:  # noqa: B027 — optional hook, not abstract by design
        """Open clients, warm caches. Failing here marks the provider ``error``."""

    @abstractmethod
    async def poll(self, group: str) -> PollResult:
        """Run one poll group. Raise to signal failure; the scheduler handles backoff."""

    async def shutdown(self) -> None:  # noqa: B027 — optional hook, not abstract by design
        """Close clients. Must not raise."""

    def group(self, name: str) -> PollGroup:
        for g in self.groups():
            if g.name == name:
                return g
        msg = f"{self.name}: no poll group {name!r}"
        raise KeyError(msg)
