# SPDX-License-Identifier: Apache-2.0
"""HTTP checks — is each app actually answering? (§12 Phase 2 slice 1 follow-up)

A container can be up while its site is broken: a crashed worker, a missing proxy route,
a 502 from upstream. Each configured check is a plain GET, redirects *not* followed:

- 2xx / 3xx — **up** (a redirect to a sign-in page means the app answered)
- 401 / 403 — **up** (an auth gate answered; HUD sends no credentials)
- 404 and other 4xx, 5xx, timeouts, connection errors — **down** (a proxy's 404 for a
  missing route is an outage, not a success)
- answered, but slower than ``slow`` — **degraded**

One resource per check (``{provider}:endpoint:{name}``), with ``response_seconds`` and
``status_code`` metrics. A failing check is that endpoint down, never a failed provider,
so availability records it as downtime. Read-only: GET only, no credentials, no bodies
kept. Targets come only from this provider's own config.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hud.config.schemas.duration import parse_duration
from hud.providers.sdk import (
    Metric,
    PluginConfig,
    PluginProvider,
    PollResult,
    ProviderContext,
    Resource,
    Schedule,
    SourceUnit,
    State,
    make_uid,
    register,
)

KIND = "endpoint"
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # the stable id: part of the uid, so history survives a URL change
    url: str
    title: str | None = None
    # Exact codes that count as up, replacing the default rule (e.g. [200] for a strict
    # health endpoint).
    expect: list[int] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME.fullmatch(v):
            msg = "check name must be [a-z0-9_-], starting with a letter or digit"
            raise ValueError(msg)
        return v

    @field_validator("url")
    @classmethod
    def _absolute(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            msg = "check url must be an absolute http(s) URL"
            raise ValueError(msg)
        return v


class HttpConfig(PluginConfig):
    checks: list[Check] = Field(min_length=1, max_length=500)
    timeout: str = "10s"
    slow: str = "2s"
    verify_tls: bool = True
    concurrency: int = Field(default=10, ge=1, le=50)

    @field_validator("timeout", "slow")
    @classmethod
    def _dur(cls, v: str) -> str:
        parse_duration(v)
        return v

    @model_validator(mode="after")
    def _unique(self) -> HttpConfig:
        names = [c.name for c in self.checks]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            msg = f"duplicate check names: {', '.join(dupes)}"
            raise ValueError(msg)
        return self


def classify(status: int | None, seconds: float, slow: float, expect: list[int] | None) -> State:
    """The state a response means (see the module docstring)."""
    if status is None:
        return State.DOWN
    answered = status < 400 or status in (401, 403)
    ok = status in expect if expect is not None else answered
    if not ok:
        return State.DOWN
    return State.DEGRADED if seconds > slow else State.UP


@register("http", config_model=HttpConfig)
class HttpChecks(PluginProvider):
    config: HttpConfig

    def __init__(self, ctx: ProviderContext, config: HttpConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(parse_duration(self.config.timeout))),
            verify=self.config.verify_tls,
            follow_redirects=False,
            headers={"User-Agent": "HUD-http-check/1"},
            limits=httpx.Limits(max_connections=self.config.concurrency),
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def discover(self) -> list[Resource]:
        now = datetime.now(UTC)
        return [self._resource(c, State.UNKNOWN, None, None, now) for c in self.config.checks]

    async def collect(self, resources: list[Resource]) -> PollResult:
        sem = asyncio.Semaphore(self.config.concurrency)
        slow = float(parse_duration(self.config.slow))

        async def one(c: Check) -> tuple[Resource, list[Metric]]:
            async with sem:
                status, seconds, reason = await self._probe(c.url)
            now = datetime.now(UTC)
            state = classify(status, seconds, slow, c.expect)
            res = self._resource(c, state, status, reason, now)
            metrics = [
                Metric.from_source(
                    resource_uid=res.uid,
                    name="response_seconds",
                    value=seconds * 1000,
                    source_unit=SourceUnit.MILLISECONDS,
                    ts=now,
                )
            ]
            if status is not None:
                metrics.append(
                    Metric.from_source(
                        resource_uid=res.uid,
                        name="status_code",
                        value=float(status),
                        source_unit=SourceUnit.COUNT,
                        ts=now,
                    )
                )
            return res, metrics

        done = await asyncio.gather(*(one(c) for c in self.config.checks))
        return PollResult(resources=[r for r, _ in done], metrics=[m for _, ms in done for m in ms])

    async def _probe(self, url: str) -> tuple[int | None, float, str | None]:
        """(status, seconds, reason). Never raises: an unreachable endpoint is a result."""
        assert self._client is not None
        started = time.monotonic()
        try:
            resp = await self._client.get(url)
        except httpx.TimeoutException:
            return None, time.monotonic() - started, "timeout"
        except httpx.HTTPError as exc:
            return None, time.monotonic() - started, type(exc).__name__
        return resp.status_code, time.monotonic() - started, f"HTTP {resp.status_code}"

    def _resource(
        self, c: Check, state: State, status: int | None, reason: str | None, now: datetime
    ) -> Resource:
        attrs: dict[str, object] = {"url": c.url}
        if status is not None:
            attrs["status_code"] = status
        if reason is not None:
            attrs["reason"] = reason
        return Resource(
            uid=make_uid(self.name, KIND, c.name),
            provider=self.name,
            kind=KIND,
            name=c.title or c.name,
            state=state,
            attrs=attrs,
            links={"ui": c.url},
            fetched_at=now,
        )


__all__ = ["Check", "HttpChecks", "HttpConfig", "classify"]
