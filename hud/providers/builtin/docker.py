# SPDX-License-Identifier: Apache-2.0
"""Docker plugin — containers via ``linuxserver/socket-proxy`` (PLAN.md §7.2, invariant 5).

Read-only: ``GET /containers/json`` for the set and state, ``GET /containers/{name}/stats``
for CPU, memory and network rates. The proxy's ``CONTAINERS=1`` covers both; ``POST=0``
means nothing here could act even if asked. The UID is the container *name*, never the
id, so a recreated container keeps its history (invariant 8).
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import Field, field_validator

from hud.config.schemas.settings import parse_duration
from hud.providers.sdk import (
    HttpOptions,
    Metric,
    PluginConfig,
    PluginProvider,
    PollGroup,
    PollResult,
    ProviderContext,
    ProviderPollError,
    Resource,
    Schedule,
    SourceUnit,
    State,
    make_uid,
    register,
)

KIND = "container"
STATS_GROUP = "stats"
LIST_CONNECTIONS = 2  # discover and collect may overlap; neither ever waits on stats
STATS_CONCURRENCY = 4
# Budget ceiling per stats request. With one-shot a request returns at once; an engine that
# ignores one-shot samples twice a second apart, so the budget stays sized for that.
STATS_SECONDS_EACH = 2.0


class DockerConfig(PluginConfig):
    base_url: str = "http://socket-proxy:2375"
    timeout: str = "10s"
    verify_tls: bool = True
    stats: bool = True  # per-container /stats each interval; False = state only
    max_stats: int = Field(default=50, ge=1, le=500)  # cap on stats requests per poll
    include_stopped: bool = True

    @field_validator("base_url")
    @classmethod
    def _http_scheme(cls, v: str) -> str:
        # DOCKER_HOST conventionally says tcp://; the proxy speaks plain HTTP.
        if v.startswith("tcp://"):
            v = "http://" + v.removeprefix("tcp://")
        if not v.startswith(("http://", "https://")):
            msg = "base_url must be http(s):// (or tcp://, which is rewritten)"
            raise ValueError(msg)
        return v.rstrip("/")

    @field_validator("timeout")
    @classmethod
    def _dur(cls, v: str) -> str:
        parse_duration(v)
        return v

    @property
    def timeout_seconds(self) -> float:
        return float(parse_duration(self.timeout))


_STATE: dict[str, State] = {
    "running": State.UP,
    "paused": State.PAUSED,
    "restarting": State.DEGRADED,
    "created": State.UNKNOWN,
    "removing": State.DOWN,
    "exited": State.DOWN,
    "dead": State.DOWN,
}


@register("docker", config_model=DockerConfig)
class DockerProvider(PluginProvider):
    config: DockerConfig

    def __init__(self, ctx: ProviderContext, config: DockerConfig, schedule: Schedule) -> None:
        super().__init__(ctx, config, schedule)
        self._client: httpx.AsyncClient | None = None
        self._stats_client: httpx.AsyncClient | None = None
        self._net_prev: dict[str, tuple[float, int, int]] = {}
        self._cpu_prev: dict[str, tuple[int, int]] = {}  # uid -> (total_usage, system_usage)

    def groups(self) -> Sequence[PollGroup]:
        """Container state and per-container stats are separate groups on separate
        connections. Found on a live 103-container stack: when they shared one poll and one
        4-connection pool, a slow stats pass on a busy engine timed out the whole collect —
        staling every container's up/down state — and starved the discover listing of a
        connection until its breaker opened for five minutes. Stats now report metrics
        only, so the cache attributes no resources to them and a failed stats pass cannot
        mark any container stale; it costs a gap in metrics, nothing more."""
        s = self.schedule
        per_request = self.config.timeout_seconds
        groups = [
            PollGroup("discover", s.discover_interval, s.jitter, per_request + 5.0),
            PollGroup("collect", s.interval, s.jitter, per_request + 5.0),
        ]
        if self.config.stats:
            batches = math.ceil(self.config.max_stats / STATS_CONCURRENCY)
            timeout = per_request + batches * STATS_SECONDS_EACH + 5.0
            groups.append(PollGroup(STATS_GROUP, s.interval, s.jitter, timeout))
        return groups

    async def startup(self) -> None:
        def client(max_connections: int) -> httpx.AsyncClient:
            return self.ctx.new_http_client(
                HttpOptions(
                    base_url=self.config.base_url,
                    timeout=self.config.timeout_seconds,
                    verify_tls=self.config.verify_tls,
                    headers={"Accept": "application/json"},
                    max_connections=max_connections,
                )
            )

        self._client = client(LIST_CONNECTIONS)
        if self.config.stats:
            self._stats_client = client(STATS_CONCURRENCY)

    async def shutdown(self) -> None:
        for c in (self._client, self._stats_client):
            if c is not None:
                await c.aclose()
        self._client = self._stats_client = None

    async def poll(self, group: str) -> PollResult:
        if group == STATS_GROUP:
            return await self._stats_pass()
        return await super().poll(group)

    # ---------------------------------------------------------------- contract

    async def discover(self) -> list[Resource]:
        return await self._list()

    async def collect(self, resources: list[Resource]) -> PollResult:
        # Listing only: one cheap call, and container state matters at collect cadence.
        return PollResult(resources=await self._list())

    async def _stats_pass(self) -> PollResult:
        """Metrics only, for the running containers of the latest listing."""
        current = self._resources or await self._list()
        # Chosen by name, not in listing order: /containers/json lists newest-created first,
        # so under the cap a recreated container (an image update) would push another out of
        # the stats set, and which containers keep continuous history would drift on every
        # update. By name, the covered set changes only when containers are added or removed.
        running = sorted(
            (r for r in current if r.state in (State.UP, State.DEGRADED)), key=lambda r: r.name
        )
        skipped = len(running) - self.config.max_stats
        if skipped > 0:
            cap = self.config.max_stats
            self.log.warning(
                "stats capped at %d; %d containers skipped (the last by name, from %r); "
                "raise config.max_stats to cover them",
                cap,
                skipped,
                running[cap].name,
            )
            running = running[:cap]
        sem = asyncio.Semaphore(STATS_CONCURRENCY)

        async def one(r: Resource) -> list[Metric]:
            async with sem:
                return await self._stats(r)

        result = PollResult(resources=[])
        for metrics in await asyncio.gather(*(one(r) for r in running)):
            result.metrics.extend(metrics)
        return result

    # ---------------------------------------------------------------- docker api

    async def _get(self, path: str, *, stats_pool: bool = False, **params: str) -> Any:  # noqa: ANN401
        client = self._stats_client if stats_pool else self._client
        if client is None:
            raise ProviderPollError(self.name, "provider not started")
        try:
            resp = await client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderPollError(self.name, f"GET {path}: timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderPollError(self.name, f"GET {path}: {exc}") from exc
        if resp.status_code == 403:
            msg = f"GET {path}: 403 — the socket proxy does not allow this endpoint (CONTAINERS=1?)"
            raise ProviderPollError(self.name, msg)
        if resp.status_code >= 300:
            raise ProviderPollError(self.name, f"GET {path}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderPollError(self.name, f"GET {path}: response is not JSON") from exc

    async def _list(self) -> list[Resource]:
        raw = await self._get("/containers/json", all="1" if self.config.include_stopped else "0")
        if not isinstance(raw, list):
            raise ProviderPollError(self.name, "/containers/json did not return a list")
        now = datetime.now(UTC)
        out: list[Resource] = []
        for c in raw:
            if not isinstance(c, dict):
                self.log.warning("skipping non-object entry in /containers/json: %r", c)
                continue
            try:
                out.append(self._container(c, now))
            except (KeyError, ValueError, TypeError) as exc:
                self.log.warning("skipping container %s: %s", str(c.get("Id", "?"))[:12], exc)
        return out

    def _container(self, c: dict[str, Any], now: datetime) -> Resource:
        names = [str(n).lstrip("/") for n in c.get("Names") or []]
        name = names[0] if names else str(c["Id"])[:12]
        docker_state = str(c.get("State", "")).lower()
        status = str(c.get("Status", ""))
        state = _STATE.get(docker_state, State.UNKNOWN)
        if state is State.UP and "(unhealthy)" in status:
            state = State.DEGRADED
        labels: dict[str, Any] = c.get("Labels") or {}
        attrs: dict[str, Any] = {
            "image": c.get("Image"),
            "status": status,
            "docker_state": docker_state,
            "id": str(c.get("Id", ""))[:12],
            "created": datetime.fromtimestamp(int(c["Created"]), UTC).isoformat()
            if c.get("Created")
            else None,
            "ports": _ports(c.get("Ports") or []),
        }
        if "com.docker.compose.project" in labels:
            attrs["compose_project"] = labels["com.docker.compose.project"]
            attrs["compose_service"] = labels.get("com.docker.compose.service")
        return Resource(
            uid=make_uid(self.name, KIND, name),
            provider=self.name,
            kind=KIND,
            name=name,
            state=state,
            attrs={k: v for k, v in attrs.items() if v is not None},
            fetched_at=now,
        )

    async def _stats(self, r: Resource) -> list[Metric]:
        path = f"/containers/{r.name}/stats"
        try:
            # one-shot: answer immediately instead of holding the request ~1s to sample CPU
            # twice. Found live: 103 two-sample requests made the pass overrun 75s about
            # every other time and slowed the engine enough to time out the listing too.
            s = await self._get(path, stats_pool=True, stream="false", **{"one-shot": "true"})
        except ProviderPollError as exc:
            # A container that stopped since listing is not a provider failure.
            self.log.debug("%s", exc.message)
            return []
        ts = datetime.now(UTC)
        out: list[Metric] = []

        def add(name: str, value: float, unit: SourceUnit) -> None:
            m = Metric.from_source(
                resource_uid=r.uid, name=name, value=value, source_unit=unit, ts=ts
            )
            out.append(m)

        with contextlib.suppress(KeyError, TypeError, ZeroDivisionError):
            cpu = s["cpu_stats"]
            total, system = int(cpu["cpu_usage"]["total_usage"]), int(cpu["system_cpu_usage"])
            # One-shot leaves precpu_stats zeroed, so the delta is taken against this
            # container's previous sample, as network rates already are. An engine that
            # ignored one-shot sends its own ~1s earlier sample; prefer that when present.
            pre = s.get("precpu_stats") or {}
            pre_system = int(pre.get("system_cpu_usage") or 0)
            if pre_system > 0:
                base: tuple[int, int] | None = (int(pre["cpu_usage"]["total_usage"]), pre_system)
            else:
                base = self._cpu_prev.get(r.uid)
            self._cpu_prev[r.uid] = (total, system)
            if base is not None:
                cpu_delta, sys_delta = total - base[0], system - base[1]
                cpus = (
                    cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
                )
                if sys_delta > 0 and cpu_delta >= 0:
                    add("cpu_pct", cpu_delta / sys_delta * cpus * 100.0, SourceUnit.PCT)
        with contextlib.suppress(KeyError, TypeError, ZeroDivisionError):
            mem = s["memory_stats"]
            inner = mem.get("stats") or {}
            # cgroup v2 reports inactive_file, v1 reports cache; docker CLI subtracts either.
            used = mem["usage"] - inner.get("inactive_file", inner.get("cache", 0))
            add("mem_bytes", float(used), SourceUnit.BYTES)
            limit = mem.get("limit")
            if limit:
                add("mem_pct", used / limit * 100.0, SourceUnit.PCT)
        nets = s.get("networks") or {}
        if isinstance(nets, dict) and nets:
            rx = sum(int(n.get("rx_bytes", 0)) for n in nets.values())
            tx = sum(int(n.get("tx_bytes", 0)) for n in nets.values())
            now = time.monotonic()
            prev = self._net_prev.get(r.uid)
            self._net_prev[r.uid] = (now, rx, tx)
            if prev is not None:
                dt = now - prev[0]
                if dt > 0 and rx >= prev[1] and tx >= prev[2]:
                    add("rx_bps", (rx - prev[1]) / dt, SourceUnit.BYTES_PER_SEC)
                    add("tx_bps", (tx - prev[2]) / dt, SourceUnit.BYTES_PER_SEC)
        return out


def _ports(ports: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for p in ports:
        private = p.get("PrivatePort")
        public = p.get("PublicPort")
        proto = p.get("Type", "tcp")
        text = f"{public}->{private}/{proto}" if public else f"{private}/{proto}"
        if text not in seen:
            seen.append(text)
    return seen
