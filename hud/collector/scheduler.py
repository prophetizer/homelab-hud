# SPDX-License-Identifier: Apache-2.0
"""Collector / Scheduler (PLAN.md §4.1, §7.3): one APScheduler job per poll group.

Each job runs the group under a hard timeout, pushes the result through ``normalize`` into
the :class:`LiveCache`, then hands it to every registered sink (the store writer). A
failure marks the group's resources stale and feeds the group's circuit breaker; an open
circuit pushes the job's next run out to the breaker's open-until time. Jitter is applied
to every fire so forty providers never stampede on the minute boundary.

Per-provider health is derived on demand from the per-group run records; nothing here is
persisted.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from hud.collector.breaker import CircuitBreaker
from hud.collector.cache import LiveCache
from hud.collector.normalize import Normalized, normalize
from hud.models import Event
from hud.providers.base import (
    GroupTiming,
    PollGroup,
    Provider,
    ProviderHealth,
    ProviderStatus,
    ProviderTier,
)
from hud.providers.errors import ProviderPollError
from hud.providers.registry import ProviderRegistry, RegistryDiff

log = logging.getLogger(__name__)

_ROUTINE_SKIP = "maximum number of running instances"


class SchedulerLog(logging.LoggerAdapter[logging.Logger]):
    """APScheduler's logger with one routine message demoted to DEBUG.

    A poll group that runs longer than its interval — a full docker stats pass over a
    hundred containers — makes the next tick skip, by design (``max_instances: 1``,
    ``coalesce``). APScheduler logs that at WARNING on every tick, which buries the
    warnings that do matter (timeouts, breaker trips) under routine ones. Found on the
    first live deployment; everything else APScheduler logs keeps its level.
    """

    def warning(self, msg: object, *args: object, **kwargs: Any) -> None:  # noqa: ANN401
        if isinstance(msg, str) and _ROUTINE_SKIP in msg:
            self.debug(msg, *args, **kwargs)
            return
        super().warning(msg, *args, **kwargs)


# Called after every successful poll with the normalized result and generated events.
ResultSink = Callable[[str, str, Normalized, list[Event]], Awaitable[None]]


@dataclass
class _GroupRun:
    group: PollGroup
    breaker: CircuitBreaker
    last_poll: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    last_seconds: float | None = None  # how long the last run took, success or not
    running: bool = False


@dataclass
class _ProviderRuns:
    provider: Provider
    groups: dict[str, _GroupRun] = field(default_factory=dict)


class Collector:
    def __init__(
        self,
        registry: ProviderRegistry,
        cache: LiveCache,
        *,
        sinks: list[ResultSink] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.cache = cache
        self.sinks: list[ResultSink] = list(sinks or [])
        self._clock = clock
        self._runs: dict[str, _ProviderRuns] = {}
        self._scheduler = AsyncIOScheduler(
            timezone=UTC,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": None},
            logger=SchedulerLog(logging.getLogger("apscheduler.scheduler")),
        )
        registry.on_diff(self._on_diff)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._scheduler.start()
        for name, provider in self.registry.providers.items():
            self._install(name, provider)

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._runs.clear()

    async def _on_diff(self, diff: RegistryDiff) -> None:
        for name in diff.removed:
            self._uninstall(name)
            self.cache.remove_provider(name)
        for name in diff.replaced:
            self._uninstall(name)
        for name in (*diff.added, *diff.replaced):
            provider = self.registry.get(name)
            if provider is not None:
                self._install(name, provider)
        for name in diff.failed:
            self._uninstall(name)

    def _install(self, name: str, provider: Provider) -> None:
        self.cache.register_provider(name, provider.labels)
        runs = _ProviderRuns(provider)
        for g in provider.groups():
            runs.groups[g.name] = _GroupRun(g, CircuitBreaker(base_delay=g.interval))
            if not self._scheduler.running:
                continue
            first = datetime.now(UTC) + timedelta(seconds=random.uniform(0, g.jitter))  # noqa: S311 — scheduling jitter, not security
            self._scheduler.add_job(
                self.run_group,
                IntervalTrigger(seconds=g.interval, jitter=g.jitter or None, timezone=UTC),
                args=(name, g.name),
                id=_job_id(name, g.name),
                name=f"{name}/{g.name}",
                next_run_time=first,
                replace_existing=True,
            )
        self._runs[name] = runs

    def _uninstall(self, name: str) -> None:
        runs = self._runs.pop(name, None)
        if runs is None:
            return
        for group in runs.groups:
            job = self._scheduler.get_job(_job_id(name, group))
            if job is not None:
                job.remove()

    # ------------------------------------------------------------------ polling

    async def run_group(self, provider_name: str, group_name: str) -> bool:
        """Poll one group now. Returns True on success. Safe to call from a job or an
        API handler; concurrent calls for the same group coalesce into a skip."""
        runs = self._runs.get(provider_name)
        if runs is None or group_name not in runs.groups:
            return False
        run = runs.groups[group_name]
        now = self._clock()
        if run.running or run.breaker.is_open(now):
            return False
        run.running = True
        run.last_poll = datetime.now(UTC)
        provider = runs.provider
        started = self._clock()
        try:
            try:
                result = await asyncio.wait_for(
                    provider.poll(group_name), timeout=run.group.timeout
                )
            finally:
                run.last_seconds = self._clock() - started
        except TimeoutError:
            self._failed(provider_name, run, f"timed out after {run.group.timeout:g}s")
            return False
        except ProviderPollError as exc:
            self._failed(provider_name, run, exc.message)
            return False
        except asyncio.CancelledError:
            run.running = False
            raise
        except Exception as exc:
            log.exception("%s/%s: poll raised", provider_name, group_name)
            self._failed(provider_name, run, f"{type(exc).__name__}: {exc}")
            return False
        else:
            normalized = normalize(provider_name, result)
            events = self.cache.apply(
                provider_name,
                group_name,
                normalized.resources,
                normalized.metrics,
                normalized.events,
            )
            for e in events:
                if e.type == "state_change":
                    log.info("%s: %s", e.resource_uid, e.message)
            for sink in self.sinks:
                try:
                    await sink(provider_name, group_name, normalized, events)
                except Exception:
                    log.exception("%s/%s: result sink %r failed", provider_name, group_name, sink)
            run.breaker.record_success()
            run.last_success = run.last_poll
            run.last_error = None
            run.running = False
            return True

    def _failed(self, provider_name: str, run: _GroupRun, error: str) -> None:
        run.running = False
        run.last_error = error
        self.cache.mark_stale(provider_name, run.group.name, error)
        opened_until = run.breaker.record_failure(self._clock())
        if opened_until is None:
            log.warning(
                "%s/%s: poll failed (%d/%d): %s",
                provider_name,
                run.group.name,
                run.breaker.consecutive_failures,
                run.breaker.threshold,
                error,
            )
            return
        wait = run.breaker.seconds_remaining(self._clock())
        log.error(
            "%s/%s: circuit open for %.0fs after %d consecutive failures: %s",
            provider_name,
            run.group.name,
            wait,
            run.breaker.consecutive_failures,
            error,
        )
        job = self._scheduler.get_job(_job_id(provider_name, run.group.name))
        if job is not None:
            job.modify(next_run_time=datetime.now(UTC) + timedelta(seconds=wait))

    async def poll_provider(self, name: str) -> dict[str, bool]:
        """Reset breakers and poll every group of one provider now (reload endpoint)."""
        runs = self._runs.get(name)
        if runs is None:
            return {}
        for run in runs.groups.values():
            run.breaker.record_success()
        return {g: await self.run_group(name, g) for g in list(runs.groups)}

    def groups_of(self, name: str) -> list[str]:
        runs = self._runs.get(name)
        return list(runs.groups) if runs else []

    # ------------------------------------------------------------------ health

    def health(self) -> list[ProviderHealth]:
        out: list[ProviderHealth] = []
        for name, runs in sorted(self._runs.items()):
            out.append(self._provider_health(name, runs))
        out.extend(_failed_health(n, e) for n, e in sorted(self.registry.failures.items()))
        return sorted(out, key=lambda h: h.name)

    def provider_health(self, name: str) -> ProviderHealth | None:
        runs = self._runs.get(name)
        if runs is not None:
            return self._provider_health(name, runs)
        error = self.registry.failures.get(name)
        return _failed_health(name, error) if error is not None else None

    def _provider_health(self, name: str, runs: _ProviderRuns) -> ProviderHealth:
        groups = list(runs.groups.values())
        now = self._clock()
        polled = [g for g in groups if g.last_poll is not None]
        failing = [g for g in groups if g.last_error is not None]
        open_until: datetime | None = None
        for g in groups:
            if g.breaker.is_open(now):
                until = datetime.now(UTC) + timedelta(seconds=g.breaker.seconds_remaining(now))
                open_until = max(open_until, until) if open_until else until
        if not polled:
            status = ProviderStatus.STARTING
        elif failing:
            status = ProviderStatus.DEGRADED
        else:
            status = ProviderStatus.OK
        return ProviderHealth(
            name=name,
            tier=runs.provider.tier,
            status=status,
            labels=runs.provider.labels,
            groups=[g.group.name for g in groups],
            last_poll=_latest(g.last_poll for g in groups),
            last_success=_latest(g.last_success for g in groups),
            last_error=failing[0].last_error if failing else None,
            consecutive_failures=max((g.breaker.consecutive_failures for g in groups), default=0),
            circuit_open_until=open_until,
            resource_count=self.cache.provider_resource_count(name),
            timings=sorted(
                (
                    GroupTiming(
                        name=g.group.name,
                        seconds=g.last_seconds,
                        timeout=g.group.timeout,
                        ok=g.last_error is None,
                    )
                    for g in groups
                    if g.last_seconds is not None
                ),
                key=lambda t: t.seconds,
                reverse=True,
            ),
        )


def _latest(times: Iterable[datetime | None]) -> datetime | None:
    known = [t for t in times if t is not None]
    return max(known) if known else None


def _failed_health(name: str, error: str) -> ProviderHealth:
    # Tier is unknown for a provider that never built; declarative is the common case.
    return ProviderHealth(
        name=name, tier=ProviderTier.DECLARATIVE, status=ProviderStatus.ERROR, last_error=error
    )


def _job_id(provider: str, group: str) -> str:
    return f"{provider}/{group}"
