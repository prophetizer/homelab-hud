# SPDX-License-Identifier: Apache-2.0
"""Collector: timeout, breaker, stale marking, sinks, health derivation, real scheduling."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hud.collector import LiveCache, Normalized
from hud.collector.breaker import CircuitBreaker
from hud.collector.scheduler import Collector
from hud.config import ConfigManager, SecretResolver
from hud.config.schemas import ProviderDocument
from hud.models import Event, Metric, Resource, Severity, State, Unit
from hud.providers import (
    PollGroup,
    PollResult,
    Provider,
    ProviderContext,
    ProviderPollError,
    ProviderRegistry,
    ProviderStatus,
    ProviderTier,
)
from tests.test_providers_registry import provider_yaml

# ---------------------------------------------------------------- breaker


def test_breaker_opens_after_threshold_and_backs_off_exponentially() -> None:
    b = CircuitBreaker(base_delay=30)
    assert b.record_failure(0) is None
    assert b.record_failure(0) is None
    assert b.record_failure(100) == 130  # 3rd failure: open for 1x interval
    assert b.is_open(129) and not b.is_open(130)
    assert b.record_failure(200) == 260  # 2x interval
    assert b.record_failure(300) == 420  # 4x
    for _ in range(5):
        b.record_failure(1000)
    assert b.open_until == 1300  # ceiling 300s
    b.record_success()
    assert b.consecutive_failures == 0 and b.opened_count == 0 and not b.is_open(0)
    assert b.record_failure(0) is None


def test_breaker_short_intervals_back_off_from_one_second() -> None:
    b = CircuitBreaker(base_delay=0.1)
    for _ in range(3):
        opened = b.record_failure(0)
    assert opened == 1.0


# ---------------------------------------------------------------- fake provider


class Script(Provider):
    """Poll behaviour is a queue of outcomes: a PollResult, an exception, or 'hang'."""

    tier = ProviderTier.PLUGIN

    def __init__(self, ctx: ProviderContext, interval: float = 30, timeout: float = 0.2) -> None:
        super().__init__(ctx, {"category": "test"})
        self.outcomes: list[object] = []
        self.polls = 0
        self._group = PollGroup("main", interval=interval, jitter=0, timeout=timeout)

    def groups(self) -> Sequence[PollGroup]:
        return [self._group]

    async def poll(self, group: str) -> PollResult:
        self.polls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else PollResult()
        if outcome == "hang":
            await asyncio.sleep(10)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, PollResult)
        return outcome


def result(*states: State, provider: str = "p") -> PollResult:
    now = datetime.now(UTC)
    resources = [
        Resource(
            uid=f"{provider}:thing:r{i}",
            provider=provider,
            kind="thing",
            name=f"r{i}",
            state=s,
            fetched_at=now,
        )
        for i, s in enumerate(states)
    ]
    metrics = [
        Metric(resource_uid=r.uid, name="v", value=float(i), unit=Unit.COUNT, ts=now)
        for i, r in enumerate(resources)
    ]
    return PollResult(resources=resources, metrics=metrics)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


Harness = tuple[Collector, ProviderRegistry, LiveCache, Clock, ConfigManager]


@pytest.fixture
def harness(config_dir: Path) -> Harness:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir()
    (config_dir / "providers" / "p.yaml").write_text(provider_yaml("p"))
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env={})

    def factory(doc: ProviderDocument, ctx: ProviderContext) -> Provider:
        return Script(ctx)

    registry = ProviderRegistry(
        factory=factory, context_factory=lambda n: ProviderContext.create(n, secrets, {})
    )
    cache = LiveCache()
    clock = Clock()
    collector = Collector(registry, cache, clock=clock)
    return collector, registry, cache, clock, ConfigManager(config_dir)


async def test_success_path_fills_cache_and_calls_sinks(
    harness: Harness,
) -> None:
    collector, registry, cache, _, manager = harness
    seen: list[tuple[str, str, int, int]] = []

    async def sink(provider: str, group: str, n: Normalized, events: list[Event]) -> None:
        seen.append((provider, group, len(n.resources), len(events)))

    collector.sinks.append(sink)
    await registry.apply(manager.load())
    p = registry.get("p")
    assert isinstance(p, Script)
    p.outcomes = [result(State.UP, State.UP), result(State.UP, State.DOWN)]
    assert await collector.run_group("p", "main") is True
    assert len(cache) == 2 and cache.metric("p:thing:r1", "v") is not None
    assert await collector.run_group("p", "main") is True
    assert seen == [("p", "main", 2, 0), ("p", "main", 2, 1)]
    (h,) = collector.health()
    assert h.status is ProviderStatus.OK
    assert h.resource_count == 2 and h.groups == ["main"] and h.labels == {"category": "test"}
    assert h.last_success == h.last_poll and h.last_error is None
    assert h.tier is ProviderTier.PLUGIN


async def test_failures_mark_stale_open_circuit_and_recover(
    harness: Harness,
) -> None:
    collector, registry, cache, clock, manager = harness
    await registry.apply(manager.load())
    p = registry.get("p")
    assert isinstance(p, Script)
    p.outcomes = [result(State.UP)]
    await collector.run_group("p", "main")
    p.outcomes = [
        ProviderPollError("p", "HTTP 500"),
        RuntimeError("bug"),
        "hang",  # times out at 0.2s
    ]
    assert await collector.run_group("p", "main") is False
    r = cache.resource("p:thing:r0")
    assert r is not None and r.stale and r.state is State.UP  # last-known-good kept
    h = collector.provider_health("p")
    assert h is not None and h.status is ProviderStatus.DEGRADED
    assert h.last_error == "HTTP 500" and h.consecutive_failures == 1
    assert await collector.run_group("p", "main") is False
    assert await collector.run_group("p", "main") is False
    h = collector.provider_health("p")
    assert h is not None
    assert h.last_error == "timed out after 0.2s"
    assert h.consecutive_failures == 3
    assert h.circuit_open_until is not None
    # Open circuit: the group is skipped without touching the provider.
    polls = p.polls
    assert await collector.run_group("p", "main") is False
    assert p.polls == polls
    # Time passes (1x interval = 30s), circuit half-opens, a success closes it.
    clock.t += 31
    p.outcomes = [result(State.UP)]
    assert await collector.run_group("p", "main") is True
    r = cache.resource("p:thing:r0")
    assert r is not None and not r.stale
    h = collector.provider_health("p")
    assert h is not None and h.status is ProviderStatus.OK and h.circuit_open_until is None


async def test_poll_provider_resets_breaker(
    harness: Harness,
) -> None:
    collector, registry, _, _, manager = harness
    await registry.apply(manager.load())
    p = registry.get("p")
    assert isinstance(p, Script)
    p.outcomes = [ProviderPollError("p", "x")] * 3
    for _ in range(3):
        await collector.run_group("p", "main")
    assert await collector.run_group("p", "main") is False  # open
    p.outcomes = [result(State.UP)]
    assert await collector.poll_provider("p") == {"main": True}
    assert await collector.poll_provider("nope") == {}


async def test_concurrent_runs_of_one_group_coalesce(
    harness: Harness,
) -> None:
    collector, registry, _, _, manager = harness
    await registry.apply(manager.load())
    p = registry.get("p")
    assert isinstance(p, Script)
    p.outcomes = ["hang"]
    first = asyncio.create_task(collector.run_group("p", "main"))
    await asyncio.sleep(0.01)
    assert await collector.run_group("p", "main") is False  # already running
    assert await first is False  # the hang timed out
    assert p.polls == 1


async def test_registry_diff_installs_and_removes(
    harness: Harness,
) -> None:
    collector, registry, cache, _, manager = harness
    await registry.apply(manager.load())
    p = registry.get("p")
    assert isinstance(p, Script)
    p.outcomes = [result(State.UP)]
    await collector.run_group("p", "main")
    assert len(cache) == 1
    (manager.config_dir / "providers" / "p.yaml").unlink()
    await registry.apply(manager.load())
    assert collector.health() == []
    assert len(cache) == 0
    assert await collector.run_group("p", "main") is False


async def test_health_reports_build_failures(
    harness: Harness,
) -> None:
    collector, registry, _, _, manager = harness
    registry.factory = lambda doc, ctx: (_ for _ in ()).throw(RuntimeError("no plugin"))
    await registry.apply(manager.load())
    (h,) = collector.health()
    assert h.status is ProviderStatus.ERROR and h.last_error == "RuntimeError: no plugin"
    assert collector.provider_health("p") == h
    assert collector.provider_health("nope") is None


async def test_starting_status_before_first_poll(
    harness: Harness,
) -> None:
    collector, registry, _, _, manager = harness
    await registry.apply(manager.load())
    (h,) = collector.health()
    assert h.status is ProviderStatus.STARTING and h.last_poll is None


async def test_real_scheduler_polls_on_interval(config_dir: Path) -> None:
    (config_dir / "settings.yaml").write_text("apiVersion: hud/v1\nkind: Settings\n")
    (config_dir / "providers").mkdir()
    (config_dir / "providers" / "p.yaml").write_text(provider_yaml("p"))
    secrets = SecretResolver(config_dir, secrets_dir=config_dir / "none", env={})
    made: list[Script] = []

    def factory(doc: ProviderDocument, ctx: ProviderContext) -> Provider:
        s = Script(ctx, interval=0.05)
        s.outcomes = [result(State.UP), result(State.DOWN)]
        made.append(s)
        return s

    registry = ProviderRegistry(
        factory=factory, context_factory=lambda n: ProviderContext.create(n, secrets, {})
    )
    cache = LiveCache()
    collector = Collector(registry, cache)
    await registry.apply(ConfigManager(config_dir).load())
    collector.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            if made and made[0].polls >= 2:
                break
        assert made[0].polls >= 2
        r = cache.resource("p:thing:r0")
        assert r is not None and r.state is State.DOWN
        (e,) = cache.events(limit=1)
        assert e.type == "state_change" and e.severity is Severity.ERROR
    finally:
        await collector.stop()
        await registry.shutdown()


def test_routine_skip_is_debug_but_other_scheduler_warnings_stay_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Found live: a ~90s docker stats pass against a 30s interval skips ticks by design,
    and APScheduler logged each skip at WARNING — burying breaker trips and timeouts."""
    import logging  # noqa: PLC0415

    from hud.collector.scheduler import SchedulerLog  # noqa: PLC0415

    adapter = SchedulerLog(logging.getLogger("apscheduler.scheduler"))
    with caplog.at_level(logging.DEBUG, logger="apscheduler.scheduler"):
        # The exact message APScheduler 3.11 emits (schedulers/base.py).
        adapter.warning(
            'Execution of job "%s" skipped: maximum number of running instances reached (%d)',
            "docker/collect",
            1,
        )
        adapter.warning('Run time of job "%s" was missed by %s', "docker/collect", "0:00:05")
    levels = {r.getMessage().split(" ")[0]: r.levelname for r in caplog.records}
    assert levels == {"Execution": "DEBUG", "Run": "WARNING"}


def test_collector_hands_apscheduler_the_demoting_logger() -> None:
    from hud.collector import LiveCache  # noqa: PLC0415
    from hud.collector.scheduler import Collector, SchedulerLog  # noqa: PLC0415
    from hud.providers import ProviderRegistry  # noqa: PLC0415

    registry = ProviderRegistry(factory=lambda *_: None, context_factory=lambda _: None)  # type: ignore[arg-type,return-value]
    collector = Collector(registry, LiveCache())
    assert isinstance(collector._scheduler._logger, SchedulerLog)


async def test_health_reports_how_long_each_group_took(harness: Harness) -> None:
    """The System page's poll-time bar: slow is visible before it trips the breaker."""
    collector, registry, _, clock, manager = harness
    await registry.apply(manager.load())
    collector.start()
    try:
        provider = registry.get("p")
        assert isinstance(provider, Script)
        real_poll = provider.poll

        async def slow_poll(group: str) -> PollResult:
            clock.t += 0.15  # the injected clock: "took 150 ms"
            return await real_poll(group)

        provider.poll = slow_poll  # type: ignore[method-assign]
        assert await collector.run_group("p", "main") is True
        health = collector.provider_health("p")
        assert health is not None
        (t,) = health.timings
        assert (t.name, round(t.seconds, 3), t.timeout, t.ok) == ("main", 0.15, 0.2, True)
    finally:
        await collector.stop()
